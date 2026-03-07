"""
APEX Model Registry
Central registry tracking all model versions.
State is persisted in Redis. MLflow used for artifact tracking.
"""

import redis
import json
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ModelStatus(Enum):
    TRAINING   = "training"
    VALIDATING = "validating"
    STAGING    = "staging"
    LIVE       = "live"
    DEMOTED    = "demoted"
    FAILED     = "failed"


class ModelType(Enum):
    TFT      = "tft"
    XGB      = "xgb"
    LSTM     = "lstm"
    ENSEMBLE = "ensemble"


@dataclass
class ModelVersion:
    model_id:   str           # e.g. "tft_v12"
    model_type: ModelType
    version:    int
    status:     ModelStatus

    # Training metadata
    trained_at:              Optional[str]
    training_duration_mins:  Optional[float]
    fold_id:                 Optional[str]

    # Validation performance metrics
    val_sharpe:   Optional[float]
    val_hit_rate: Optional[float]
    val_loss:     Optional[float]
    val_mae:      Optional[float]

    # Live performance metrics (measured after going live)
    live_sharpe:   Optional[float]
    live_hit_rate: Optional[float]
    live_trades:   Optional[int]

    # MLflow
    mlflow_run_id:       Optional[str]
    mlflow_artifact_uri: Optional[str]

    # Lifecycle control
    promoted_at:      Optional[str]
    promoted_by:      str   # "auto" or "manual" or username
    demoted_at:       Optional[str]
    demotion_reason:  Optional[str]

    # A/B test
    ab_test_active: bool  = False
    ab_test_weight: float = 1.0   # 0-1, portion of signals routed here

    def to_dict(self) -> dict:
        d = asdict(self)
        d["model_type"] = self.model_type.value
        d["status"]     = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelVersion":
        d = d.copy()
        d["model_type"] = ModelType(d["model_type"])
        d["status"]     = ModelStatus(d["status"])
        return cls(**d)


