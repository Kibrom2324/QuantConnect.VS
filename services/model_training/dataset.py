"""
APEX Feature Dataset — services/model_training/dataset.py

Fixes implemented in this file
───────────────────────────────
  CF-4   Normalisation statistics leakage:
         Previously the scaler was fitted on the entire dataset before
         splitting into folds, so OOS rows informed the normalisation of IS
         rows (data leakage).

         Fix: fit the scaler on the TRAINING (IS) slice only.
              Persist the scaler params to a JSON sidecar file alongside
              each fold's serialised model so inference uses exactly the
              same shift/scale as training — and the sidecar can be
              audited / loaded independently of the model artefact.

Sidecar format  configs/models/<fold_id>_scaler.json
  {
    "fold_id":    "fold_02",
    "fitted_at":  "2026-02-27T12:00:00Z",
    "feature_names": [...],
    "mean_":      [...],
    "scale_":     [...]
  }
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Default output directory for scaler sidecars
SCALER_SIDECAR_DIR = Path(__file__).parent.parent.parent / "configs" / "models"


@dataclass
class ScalerParams:
    """Serialisable container for StandardScaler parameters."""
    fold_id:       str
    fitted_at:     str
    feature_names: list[str]
    mean_:         list[float]
    scale_:        list[float]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "ScalerParams":
        d = json.loads(raw)
        return cls(**d)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply stored normalisation to a DataFrame using the IS statistics."""
        arr    = df[self.feature_names].to_numpy(dtype=float)
        mean   = np.asarray(self.mean_)
        scale  = np.asarray(self.scale_)
        scale  = np.where(scale == 0, 1.0, scale)   # avoid div-by-zero for constant features
        normed = (arr - mean) / scale
        return pd.DataFrame(normed, index=df.index, columns=self.feature_names)


class FoldScaler:
    """
    Per-fold feature normaliser.

    CF-4 FIX 2026-02-27
    ───────────────────
    fit() is called on the IS slice ONLY.
    The resulting mean / std are then applied to both IS and OOS frames.
    Stats are saved to a JSON sidecar so they can be:
      • loaded at inference time (no scaler state in model pickle),
      • audited independently,
      • reproduced exactly from the JSON alone.
    """

    def __init__(self, fold_id: str, feature_names: Sequence[str]) -> None:
        self.fold_id       = fold_id
        self.feature_names = list(feature_names)
        self._params: ScalerParams | None = None

    # ─── Fit on IS only ──────────────────────────────────────────────────────

    def fit(self, X_is: pd.DataFrame) -> "FoldScaler":
        """
        CF-4 FIX: Compute mean/std from in-sample rows only.

        Parameters
        ----------
        X_is : pd.DataFrame — in-sample feature matrix (no OOS rows)
        """
        arr   = X_is[self.feature_names].to_numpy(dtype=float)
        mean_ = arr.mean(axis=0).tolist()
        std_  = arr.std(axis=0, ddof=1).tolist()

        self._params = ScalerParams(
            fold_id       = self.fold_id,
            fitted_at     = datetime.now(timezone.utc).isoformat(),
            feature_names = self.feature_names,
            mean_         = mean_,
            scale_        = std_,
        )
        logger.info(
            "scaler_fitted_on_is_only",       # CF-4 FIX identifier
            fold_id=self.fold_id,
            n_features=len(self.feature_names),
            n_is_rows=len(X_is),
        )
        return self

    # ─── Transform ───────────────────────────────────────────────────────────

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Apply IS-fitted statistics to any slice (IS or OOS)."""
        if self._params is None:
            raise RuntimeError(f"FoldScaler({self.fold_id}) has not been fitted yet.")
        return self._params.transform(X)

    def fit_transform(self, X_is: pd.DataFrame) -> pd.DataFrame:
        """Convenience: fit on X_is and immediately transform it."""
        return self.fit(X_is).transform(X_is)

    # ─── Sidecar persistence ─────────────────────────────────────────────────

    def save_sidecar(self, output_dir: Path | None = None) -> Path:
        """
        CF-4 FIX: Persist scaler stats as a JSON sidecar.

        Default location: configs/models/<fold_id>_scaler.json
        """
        if self._params is None:
            raise RuntimeError("Cannot save sidecar: scaler has not been fitted.")

        out_dir = Path(output_dir) if output_dir else SCALER_SIDECAR_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.fold_id}_scaler.json"

        path.write_text(self._params.to_json())
        logger.info("scaler_sidecar_saved", path=str(path))
        return path

    @classmethod
    def load_sidecar(cls, fold_id: str, sidecar_dir: Path | None = None) -> "FoldScaler":
        """
        CF-4 FIX: Load scaler params saved during training.
        Called at inference time to apply IDENTICAL normalisation.
        """
        base = Path(sidecar_dir) if sidecar_dir else SCALER_SIDECAR_DIR
        path = base / f"{fold_id}_scaler.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Scaler sidecar not found: {path}.  "
                "Run training to generate it before running inference."
            )
        params = ScalerParams.from_json(path.read_text())
        instance = cls(fold_id=fold_id, feature_names=params.feature_names)
        instance._params = params
        logger.info("scaler_sidecar_loaded", fold_id=fold_id, path=str(path))
        return instance


# ─── Convenience: build fold scalers from WalkForwardTrainer fold specs ───────

def build_fold_scalers(
    X:             pd.DataFrame,
    fold_specs:    list[tuple[int, int, int, int]],
    feature_cols:  list[str],
    output_dir:    Path | None = None,
) -> list[FoldScaler]:
    """
    Create, fit, and save one FoldScaler per training fold.

    CF-4 FIX: Each scaler is fitted on X.iloc[is_start:is_end] only.

    Parameters
    ----------
    fold_specs : list of (is_start, is_end, oos_start, oos_end) from build_folds()
    """
    scalers: list[FoldScaler] = []
    for i, (is_start, is_end, _, _) in enumerate(fold_specs):
        fold_id = f"fold_{i:02d}"
        scaler  = FoldScaler(fold_id=fold_id, feature_names=feature_cols)
        scaler.fit(X.iloc[is_start:is_end])          # CF-4: IS slice only
        scaler.save_sidecar(output_dir=output_dir)   # CF-4: persist sidecar
        scalers.append(scaler)
        logger.info(
            "fold_scaler_built",
            fold=i,
            is_rows=is_end - is_start,
        )
    return scalers
