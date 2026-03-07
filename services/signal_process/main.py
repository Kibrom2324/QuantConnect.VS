"""
APEX Consolidated Signal Process — services/signal_process/main.py

Phase 6: Consolidates the Hot Decision Plane into a single service:
  Kafka Consumer (market.raw)
  → Feature Engine
  → Regime Classifier
  → Parallel Model Inference (XGBoost, LSTM, TimesFM, Indicator Composite)
  → Adaptive Combiner (regime-weighted)
  → Calibrator (isotonic)
  → OOD Detector
  → Disagreement Modifier
  → Cost Estimator (net edge, veto if < 0)
  → Kafka Producer → signals.scored

Feature flags control each new component. When disabled, the service
behaves identically to the original signal_engine/main.py.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from services.graceful_shutdown import GracefulShutdown
from services.signal_engine.ensemble import EnsembleScorer
from shared.core.adaptive_combiner import AdaptiveCombiner
from shared.core.calibrator import IsotonicCalibrator
from shared.core.cost_estimator import ExecutionCostEstimator
from shared.core.disagreement import DisagreementModifier
from shared.core.ood_detector import OODDetector
from shared.core.regime import RegimeClassifier
from shared.core.staleness import StalenessPolicy

logger = structlog.get_logger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
RAW_TOPIC = os.environ.get("SIGNAL_RAW_TOPIC", "market.raw")
SCORED_TOPIC = os.environ.get("SIGNAL_SCORED_TOPIC", "signals.scored")
GROUP_ID = os.environ.get("SIGNAL_GROUP_ID", "apex-signal-process-v1")
NY_TZ = ZoneInfo("America/New_York")

# Feature flags
ENABLE_REGIME = os.environ.get("ENABLE_REGIME_DETECTION", "false").lower() == "true"
ENABLE_ADAPTIVE = os.environ.get("ENABLE_ADAPTIVE_COMBINER", "false").lower() == "true"
ENABLE_ISOTONIC = os.environ.get("ENABLE_ISOTONIC_CALIBRATION", "false").lower() == "true"
ENABLE_OOD = os.environ.get("ENABLE_OOD_DETECTION", "false").lower() == "true"
ENABLE_DISAGREEMENT = os.environ.get("ENABLE_DISAGREEMENT_MODIFIER", "false").lower() == "true"
ENABLE_COST = os.environ.get("ENABLE_COST_ESTIMATION", "false").lower() == "true"
ENABLE_LINEAGE = os.environ.get("ENABLE_PREDICTION_LINEAGE", "false").lower() == "true"
ENABLE_STALENESS = os.environ.get("ENABLE_STALENESS_POLICY", "false").lower() == "true"

SIGNAL_MIN_CONVICTION = float(os.environ.get("SIGNAL_MIN_CONVICTION", "0.03"))


class ConsolidatedSignalProcess:
    """
    Consolidated signal process running the entire Hot Decision Plane
    in a single service.
    """

    def __init__(self) -> None:
        self._shutdown = GracefulShutdown()

        # Core scoring
        self._ensemble = EnsembleScorer()

        # Phase 0: calibration
        self._isotonic = IsotonicCalibrator()

        # Phase 1: cost estimation + staleness
        self._cost_estimator = ExecutionCostEstimator()
        self._staleness = StalenessPolicy()

        # Phase 3: regime + adaptive combiner + disagreement
        self._regime = RegimeClassifier()
        self._combiner = AdaptiveCombiner()
        self._disagreement = DisagreementModifier()

        # Phase 4: OOD detection
        self._ood = OODDetector()

        # Redis client for calibrator + LLM scores
        import redis as _redis_lib
        try:
            self._redis = _redis_lib.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", "16379")),
                socket_timeout=1,
                decode_responses=True,
            )
            self._redis.ping()
        except Exception:
            self._redis = None

        # Load calibrator from Redis
        if self._redis:
            try:
                self._isotonic.load_from_redis(self._redis)
            except Exception:
                pass

        # Kafka consumer/producer
        self._consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        })
        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
        })

    async def run(self) -> None:
        self._consumer.subscribe([RAW_TOPIC])
        logger.info("signal_process_started", topic=RAW_TOPIC)

        while not self._shutdown.is_shutdown:
            msg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._consumer.poll(timeout=1.0),
            )
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("kafka_error", error=str(msg.error()))
                continue

            await self._process(msg)

        self._consumer.close()
        self._producer.flush()
        logger.info("signal_process_stopped")

    async def _process(self, msg) -> None:
        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except Exception as e:
            logger.error("invalid_message", error=str(e))
            self._consumer.commit(message=msg, asynchronous=False)
            return

        symbol = payload.get("symbol", "UNKNOWN")

        # ── Regime classification ──────────────────────────────────────────
        regime = 0
        if ENABLE_REGIME:
            regime = self._regime.classify(payload)
            if self._redis:
                try:
                    self._redis.set(f"apex:regime:{symbol}", str(regime))
                except Exception:
                    pass

        # ── Ensemble scoring (includes TFT staleness gate) ─────────────────
        score, prediction_ids = self._ensemble.score(payload)
        if score is None:
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # ── Staleness policy (additional decay beyond TFT gate) ────────────
        if ENABLE_STALENESS:
            # Apply decay to overall score based on data freshness
            age = float(payload.get("data_age_seconds", 0))
            result = self._staleness.evaluate("xgboost", age, abs(score))
            if result.is_expired:
                logger.warning("signal_expired", symbol=symbol, age=age)
                self._consumer.commit(message=msg, asynchronous=False)
                return
            score = score * result.decay_factor if score > 0 else score / result.decay_factor if result.decay_factor > 0 else 0

        # ── Calibration ────────────────────────────────────────────────────
        if ENABLE_ISOTONIC and self._isotonic.is_fitted:
            prob = self._isotonic.calibrate(score)
        else:
            # Fallback: sigmoid approximation
            prob = 1.0 / (1.0 + np.exp(-score))

        # ── OOD detection ──────────────────────────────────────────────────
        ood_score = 0.0
        ood_flag = False
        if ENABLE_OOD and self._ood.is_fitted:
            # Extract numerical features for OOD check
            feature_keys = ["rsi_14", "ema_12", "ema_26", "macd_line", "realized_vol_20d"]
            features = np.array([float(payload.get(k, 0.0)) for k in feature_keys])
            ood_result = self._ood.evaluate(features)
            ood_score = ood_result.ood_score
            ood_flag = ood_result.should_suppress
            prob *= ood_result.confidence_modifier

            if ood_result.should_suppress:
                logger.warning("signal_ood_suppressed", symbol=symbol, ood_score=ood_score)
                self._consumer.commit(message=msg, asynchronous=False)
                return

        # ── Disagreement modifier ──────────────────────────────────────────
        disagreement_score = 0.0
        if ENABLE_DISAGREEMENT:
            model_probs = {}
            for key in ["tft_score", "xgb_score", "factor_score", "llm_score"]:
                val = payload.get(key)
                if val is not None:
                    model_probs[key.replace("_score", "")] = float(val)
            if model_probs:
                dis_result = self._disagreement.analyze(model_probs, symbol)
                disagreement_score = dis_result.disagreement_score
                prob *= dis_result.modifier

        # ── Conviction filter ──────────────────────────────────────────────
        conviction = abs(prob - 0.5)
        if conviction < SIGNAL_MIN_CONVICTION:
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # ── Cost estimation ────────────────────────────────────────────────
        net_edge_bps = None
        estimated_cost_bps = 0.0
        raw_edge_bps = (prob - 0.5) * 200  # convert prob to ~bps
        if ENABLE_COST:
            spread = float(payload.get("spread_bps", 5.0))
            adv = float(payload.get("dollar_volume", 1e6))
            order_value = float(payload.get("order_value", 1000.0))
            cost = self._cost_estimator.estimate(raw_edge_bps, spread, order_value, adv)
            net_edge_bps = cost.net_edge_bps
            estimated_cost_bps = cost.total_cost_bps

            if self._cost_estimator.should_veto(cost):
                logger.info("signal_vetoed_negative_edge", symbol=symbol, net_edge=net_edge_bps)
                self._consumer.commit(message=msg, asynchronous=False)
                return

        # ── Determine direction ────────────────────────────────────────────
        direction = 1 if score > 0 else -1

        # ── Build output signal ────────────────────────────────────────────
        from shared.contracts.schemas import generate_signal_id
        signal_id = generate_signal_id()

        signal_payload = {
            "signal_id": signal_id,
            "symbol": symbol,
            "side": "BUY" if direction == 1 else "SELL",
            "direction": direction,
            "raw_score": score,
            "probability": prob,
            "calibrated_prob": prob,
            "regime": regime,
            "ood_score": ood_score,
            "ood_flag": ood_flag,
            "disagreement_score": disagreement_score,
            "raw_edge_bps": raw_edge_bps,
            "net_edge_bps": net_edge_bps,
            "estimated_cost_bps": estimated_cost_bps,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        if ENABLE_LINEAGE:
            signal_payload["prediction_ids"] = prediction_ids

        out = json.dumps(signal_payload).encode()
        self._producer.produce(SCORED_TOPIC, value=out)
        self._producer.flush()
        self._consumer.commit(message=msg, asynchronous=False)

        logger.info(
            "signal_published",
            symbol=symbol,
            prob=round(prob, 4),
            regime=regime,
            net_edge=net_edge_bps,
        )


async def main() -> None:
    svc = ConsolidatedSignalProcess()
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
