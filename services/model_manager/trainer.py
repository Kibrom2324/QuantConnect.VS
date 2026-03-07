"""
APEX Model Trainer
Handles async training for TFT, XGB, LSTM, and Ensemble models.
Each trainer follows the same pattern:
  1. Register as TRAINING in registry
  2. Subprocess the appropriate train script
  3. Parse MLflow metrics
  4. Update registry with results
  5. Optionally auto-promote if metrics beat current live
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import redis

from .model_registry import ModelRegistry, ModelVersion, ModelStatus, ModelType

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AUTO_PROMOTE_MIN_SHARPE   = 1.20
AUTO_PROMOTE_MIN_HIT_RATE = 0.52
AUTO_PROMOTE_IMPROVEMENT  = 1.05   # new must be 5% better than current live
SCRIPTS_BASE              = Path(__file__).parent.parent  # services/


def _make_base_version(
    model_id:    str,
    model_type:  ModelType,
    version:     int,
    triggered_by: str,
) -> ModelVersion:
    return ModelVersion(
        model_id=model_id,
        model_type=model_type,
        version=version,
        status=ModelStatus.TRAINING,
        trained_at=None,
        training_duration_mins=None,
        fold_id=f"fold_{version}",
        val_sharpe=None,
        val_hit_rate=None,
        val_loss=None,
        val_mae=None,
        live_sharpe=None,
        live_hit_rate=None,
        live_trades=0,
        mlflow_run_id=None,
        mlflow_artifact_uri=None,
        promoted_at=None,
        promoted_by=triggered_by,
        demoted_at=None,
        demotion_reason=None,
    )


class ModelTrainer:
    """Non-blocking async trainer for all APEX model types."""

    def __init__(self, registry: ModelRegistry, redis_client: redis.Redis):
        self.registry      = registry
        self.redis         = redis_client
        self.training_jobs: dict[str, asyncio.Task] = {}

    # ─────────────────────────────────────────────
    # Public: train individual models
    # ─────────────────────────────────────────────

    async def train_tft(
        self,
        version:      int,
        triggered_by: str = "scheduler"
    ) -> str:
        model_id = f"tft_v{version}"
        self._register_training(model_id, ModelType.TFT, version, triggered_by)
        start = datetime.now(timezone.utc)

        try:
            proc = await asyncio.create_subprocess_exec(
                "python",
                str(SCRIPTS_BASE / "model_training" / "train_tft.py"),
                "--fold",               str(version),
                "--mlflow-experiment",  "apex_tft",
                "--model-id",           model_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace")[:500]
                logger.error(f"TFT training failed: {error_msg}")
                self.registry.update_status(model_id, ModelStatus.FAILED, error_msg)
                return model_id

            duration = (datetime.now(timezone.utc) - start).seconds / 60
            metrics  = self._get_mlflow_metrics("apex_tft", model_id)
            self._finalise_training(model_id, duration, metrics)
            await self._maybe_auto_promote(model_id)

        except Exception as e:
            logger.exception(f"train_tft exception: {e}")
            self.registry.update_status(model_id, ModelStatus.FAILED, str(e))

        return model_id

    async def train_xgb(
        self,
        version:      int,
        triggered_by: str = "scheduler"
    ) -> str:
        """
        XGBoost/LightGBM ensemble trainer.
        Trains in ~5 minutes — much faster than TFT.
        """
        model_id = f"xgb_v{version}"
        self._register_training(model_id, ModelType.XGB, version, triggered_by)
        start = datetime.now(timezone.utc)

        try:
            proc = await asyncio.create_subprocess_exec(
                "python",
                str(SCRIPTS_BASE / "model_training" / "train_xgb.py"),
                "--fold",              str(version),
                "--mlflow-experiment", "apex_xgb",
                "--model-id",          model_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace")[:500]
                logger.error(f"XGB training failed: {error_msg}")
                self.registry.update_status(model_id, ModelStatus.FAILED, error_msg)
                return model_id

            duration = (datetime.now(timezone.utc) - start).seconds / 60
            metrics  = self._get_mlflow_metrics("apex_xgb", model_id)
            self._finalise_training(model_id, duration, metrics)
            await self._maybe_auto_promote(model_id)

        except Exception as e:
            logger.exception(f"train_xgb exception: {e}")
            self.registry.update_status(model_id, ModelStatus.FAILED, str(e))

        return model_id

    async def train_lstm(
        self,
        version:      int,
        triggered_by: str = "scheduler"
    ) -> str:
        """
        LSTM trainer — classic deep learning baseline.
        ~30 minutes training time.
        """
        model_id = f"lstm_v{version}"
        self._register_training(model_id, ModelType.LSTM, version, triggered_by)
        start = datetime.now(timezone.utc)

        try:
            proc = await asyncio.create_subprocess_exec(
                "python",
                str(SCRIPTS_BASE / "model_training" / "train_lstm.py"),
                "--fold",              str(version),
                "--mlflow-experiment", "apex_lstm",
                "--model-id",          model_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace")[:500]
                logger.error(f"LSTM training failed: {error_msg}")
                self.registry.update_status(model_id, ModelStatus.FAILED, error_msg)
                return model_id

            duration = (datetime.now(timezone.utc) - start).seconds / 60
            metrics  = self._get_mlflow_metrics("apex_lstm", model_id)
            self._finalise_training(model_id, duration, metrics)
            await self._maybe_auto_promote(model_id)

        except Exception as e:
            logger.exception(f"train_lstm exception: {e}")
            self.registry.update_status(model_id, ModelStatus.FAILED, str(e))

        return model_id

    async def train_ensemble(
        self,
        version:     int,
        xgb_id:      str,
        lstm_id:     str,
        tft_id:      Optional[str] = None,
        timesfm_id:  Optional[str] = None,
        triggered_by: str = "scheduler"
    ) -> str:
        """
        Ensemble trainer — supports either TFT or TimesFM as the third component.

        Reads predictions from XGB/LSTM and one of TFT/TimesFM staging models,
        runs scipy.optimize to maximise Sharpe, stores weights in Redis and MLflow.

        Args:
            version:      Numeric version suffix for the new ensemble model ID.
            xgb_id:       XGB component model ID (required).
            lstm_id:      LSTM component model ID (required).
            tft_id:       TFT component model ID.  Mutually exclusive with timesfm_id.
            timesfm_id:   TimesFM component model ID.  Mutually exclusive with tft_id.
            triggered_by: Who triggered this training run.

        Returns:
            The new ensemble model_id string (e.g. 'ens_v3').

        Raises:
            ValueError: If both tft_id and timesfm_id are provided, or neither.
        """
        if tft_id is not None and timesfm_id is not None:
            raise ValueError(
                "Provide either tft_id OR timesfm_id — not both. "
                "The ensemble supports one time-series foundation component."
            )
        if tft_id is None and timesfm_id is None:
            raise ValueError(
                "Exactly one of tft_id or timesfm_id must be provided."
            )

        model_id = f"ens_v{version}"
        self._register_training(model_id, ModelType.ENSEMBLE, version, triggered_by)
        start = datetime.now(timezone.utc)

        # Build subprocess arguments — use --timesfm-id when requested
        if timesfm_id is not None:
            third_component_args = ["--timesfm-id", timesfm_id]
            component_key        = "timesfm"
            component_val        = timesfm_id
        else:
            third_component_args = ["--tft-id", tft_id]  # type: ignore[arg-type]
            component_key        = "tft"
            component_val        = tft_id

        try:
            proc = await asyncio.create_subprocess_exec(
                "python",
                str(SCRIPTS_BASE / "model_training" / "train_ensemble.py"),
                *third_component_args,
                "--xgb-id",   xgb_id,
                "--lstm-id",  lstm_id,
                "--version",  str(version),
                "--model-id", model_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace")[:500]
                logger.error(f"Ensemble training failed: {error_msg}")
                self.registry.update_status(model_id, ModelStatus.FAILED, error_msg)
                return model_id

            duration = (datetime.now(timezone.utc) - start).seconds / 60
            metrics  = self._get_mlflow_metrics("apex_ensemble", model_id)

            # Store the component model IDs for transparency
            model = self.registry.get(model_id)
            if model:
                model["component_models"] = {
                    component_key: component_val,
                    "xgb":         xgb_id,
                    "lstm":        lstm_id,
                }
                self.redis.set(f"apex:models:{model_id}", json.dumps(model))

            self._finalise_training(model_id, duration, metrics)
            await self._maybe_auto_promote(model_id)

        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            logger.exception(f"train_ensemble exception: {e}")
            self.registry.update_status(model_id, ModelStatus.FAILED, str(e))

        return model_id

    # ─────────────────────────────────────────────
    # Auto-promote logic
    # ─────────────────────────────────────────────

    async def _maybe_auto_promote(self, new_model_id: str) -> None:
        """
        Auto-promote if:
          - Sharpe >= AUTO_PROMOTE_MIN_SHARPE (1.2)
          - Hit rate >= AUTO_PROMOTE_MIN_HIT_RATE (52%)
          - Sharpe > current_live * improvement_factor (5% better)
        Skips if any threshold not met and logs reason.
        """
        sched = self.registry.get_schedule()
        if not sched.get("auto_promote_enabled", True):
            return

        min_sharpe   = sched.get("min_sharpe_threshold", AUTO_PROMOTE_MIN_SHARPE)
        min_hit_rate = sched.get("min_hit_rate_threshold", AUTO_PROMOTE_MIN_HIT_RATE)
        improve_pct  = sched.get("improvement_threshold_pct", 5) / 100.0

        new_model    = self.registry.get(new_model_id)
        if not new_model:
            return

        new_sharpe   = new_model.get("val_sharpe")   or 0
        new_hit_rate = new_model.get("val_hit_rate")  or 0

        if new_sharpe < min_sharpe or new_hit_rate < min_hit_rate:
            self.registry._log_event(
                new_model_id,
                "AUTO_PROMOTE_SKIPPED",
                f"Sharpe {new_sharpe:.3f} or hit_rate "
                f"{new_hit_rate:.3f} below threshold"
            )
            return

        current_live    = self.registry.get_live_model()
        current_sharpe  = (current_live.get("val_sharpe") or 0) if current_live else 0

        if current_live is None or new_sharpe > current_sharpe * (1 + improve_pct):
            self.registry.promote_to_live(new_model_id, "auto")
        else:
            self.registry._log_event(
                new_model_id,
                "AUTO_PROMOTE_SKIPPED",
                f"New Sharpe {new_sharpe:.3f} not "
                f"{improve_pct*100:.0f}% better than "
                f"live {current_sharpe:.3f}"
            )

    # ─────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────

    def _register_training(
        self,
        model_id:     str,
        model_type:   ModelType,
        version:      int,
        triggered_by: str,
    ) -> None:
        mv = _make_base_version(model_id, model_type, version, triggered_by)
        self.registry.register(mv)
        logger.info(f"Queued training for {model_id}")

    def _finalise_training(
        self,
        model_id:  str,
        duration:  float,
        metrics:   dict
    ) -> None:
        self.registry.update_metrics(model_id, {
            "trained_at":             datetime.now(timezone.utc).isoformat(),
            "training_duration_mins": round(duration, 2),
            "val_sharpe":             metrics.get("val_sharpe"),
            "val_hit_rate":           metrics.get("val_hit_rate"),
            "val_loss":               metrics.get("val_loss"),
            "val_mae":                metrics.get("val_mae"),
            "mlflow_run_id":          metrics.get("run_id"),
            "mlflow_artifact_uri":    metrics.get("artifact_uri"),
            "status":                 ModelStatus.STAGING.value,
        })

    def _get_mlflow_metrics(self, experiment: str, model_id: str) -> dict:
        try:
            import mlflow
            client = mlflow.tracking.MlflowClient()
            exp    = client.get_experiment_by_name(experiment)
            if exp is None:
                return {}
            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                filter_string=f"tags.model_id = '{model_id}'",
                max_results=1,
            )
            if runs:
                run = runs[0]
                return {
                    "val_sharpe":    run.data.metrics.get("val_sharpe"),
                    "val_hit_rate":  run.data.metrics.get("val_hit_rate"),
                    "val_loss":      run.data.metrics.get("val_loss"),
                    "val_mae":       run.data.metrics.get("val_mae"),
                    "run_id":        run.info.run_id,
                    "artifact_uri":  run.info.artifact_uri,
                }
        except Exception as e:
            logger.warning(f"MLflow metrics lookup failed for {model_id}: {e}")
        return {}