class ModelRegistry:
    """
    Central registry for all APEX model versions.
    All state is stored in Redis; immutable once written.
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis      = redis_client
        self.key_prefix = "apex:models"

    # ─────────────────────────────────────────────
    # Write operations
    # ─────────────────────────────────────────────

    def register(self, model: ModelVersion) -> None:
        """Add a new model version to the registry."""
        key = f"{self.key_prefix}:{model.model_id}"
        self.redis.set(key, json.dumps(model.to_dict()))
        self.redis.sadd(f"{self.key_prefix}:all", model.model_id)
        self._log_event(
            model.model_id,
            "REGISTERED",
            f"New {model.model_type.value} v{model.version}"
        )
        logger.info(f"Registered model {model.model_id}")

    def update_status(
        self,
        model_id: str,
        status: ModelStatus,
        reason: str = ""
    ) -> None:
        model = self._get_raw(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found in registry")
        model["status"] = status.value
        if status == ModelStatus.LIVE:
            model["promoted_at"] = datetime.now(timezone.utc).isoformat()
        if status == ModelStatus.DEMOTED:
            model["demoted_at"]       = datetime.now(timezone.utc).isoformat()
            model["demotion_reason"]  = reason
        self.redis.set(f"{self.key_prefix}:{model_id}", json.dumps(model))
        self._log_event(model_id, status.value.upper(), reason)

    def update_metrics(self, model_id: str, metrics: dict) -> None:
        """Patch validation or live performance metrics onto an existing record."""
        model = self._get_raw(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found")
        model.update({k: v for k, v in metrics.items() if v is not None})
        self.redis.set(f"{self.key_prefix}:{model_id}", json.dumps(model))

    def promote_to_live(
        self,
        model_id: str,
        promoted_by: str = "manual"
    ) -> None:
        """
        Promote model_id to LIVE status.
        Automatically demotes the current LIVE model.
        Updates the Signal Engine active-model pointer.
        """
        current_live = self.get_live_model()
        if current_live:
            self.update_status(
                current_live["model_id"],
                ModelStatus.DEMOTED,
                f"Replaced by {model_id}"
            )

        model = self._get_raw(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found")
        model["promoted_by"] = promoted_by
        self.redis.set(f"{self.key_prefix}:{model_id}", json.dumps(model))
        self.update_status(model_id, ModelStatus.LIVE)

        # Tell Signal Engine which model to use
        self.redis.set("apex:signal_engine:active_model", model_id)

        self._log_event(
            model_id,
            "PROMOTED_TO_LIVE",
            f"Promoted by {promoted_by}. "
            f"Replaced: {current_live['model_id'] if current_live else 'none'}"
        )
        logger.info(f"Promoted {model_id} to LIVE (by {promoted_by})")

    def demote(
        self,
        model_id: str,
        reason: str = "manual demotion",
        demoted_by: str = "manual"
    ) -> None:
        self.update_status(model_id, ModelStatus.DEMOTED, reason)
        self._log_event(model_id, "DEMOTED", f"{reason} (by {demoted_by})")

    def start_ab_test(
        self,
        model_a_id: str,
        model_b_id: str,
        weight_b: float = 0.30
    ) -> None:
        """
        Route (1-weight_b) of signals to model_a (current live),
        and weight_b of signals to model_b (challenger).
        """
        model_a = self._get_raw(model_a_id)
        model_b = self._get_raw(model_b_id)
        if not model_a or not model_b:
            raise ValueError("Both models must exist to start A/B test")

        model_a["ab_test_active"] = True
        model_a["ab_test_weight"] = round(1.0 - weight_b, 4)
        model_b["ab_test_active"] = True
        model_b["ab_test_weight"] = round(weight_b, 4)
        model_b["status"]         = ModelStatus.LIVE.value

        self.redis.set(f"{self.key_prefix}:{model_a_id}", json.dumps(model_a))
        self.redis.set(f"{self.key_prefix}:{model_b_id}", json.dumps(model_b))

        ab_config = {
            "active":   True,
            "model_a":  model_a_id,
            "model_b":  model_b_id,
            "weight_b": weight_b,
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        self.redis.set("apex:signal_engine:ab_test", json.dumps(ab_config))

        self._log_event(
            model_b_id,
            "AB_TEST_STARTED",
            f"Testing {model_b_id} ({weight_b*100:.0f}% traffic) "
            f"vs {model_a_id}"
        )

    def stop_ab_test(self, winner_id: str) -> None:
        """Conclude an A/B test, promote the winner, demote the loser."""
        ab_raw = self.redis.get("apex:signal_engine:ab_test")
        if not ab_raw:
            raise ValueError("No A/B test is currently active")

        config   = json.loads(ab_raw)
        loser_id = (
            config["model_a"] if winner_id == config["model_b"]
            else config["model_b"]
        )

        # Clear A/B flags on winner
        winner = self._get_raw(winner_id)
        if winner:
            winner["ab_test_active"] = False
            winner["ab_test_weight"] = 1.0
            self.redis.set(f"{self.key_prefix}:{winner_id}", json.dumps(winner))

        self.update_status(loser_id, ModelStatus.DEMOTED, f"Lost A/B test to {winner_id}")
        self.redis.delete("apex:signal_engine:ab_test")

        self._log_event(winner_id, "AB_TEST_WON",   f"Defeated {loser_id}")
        self._log_event(loser_id,  "AB_TEST_LOST",  f"Lost to {winner_id}")

    # ─────────────────────────────────────────────
    # Read operations
    # ─────────────────────────────────────────────

    def get(self, model_id: str) -> Optional[dict]:
        return self._get_raw(model_id)

    def get_all(self) -> list[dict]:
        all_ids = self.redis.smembers(f"{self.key_prefix}:all")
        models  = []
        for mid in all_ids:
            mid_str = mid.decode() if isinstance(mid, bytes) else mid
            model   = self._get_raw(mid_str)
            if model:
                models.append(model)
        return sorted(models, key=lambda x: x.get("trained_at") or "", reverse=True)

    def get_live_model(self) -> Optional[dict]:
        for model in self.get_all():
            if model.get("status") == "live" and not model.get("ab_test_active"):
                return model
        # Fall back to any 'live' model
        for model in self.get_all():
            if model.get("status") == "live":
                return model
        return None

    def get_events(self, limit: int = 100) -> list[dict]:
        raw = self.redis.lrange("apex:model_events", 0, limit - 1)
        events = []
        for r in raw:
            try:
                events.append(json.loads(r))
            except Exception:
                pass
        return events

    def get_alerts(self, limit: int = 50) -> list[dict]:
        raw = self.redis.lrange("apex:model_alerts", 0, limit - 1)
        alerts = []
        for r in raw:
            try:
                alerts.append(json.loads(r))
            except Exception:
                pass
        return alerts

    def get_schedule(self) -> dict:
        raw = self.redis.get("apex:model_schedule")
        if raw:
            return json.loads(raw)
        return {
            "daily_retrain_enabled":       True,
            "daily_retrain_time_utc":      "02:00",
            "weekly_ensemble_enabled":     True,
            "weekly_ensemble_day":         "sun",
            "weekly_ensemble_time_utc":    "03:00",
            "auto_promote_enabled":        True,
            "min_sharpe_threshold":        1.2,
            "min_hit_rate_threshold":      0.52,
            "improvement_threshold_pct":   5,
        }

    def save_schedule(self, schedule: dict) -> None:
        self.redis.set("apex:model_schedule", json.dumps(schedule))

    # ─────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────

    def _get_raw(self, model_id: str) -> Optional[dict]:
        key  = f"{self.key_prefix}:{model_id}"
        data = self.redis.get(key)
        return json.loads(data) if data else None

    def _log_event(self, model_id: str, event: str, details: str) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_id":  model_id,
            "event":     event,
            "details":   details,
        }
        self.redis.lpush("apex:model_events", json.dumps(entry))
        self.redis.ltrim("apex:model_events", 0, 999)

        # Also push to agent log so dashboard shows it
        agent_entry = {
            "id":        f"mev-{datetime.now(timezone.utc).timestamp()}",
            "timestamp": entry["timestamp"],
            "type":      "ENGINE",
            "details":   f"[{model_id}] {event}: {details}",
            "symbol":    None,
        }
        self.redis.lpush("apex:agent_log", json.dumps(agent_entry))
        self.redis.ltrim("apex:agent_log", 0, 999)
