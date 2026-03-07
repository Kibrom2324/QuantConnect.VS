"""
APEX Walk-Forward Trainer — services/model_training/walk_forward.py

Fixes implemented in this file
───────────────────────────────
  CF-1   Best-fold selection: always return the LAST fold (most recent
         in-sample window), not the fold with the highest Sharpe.
         Selecting by Sharpe creates look-ahead bias because the in-sample
         Sharpe is not a reliable predictor of out-of-sample performance.
         The most-recent fold captures the latest market regime.

  CF-2   Embargo gap between in-sample end and out-of-sample start.
         embargo_bars = 180 (≈ half a trading year of daily bars).
         Prevents leakage of features that have multi-month memory
         (e.g. rolling 252-day volatility, long-look Kalman states).

MLflow integration
──────────────────
  Per fold: mlflow.start_run(run_name="fold_NN") inside try/finally.
    - Params: embargo_bars, n_folds, fold_index, is_bars, oos_bars
    - Metrics: oos_sharpe, oos_sortino, oos_calmar, train_loss, val_loss
    - Artifacts: normalization JSON sidecar, ONNX model (if present)
  Best fold (always folds[-1]): mlflow.set_tag("production", "true")
  Set env var MLFLOW_TRACKING_URI to point at your server (default: file:./mlruns).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

import numpy as np
import pandas as pd
import structlog

# ─── Optional MLflow — graceful if not installed ──────────────────────────────
try:
    import mlflow as _mlflow
    _MLFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover
    _mlflow = None   # type: ignore[assignment]
    _MLFLOW_AVAILABLE = False

MLFLOW_EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT_NAME", "apex-walk-forward")

logger = structlog.get_logger(__name__)


# ─── MLflow helpers ───────────────────────────────────────────────────────────

@contextmanager
def _mlflow_fold_run(
    fold_index: int,
    n_folds: int,
    embargo_bars: int,
    is_bars: int,
    oos_bars: int,
) -> Generator[Any, None, None]:
    """
    Context manager: open one MLflow run per fold, log params, yield, log
    metrics + artifacts, set the production tag on the final fold, close.

    If MLflow is not installed or MLFLOW_TRACKING_URI is unset the function
    is a no-op and yields None so the caller can skip optional log calls.
    """
    if not _MLFLOW_AVAILABLE:
        yield None
        return

    try:
        _mlflow.set_experiment(
            os.environ.get("MLFLOW_EXPERIMENT_NAME", MLFLOW_EXPERIMENT)
        )
        with _mlflow.start_run(run_name=f"fold_{fold_index:02d}") as run:
            _mlflow.log_params({
                "fold_index":    fold_index,
                "n_folds":       n_folds,
                "embargo_bars":  embargo_bars,
                "is_bars":       is_bars,
                "oos_bars":      oos_bars,
            })
            yield run
    except Exception as exc:   # pragma: no cover — MLflow server offline
        logger.warning("mlflow_fold_run_failed", fold=fold_index, error=str(exc))
        yield None


def _mlflow_log_fold_metrics(
    run: Any,
    oos_sharpe: float,
    oos_sortino: float = 0.0,
    oos_calmar: float = 0.0,
    train_loss: float = 0.0,
    val_loss: float = 0.0,
) -> None:
    """Log per-fold OOS metrics.  No-op if run is None."""
    if run is None or not _MLFLOW_AVAILABLE:
        return
    try:
        _mlflow.log_metrics({
            "oos_sharpe":   oos_sharpe,
            "oos_sortino":  oos_sortino,
            "oos_calmar":   oos_calmar,
            "train_loss":   train_loss,
            "val_loss":     val_loss,
        })
    except Exception as exc:   # pragma: no cover
        logger.warning("mlflow_log_metrics_failed", error=str(exc))


def _mlflow_log_artifacts(run: Any, sidecar_path: Path | None, model_path: Path | None) -> None:
    """Log JSON sidecar and ONNX model artifacts.  No-op if run is None."""
    if run is None or not _MLFLOW_AVAILABLE:
        return
    for path in (sidecar_path, model_path):
        if path is not None and Path(path).exists():
            try:
                _mlflow.log_artifact(str(path))
            except Exception as exc:   # pragma: no cover
                logger.warning("mlflow_log_artifact_failed", path=str(path), error=str(exc))


def _mlflow_set_production_tag(run_id: str | None) -> None:
    """Mark the selected (best = last) fold run as production=true."""
    if run_id is None or not _MLFLOW_AVAILABLE:
        return
    try:
        with _mlflow.start_run(run_id=run_id):
            _mlflow.set_tag("production", "true")
        logger.info("mlflow_production_tag_set", run_id=run_id)
    except Exception as exc:   # pragma: no cover
        logger.warning("mlflow_production_tag_failed", error=str(exc))




# CF-2 FIX 2026-02-27: embargo is 180 bars (daily).  Previous value was 21
# (one month), too short for features with 252-bar look-back windows.
EMBARGO_BARS: int = 180


@dataclass
class Fold:
    fold_index:    int
    is_start:      int   # integer iloc index into the full DataFrame
    is_end:        int
    oos_start:     int
    oos_end:       int
    sharpe:        float = 0.0
    model:         Any   = field(default=None, repr=False)
    metadata:      dict  = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """Container for the chosen model and accompanying diagnostics."""
    best_fold:   Fold
    all_folds:   list[Fold]
    chosen_by:   str     # always "most_recent_fold" after CF-1 fix

    @property
    def model(self) -> Any:
        return self.best_fold.model


def build_folds(
    n_rows:      int,
    n_folds:     int = 5,
    is_pct:      float = 0.70,
    embargo_bars: int = EMBARGO_BARS,
) -> list[tuple[int, int, int, int]]:
    """
    Return a list of (is_start, is_end, oos_start, oos_end) index tuples.

    Each fold shifts forward by oos_window bars.  The in-sample window is
    anchored to the beginning of the dataset (expanding window design) or
    can be made rolling (set is_pct per fold — not done here for simplicity).

    CF-2 FIX: oos_start = is_end + embargo_bars  (not is_end + 1)
    """
    folds = []
    oos_window = int(n_rows * (1 - is_pct) / n_folds)
    if oos_window < 1:
        raise ValueError(
            f"Too few rows ({n_rows}) for {n_folds} folds with {is_pct:.0%} IS split."
        )

    for i in range(n_folds):
        oos_end_  = n_rows - (n_folds - 1 - i) * oos_window
        oos_start_ = oos_end_ - oos_window

        # CF-2 FIX: IS ends embargo_bars before OOS starts
        is_end_   = oos_start_ - embargo_bars
        is_start_ = 0  # expanding window — IS always starts at the beginning

        if is_end_ <= is_start_:
            logger.warning(
                "fold_skipped_insufficient_is_bars",
                fold=i,
                is_end=is_end_,
                embargo_bars=embargo_bars,
            )
            continue

        folds.append((is_start_, is_end_, oos_start_, oos_end_))

    if not folds:
        raise ValueError(
            f"No valid folds generated.  "
            f"n_rows={n_rows} is too small for "
            f"n_folds={n_folds} with embargo={embargo_bars} bars."
        )
    return folds


def select_best_fold(folds: list[Fold]) -> Fold:
    """
    CF-1 FIX 2026-02-27: Return the LAST fold (most recent market regime).

    Why not max-Sharpe?
    ───────────────────
    Selecting the fold with the highest in-sample Sharpe is a form of
    look-ahead bias.  The in-sample Sharpe is calculated on the training
    set, so picking the 'best' Sharpe cherry-picks whichever fold happened
    to coincide with a favorable period — there is no guarantee it will
    generalise to the next OOS window.

    The last fold's in-sample data is the most recent and therefore the
    most representative of the current market regime.
    """
    if not folds:
        raise ValueError("select_best_fold received an empty fold list")

    # CF-1 FIX: most-recent fold = folds[-1]
    chosen = folds[-1]
    logger.info(
        "best_fold_selected",
        method="most_recent_fold",         # CF-1 FIX label
        fold_index=chosen.fold_index,
        is_range=(chosen.is_start, chosen.is_end),
        oos_range=(chosen.oos_start, chosen.oos_end),
        sharpe=chosen.sharpe,
    )
    return chosen


class WalkForwardTrainer:
    """
    Orchestrates walk-forward cross-validation.

    Usage
    ─────
    trainer = WalkForwardTrainer(model_factory=lambda: MyModel())
    result  = trainer.fit(features_df, labels_series)
    final   = result.model  # last-fold model, ready for inference
    """

    def __init__(
        self,
        model_factory,               # callable → fresh model with .fit() / .predict()
        n_folds:     int   = 5,
        is_pct:      float = 0.70,
        embargo_bars: int  = EMBARGO_BARS,
        metric_fn    = None,         # callable(y_true, y_pred) → float (higher = better)
    ) -> None:
        self._factory      = model_factory
        self._n_folds      = n_folds
        self._is_pct       = is_pct
        self._embargo_bars = embargo_bars
        self._metric_fn    = metric_fn or self._default_sharpe

    @staticmethod
    def _default_sharpe(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """Simple directional Sharpe: sign(pred) × actual_return."""
        strategy_returns = np.sign(y_pred) * y_true
        std = strategy_returns.std()
        if std == 0:
            return 0.0
        return float(strategy_returns.mean() / std * np.sqrt(252))

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> WalkForwardResult:
        """
        Train one model per fold, evaluate on OOS, return the last-fold
        model.

        CF-1 FIX: best fold = folds[-1], not argmax(sharpe)
        CF-2 FIX: OOS starts embargo_bars after IS end
        MLflow: one run per fold; production tag on folds[-1]
        """
        n = len(X)
        fold_specs = build_folds(
            n_rows       = n,
            n_folds      = self._n_folds,
            is_pct       = self._is_pct,
            embargo_bars = self._embargo_bars,
        )

        completed_folds: list[Fold] = []

        for i, (is_start, is_end, oos_start, oos_end) in enumerate(fold_specs):
            is_bars  = is_end - is_start
            oos_bars = oos_end - oos_start

            logger.info(
                "training_fold",
                fold=i,
                is_bars=is_bars,
                embargo_gap=oos_start - is_end,
                oos_bars=oos_bars,
            )

            with _mlflow_fold_run(
                fold_index   = i,
                n_folds      = self._n_folds,
                embargo_bars = self._embargo_bars,
                is_bars      = is_bars,
                oos_bars     = oos_bars,
            ) as mlflow_run:

                X_is   = X.iloc[is_start:is_end]
                y_is   = y.iloc[is_start:is_end]
                X_oos  = X.iloc[oos_start:oos_end]
                y_oos  = y.iloc[oos_start:oos_end]

                model = self._factory()
                model.fit(X_is, y_is)

                y_pred = model.predict(X_oos)
                metric = self._metric_fn(y_oos.to_numpy(), np.asarray(y_pred))

                # ── optional extra metrics from model.metadata ─────────────────
                train_loss = 0.0
                val_loss   = 0.0
                if hasattr(model, "metadata") and isinstance(model.metadata, dict):
                    train_loss = float(model.metadata.get("train_loss", 0.0))
                    val_loss   = float(model.metadata.get("val_loss",   0.0))

                # ── log metrics + artifacts to MLflow ──────────────────────────
                _mlflow_log_fold_metrics(
                    mlflow_run,
                    oos_sharpe  = metric,
                    train_loss  = train_loss,
                    val_loss    = val_loss,
                )

                # look for sidecar / ONNX artefacts written by dataset.py or model
                sidecar_path = None
                model_path   = None
                if hasattr(model, "sidecar_path"):
                    sidecar_path = Path(model.sidecar_path)
                onnx_candidate = Path(f"configs/models/fold_{i:02d}.onnx")
                if onnx_candidate.exists():
                    model_path = onnx_candidate

                _mlflow_log_artifacts(mlflow_run, sidecar_path, model_path)

                # store run_id so we can tag the production run after selection
                run_id = mlflow_run.info.run_id if mlflow_run is not None else None

                fold = Fold(
                    fold_index = i,
                    is_start   = is_start,
                    is_end     = is_end,
                    oos_start  = oos_start,
                    oos_end    = oos_end,
                    sharpe     = metric,
                    model      = model,
                    metadata   = {
                        "is_bars":    is_bars,
                        "embargo":    oos_start - is_end,  # always EMBARGO_BARS
                        "oos_bars":   oos_bars,
                        "oos_metric": metric,
                        "mlflow_run_id": run_id,
                    },
                )
            completed_folds.append(fold)

        # CF-1 FIX applied here — always returns last fold
        best = select_best_fold(completed_folds)

        # MLflow: mark the chosen fold's run as production=true
        best_run_id = best.metadata.get("mlflow_run_id")
        _mlflow_set_production_tag(best_run_id)

        return WalkForwardResult(
            best_fold = best,
            all_folds = completed_folds,
            chosen_by = "most_recent_fold",   # CF-1 FIX identifier
        )
