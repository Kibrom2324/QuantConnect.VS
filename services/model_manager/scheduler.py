"""
APEX Model Scheduler
Runs automated daily retraining and weekly ensemble optimization.
Entry point: python -m services.model_manager.scheduler
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import pytz
import redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .model_registry import ModelRegistry
from .trainer import ModelTrainer
from .ensemble import SmartEnsemble

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


class ModelScheduler:
    """
    Automated training pipeline:
      02:00 UTC daily   — full retrain (XGB → LSTM → TFT → Ensemble)
      03:00 UTC Sunday  — ensemble weight optimization (28-day lookback)
      Every hour        — live model performance monitoring
    """

    def __init__(self):
        self.redis    = redis.Redis(
            host="redis",
            port=6379,
            decode_responses=True,
        )
        self.registry  = ModelRegistry(self.redis)
        self.trainer   = ModelTrainer(self.registry, self.redis)
        self.ensemble  = SmartEnsemble(self.redis)
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)

    def start(self) -> None:
        self.scheduler.add_job(
            self.daily_retrain,
            "cron",
            hour=2,
            minute=0,
            id="daily_retrain",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.weekly_ensemble_optimize,
            "cron",
            day_of_week="sun",
            hour=3,
            minute=0,
            id="weekly_ensemble",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.check_live_model_performance,
            "interval",
            hours=1,
            id="perf_monitor",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Model scheduler started — daily 02:00, weekly Sun 03:00")

    # ─────────────────────────────────────────────
    # Daily retrain pipeline
    # ─────────────────────────────────────────────

    async def daily_retrain(self) -> None:
        """
        Full daily retraining pipeline.
        Order: XGB (fast) → LSTM (~30 min) → TFT (~2-4 h) → Ensemble
        If any component fails it is logged but the pipeline continues.
        Ensemble is only trained if all three pass staging.
        """
        logger.info("=== Daily retrain starting ===")
        version = self._get_next_version()
        self._push_agent_log("TRAINING_STARTED", f"Daily retrain v{version} started")

        results: dict[str, str] = {}

        for name, coro in [
            ("xgb",  self.trainer.train_xgb(version,  "scheduler")),
            ("lstm", self.trainer.train_lstm(version, "scheduler")),
            ("tft",  self.trainer.train_tft(version,  "scheduler")),
        ]:
            try:
                results[name] = await coro
            except Exception as e:
                logger.error(f"{name} training raised exception: {e}")
                self._push_agent_log(
                    "TRAINING_FAILED",
                    f"{name.upper()} v{version} failed: {e}"
                )

        # Check all three reached staging
        all_staging = all(
            self.registry.get(results.get(k, "")) is not None
            and self.registry.get(results[k])["status"] == "staging"
            for k in ["tft", "xgb", "lstm"]
            if k in results
        )

        if all_staging and len(results) == 3:
            try:
                ens_id = await self.trainer.train_ensemble(
                    version,
                    results["tft"],
                    results["xgb"],
                    results["lstm"],
                    "scheduler",
                )
                self._push_agent_log(
                    "TRAINING_COMPLETE",
                    f"Daily retrain v{version} done — ensemble: {ens_id}"
                )
                logger.info(f"Daily retrain complete. Ensemble: {ens_id}")
            except Exception as e:
                logger.error(f"Ensemble training failed: {e}")
        else:
            msg = "Not all models reached staging — skipping ensemble"
            logger.warning(msg)
            self._push_agent_log("TRAINING_PARTIAL", msg)

        self._push_agent_log(
            "TRAINING_COMPLETE",
            f"Daily retrain v{version} finished"
        )

    # ─────────────────────────────────────────────
    # Weekly ensemble weight optimization
    # ─────────────────────────────────────────────

    async def weekly_ensemble_optimize(self) -> None:
        """
        Re-optimizes ensemble weights using 28 days of live trade history.
        Uses scipy.optimize to find weights that maximize Sharpe.
        Applied immediately if improvement >= 2%.
        """
        logger.info("=== Weekly ensemble weight optimization starting ===")
        self._push_agent_log(
            "ENSEMBLE_OPTIMIZE",
            "Weekly ensemble weight optimization started"
        )

        loop    = asyncio.get_event_loop()
        new_weights = await loop.run_in_executor(
            None,
            lambda: self.ensemble.optimize_weights(lookback_days=28)
        )

        self._push_agent_log(
            "ENSEMBLE_OPTIMIZE",
            f"Optimization complete. "
            f"Weights: TFT {new_weights['tft']:.0%} "
            f"XGB {new_weights['xgb']:.0%} "
            f"LSTM {new_weights['lstm']:.0%}"
        )
        logger.info(f"Ensemble weights updated: {new_weights}")

    # ─────────────────────────────────────────────
    # Hourly performance monitoring
    # ─────────────────────────────────────────────

    async def check_live_model_performance(self) -> None:
        """
        Hourly check for live model degradation.
        Alerts (does NOT auto-demote) if live_sharpe drops
        more than 30% below validation Sharpe.
        Human must decide the action.
        """
        live = self.registry.get_live_model()
        if not live:
            return

        live_sharpe = live.get("live_sharpe")
        val_sharpe  = live.get("val_sharpe")

        if live_sharpe is None or val_sharpe is None:
            return

        if live_sharpe < val_sharpe * 0.70:
            alert = {
                "id":              f"alert-{datetime.now(timezone.utc).timestamp()}",
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "type":            "MODEL_DEGRADED",
                "severity":        "HIGH",
                "model_id":        live["model_id"],
                "details": (
                    f"Live Sharpe {live_sharpe:.3f} is >30% below "
                    f"validation Sharpe {val_sharpe:.3f}. "
                    f"Human review required."
                ),
                "action_required": True,
                "options": [
                    "demote_and_rollback",
                    "start_ab_test",
                    "continue_monitoring"
                ],
                "dismissed": False,
            }
            self.redis.lpush("apex:model_alerts", json.dumps(alert))
            self.redis.lpush("apex:agent_log",    json.dumps({
                **alert,
                "type":    "RISK_FAIL",
                "details": f"⚠ MODEL DEGRADED: {alert['details']}",
            }))
            logger.warning(
                f"ALERT: {live['model_id']} live Sharpe {live_sharpe:.3f} "
                f"degraded vs validation {val_sharpe:.3f}"
            )

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    def _get_next_version(self) -> int:
        v = self.redis.incr("apex:model_version_counter")
        return int(v)

    def _push_agent_log(self, event_type: str, details: str) -> None:
        entry = {
            "id":        f"sched-{datetime.now(timezone.utc).timestamp()}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type":      event_type,
            "details":   details,
            "source":    "scheduler",
        }
        self.redis.lpush("apex:agent_log", json.dumps(entry))
        self.redis.ltrim("apex:agent_log", 0, 999)


if __name__ == "__main__":
    sched = ModelScheduler()
    sched.start()
    asyncio.get_event_loop().run_forever()
