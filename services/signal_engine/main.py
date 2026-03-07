"""
APEX Signal Engine — services/signal_engine/main.py

Fixes implemented in this file
───────────────────────────────
  Bug-B   Kafka consumer auto-commit = False + manual commit after success.
          (Was enable.auto.commit=True, so a crash after consume but before
           publish would silently drop a signal.)

  HI-3    Platt scaler fitted-before-use guard.
          Inference raises a clean RuntimeError if the scaler has not
          been fitted / loaded, rather than producing NaN probabilities.

  HI-5    DST-aware market-hours check using ZoneInfo("America/New_York").
          (Was using UTC offset -5 fixed, wrong during EDT UTC-4.)
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo  # HI-5: DST-aware timezone

import numpy as np
import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from services.graceful_shutdown import GracefulShutdown
import redis as _redis_lib

from services.signal_engine.ensemble import EnsembleScorer
from services.signal_engine.filters import MarketHoursFilter
from shared.core.calibrator import IsotonicCalibrator
from shared.contracts.schemas import generate_signal_id

# Redis connection for reading LLM sentiment scores written by llm_agent
_LLM_REDIS_HOST   = os.environ.get("REDIS_HOST",  "localhost")
_LLM_REDIS_PORT   = int(os.environ.get("REDIS_PORT", "16379"))
_LLM_KEY_PREFIX   = "apex:llm:sentiment:"

# Phase 0: feature flags
ENABLE_ISOTONIC_CALIBRATION: bool = (
    os.environ.get("ENABLE_ISOTONIC_CALIBRATION", "false").lower() == "true"
)
ENABLE_PREDICTION_LINEAGE: bool = (
    os.environ.get("ENABLE_PREDICTION_LINEAGE", "false").lower() == "true"
)


def _get_llm_score(symbol: str, r) -> float | None:
    """Read LLM sentiment score from Redis. Returns None if absent or Redis down."""
    if r is None:
        return None
    try:
        raw = r.get(f"{_LLM_KEY_PREFIX}{symbol}")
        if raw is None:
            return None
        data = json.loads(raw)
        return float(data["sentiment"])
    except Exception:
        return None

logger = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
RAW_TOPIC       = os.environ.get("SIGNAL_RAW_TOPIC",       "apex.signals.raw")
SCORED_TOPIC    = os.environ.get("SIGNAL_SCORED_TOPIC",    "apex.signals.scored")
GROUP_ID        = os.environ.get("SIGNAL_GROUP_ID",        "apex-signal-engine-v1")
HEALTH_PORT     = int(os.environ.get("HEALTH_PORT", "8006"))

# HI-5 FIX 2026-02-27: DST-aware New York timezone
NY_TZ = ZoneInfo("America/New_York")


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal /health endpoint for liveness probes."""

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            body = json.dumps({
                "status": "healthy",
                "service": "signal_engine",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access logs


class PlattScaler:
    """
    Sigmoid probability calibration (Platt scaling).

    HI-3 FIX 2026-02-27: raises RuntimeError if predict() is called before
    the scaler is fitted or loaded.  Previously it would silently return NaN
    when self._coef was unset.
    """

    def __init__(self) -> None:
        self._coef:      float | None = None
        self._intercept: float | None = None
        self._is_fitted: bool = False

    def fit(self, decision_scores: np.ndarray, y: np.ndarray) -> "PlattScaler":
        """Fit logistic regression on decision scores vs labels."""
        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(C=1.0, max_iter=500)
        lr.fit(decision_scores.reshape(-1, 1), y)
        self._coef      = float(lr.coef_[0][0])
        self._intercept = float(lr.intercept_[0])
        self._is_fitted = True
        return self

    def predict_proba(self, scores: np.ndarray) -> np.ndarray:
        """
        HI-3 FIX: guard against unfitted state.
        Raises RuntimeError instead of returning NaN probabilities.
        """
        if not self._is_fitted or self._coef is None:
            raise RuntimeError(
                "PlattScaler.predict_proba() called before fit() or load().  "
                "Run training or load a persisted scaler before inference."
            )
        logit = self._coef * scores + self._intercept
        return 1.0 / (1.0 + np.exp(-logit))

    def save(self, path: str) -> None:
        import json
        with open(path, "w") as f:
            json.dump({"coef": self._coef, "intercept": self._intercept}, f)
        self._is_fitted = True

    def load(self, path: str) -> "PlattScaler":
        import json
        with open(path) as f:
            d = json.load(f)
        self._coef      = float(d["coef"])
        self._intercept = float(d["intercept"])
        self._is_fitted = True
        return self


class SignalEngineService:
    """
    Consumes raw feature events from Kafka, scores them through the ensemble,
    and publishes approved signals.

    Bug-B FIX: enable.auto.commit=False + manual commit on success path only.
    HI-3 FIX: PlattScaler raises if not fitted.
    HI-5 FIX: market-hours gate is DST-aware.
    """

    def __init__(self) -> None:
        self._shutdown  = GracefulShutdown()
        self._ensemble  = EnsembleScorer()
        self._mh_filter = MarketHoursFilter()
        self._scaler    = PlattScaler()
        self._scaler_path = os.environ.get("PLATT_SCALER_PATH", "configs/models/platt_scaler.json")

        # Redis client for reading LLM sentiment (optional — soft-fail if down)
        try:
            self._llm_redis = _redis_lib.Redis(
                host=_LLM_REDIS_HOST,
                port=_LLM_REDIS_PORT,
                socket_timeout=1,
                decode_responses=True,
            )
            self._llm_redis.ping()
            logger.info("llm_redis_connected", host=_LLM_REDIS_HOST, port=_LLM_REDIS_PORT)
        except Exception as exc:
            logger.warning("llm_redis_unavailable_llm_scores_disabled", error=str(exc))
            self._llm_redis = None

        # HI-3: load scaler before inference — raises if file missing
        try:
            self._scaler.load(self._scaler_path)
        except FileNotFoundError:
            logger.warning(
                "platt_scaler_not_found_will_raise_at_inference",
                path=self._scaler_path,
            )

        # Phase 0: isotonic calibrator (shadow mode by default)
        # Calibrator stores pickled bytes — needs decode_responses=False
        self._isotonic = IsotonicCalibrator()
        try:
            _cal_redis = _redis_lib.Redis(
                host=_LLM_REDIS_HOST,
                port=_LLM_REDIS_PORT,
                socket_timeout=1,
                decode_responses=False,
            )
            self._isotonic.load_from_redis(_cal_redis)
            if self._isotonic.is_fitted:
                logger.info("isotonic_calibrator_loaded_from_redis")
            else:
                logger.info("isotonic_calibrator_not_in_redis_using_passthrough")
        except Exception as exc:
            logger.info("isotonic_calibrator_not_loaded_using_passthrough", error=str(exc))

        # Bug-B FIX 2026-02-27: auto-commit disabled — we commit manually
        self._consumer = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           GROUP_ID,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": False,   # Bug-B FIX
        })
        self._producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "acks": "all"})

    async def run(self) -> None:
        self._consumer.subscribe([RAW_TOPIC])
        logger.info("signal_engine_started", topic=RAW_TOPIC)

        while not self._shutdown.is_shutdown:
            msg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._consumer.poll(timeout=1.0)
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
        logger.info("signal_engine_stopped")

    async def _process(self, msg) -> None:
        """
        Bug-B FIX: consumer.commit() is called ONLY after successful publish.
        If scoring or publishing fails the offset is not advanced, so the
        message will be reprocessed after restart.
        """
        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except Exception as e:
            logger.error("invalid_message_json", error=str(e))
            # Poison pill: commit to skip, send to DLQ (not implemented here for brevity)
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # HI-5 FIX: market hours check is DST-aware
        now_ny = datetime.now(NY_TZ)
        if not os.environ.get("SKIP_MARKET_HOURS") and not self._mh_filter.is_market_open(now_ny):
            logger.debug("outside_market_hours_dropping_signal", symbol=payload.get("symbol"))
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # Inject LLM sentiment from Redis (written by services/llm_agent/main.py)
        symbol = payload.get("symbol")
        llm = _get_llm_score(symbol, self._llm_redis)
        if llm is not None:
            payload["llm_score"] = llm
            logger.debug("llm_score_injected", symbol=symbol, llm_score=llm)

        # Score (Phase 0: now returns tuple with prediction IDs)
        score, prediction_ids = self._ensemble.score(payload)
        if score is None:
            logger.warning("ensemble_returned_none", symbol=payload.get("symbol"))
            return  # DO NOT commit — retry might help if TFT data was stale

        # Calibrate probability (HI-3: raises if scaler not fitted)
        try:
            prob = float(self._scaler.predict_proba(np.array([score]))[0])
        except RuntimeError as e:
            logger.critical("platt_scaler_not_fitted", error=str(e))
            return  # halt signal flow — misconfigured deployment

        # Isotonic calibration: active when flag is on and model is fitted,
        # otherwise fall back to Platt.  Rollback: set ENABLE_ISOTONIC_CALIBRATION=false.
        iso_prob = self._isotonic.calibrate(score)
        if ENABLE_ISOTONIC_CALIBRATION and self._isotonic.is_fitted:
            active_prob = iso_prob
        else:
            active_prob = prob

        logger.info(
            "calibration_comparison",
            symbol=symbol,
            platt_prob=round(prob, 6),
            isotonic_prob=round(iso_prob, 6),
            active_prob=round(active_prob, 6),
            delta=round(abs(prob - iso_prob), 6),
            source="isotonic" if active_prob == iso_prob else "platt",
        )

        # Drop low-conviction signals — |active_prob - 0.5| must exceed threshold
        _min_conviction = float(os.environ.get("SIGNAL_MIN_CONVICTION", "0.03"))
        conviction = abs(active_prob - 0.5)
        if conviction < _min_conviction:
            logger.debug(
                "signal_below_conviction_dropped",
                symbol=payload.get("symbol"),
                conviction=round(conviction, 4),
                threshold=_min_conviction,
            )
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # Publish
        # raw_edge_bps: probability edge expressed in basis points
        #   (active_prob - 0.5) * 200  →  e.g. prob=0.72 → 44 bps edge
        _raw_edge_bps = round((active_prob - 0.5) * 200, 2)
        signal_payload = {
            "signal_id":      generate_signal_id(),
            "symbol":         payload.get("symbol"),
            "side":           "BUY" if score > 0 else "SELL",
            "raw_score":      score,
            "raw_edge_bps":   _raw_edge_bps,
            "probability":    active_prob,
            "calibrated_prob": active_prob,
            "isotonic_prob":  iso_prob,
            "platt_prob":     prob,
            "prediction_ids": prediction_ids,
            "feature_version": payload.get("feature_version", "legacy"),
            "ts":             datetime.now(timezone.utc).isoformat(),
        }

        out = json.dumps(signal_payload).encode()

        self._producer.produce(SCORED_TOPIC, value=out)
        self._producer.flush()  # ensure durability before committing

        # Bug-B FIX: commit only on success
        self._consumer.commit(message=msg, asynchronous=False)
        logger.info("signal_published", symbol=payload.get("symbol"), prob=active_prob)


async def main() -> None:
    # Start health HTTP server in a daemon thread
    health_server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    threading.Thread(target=health_server.serve_forever, name="health-http", daemon=True).start()
    logger.info("health_endpoint_started", port=HEALTH_PORT)

    svc = SignalEngineService()
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
