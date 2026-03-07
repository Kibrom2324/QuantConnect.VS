"""
APEX Risk Engine — services/risk_engine/main.py

Runs two concurrent tasks:
  1. FastAPI HTTP server on PORT (default 8004) — health, metrics, readiness
  2. Kafka consumer loop — evaluate risk, publish approved/rejected decisions

Prometheus metrics are exposed at GET /metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import redis as _redis_lib
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    generate_latest,
)

logger = structlog.get_logger(__name__)
_pylogger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# ─── Config ─────────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP     = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
RISK_INPUT_TOPIC    = os.environ.get("RISK_INPUT_TOPIC",    "apex.signals.scored")
RISK_APPROVED_TOPIC = os.environ.get("RISK_APPROVED_TOPIC", "apex.risk.approved")
REDIS_URL           = os.environ.get("REDIS_URL",           "redis://localhost:6379/0")
HTTP_PORT           = int(os.environ.get("PORT", 8004))

# ─── Prometheus metrics ──────────────────────────────────────────────────────

risk_checks_total = Counter(
    "apex_risk_checks_total",
    "Total risk checks performed",
    ["result"],  # passed / rejected
)
risk_rejection_reasons = Counter(
    "apex_risk_rejections_total",
    "Risk rejections by reason",
    ["reason"],  # size_limit / daily_limit / kill_switch / drawdown
)
portfolio_drawdown = Gauge(
    "apex_portfolio_drawdown_pct",
    "Current portfolio drawdown percentage",
)
risk_engine_healthy = Gauge(
    "apex_risk_engine_healthy",
    "1 if healthy, 0 if not",
)
position_count = Gauge(
    "apex_open_positions",
    "Number of open positions",
)
daily_trades = Gauge(
    "apex_daily_trades_count",
    "Trades executed today",
)

# ─── HTTP app ────────────────────────────────────────────────────────────────

http_app = FastAPI(title="APEX Risk Engine", version="1.0.0")


def _redis_client() -> _redis_lib.Redis:
    host = os.getenv("REDIS_HOST", "redis")
    return _redis_lib.Redis(host=host, port=6379, socket_timeout=2, decode_responses=True)


@http_app.on_event("startup")
async def _http_startup() -> None:
    try:
        _redis_client().ping()
        risk_engine_healthy.set(1)
    except Exception:
        risk_engine_healthy.set(0)


@http_app.get("/health")
async def health():
    return {
        "status":    "healthy",
        "service":   "risk_engine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version":   os.getenv("SERVICE_VERSION", "1.0.0"),
    }


@http_app.get("/ready")
async def ready():
    try:
        _redis_client().ping()
        risk_engine_healthy.set(1)
        return {"ready": True}
    except Exception as exc:
        risk_engine_healthy.set(0)
        raise HTTPException(status_code=503, detail=str(exc))


@http_app.get("/metrics")
async def metrics():
    # Refresh position count from Redis before scrape
    try:
        r   = _redis_client()
        raw = r.get("apex:portfolio:state")
        if raw:
            ps = json.loads(raw)
            position_count.set(ps.get("open_positions", 0))
            portfolio_drawdown.set(float(ps.get("drawdown_pct", 0)))
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@http_app.get("/status")
async def status():
    try:
        r   = _redis_client()
        ks  = r.get("apex:kill_switch") or "0"
        raw = r.get("apex:portfolio:state")
        ps  = json.loads(raw) if raw else {}
        return {
            "kill_switch":      ks == "1",
            "open_positions":   ps.get("open_positions", 0),
            "daily_pnl_pct":    ps.get("daily_pnl_pct", 0),
            "drawdown_pct":     ps.get("drawdown_pct", 0),
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


async def kafka_consumer_loop() -> None:
    """Kafka consumer: consume → evaluate → publish.  Never raises."""
    try:
        from services.risk_engine.engine import RiskEngine
    except ImportError:
        logger.warning("RiskEngine not importable — Kafka loop disabled")
        return

    engine = RiskEngine(redis_url=REDIS_URL)
    await engine.start()    # CF-6 FIX: connect Redis, load kill-switch, set trading_enabled=True
    logger.info("risk_engine_kafka_started", topic=RISK_INPUT_TOPIC)

    try:
        from confluent_kafka import Consumer, KafkaError, Producer
    except ImportError:
        logger.warning("confluent_kafka not installed — Kafka loop disabled")
        return

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": "apex-risk-engine-v1",
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })
    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "acks": "all"})
    consumer.subscribe([RISK_INPUT_TOPIC])

    shutdown = False

    def _on_signal(*_: object) -> None:
        nonlocal shutdown
        shutdown = True

    import signal as _sig
    _sig.signal(_sig.SIGTERM, _on_signal)
    _sig.signal(_sig.SIGINT,  _on_signal)

    while not shutdown:
        msg = await asyncio.get_event_loop().run_in_executor(
            None, lambda: consumer.poll(timeout=1.0)
        )
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                logger.error("kafka_error", error=str(msg.error()))
            continue

        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("bad_json", error=str(exc))
            consumer.commit(message=msg, asynchronous=False)
            continue

        # Extract fields from the scored signal payload
        symbol      = payload.get("symbol", "UNKNOWN")
        signal_side = payload.get("side", "BUY")
        probability = float(payload.get("probability", 0.5))

        # Conviction-scaled position sizing:
        #   base_qty (PAPER_ORDER_QTY, default 1 share) × [1..3] conviction multiplier
        #   conviction=0 → qty=base; conviction=1 → qty=base*3
        base_qty   = float(os.environ.get("PAPER_ORDER_QTY", "1"))
        conviction = min(1.0, abs(probability - 0.5) * 2.0)   # normalised [0,1]
        quantity   = max(1, round(base_qty * (1.0 + conviction * 2.0)))

        portfolio_value = float(os.environ.get("PAPER_PORTFOLIO_VALUE", "100000"))

        decision = await engine.evaluate(
            symbol=symbol,
            signal_side=signal_side,
            quantity=quantity,
            portfolio_value=portfolio_value,
        )

        # Prometheus counters
        if decision.approved:
            risk_checks_total.labels(result="passed").inc()
        else:
            risk_checks_total.labels(result="rejected").inc()
            risk_rejection_reasons.labels(
                reason=decision.reason or "unknown"
            ).inc()

        result = json.dumps({
            **payload,             # Phase 0: forward ALL original signal fields (lineage)
            "approved":           decision.approved,
            "reason":             decision.reason,
            "symbol":             symbol,
            "score":              probability,
            "side":               signal_side,
            "quantity":           quantity,
            # Lineage: ensure critical fields survive even if upstream omitted them
            "signal_id":          payload.get("signal_id", ""),
            "prediction_ids":     payload.get("prediction_ids", []),
            "calibrated_prob":    payload.get("calibrated_prob", probability),
            "feature_version":    payload.get("feature_version", "legacy"),
            # Veto context from risk evaluation
            "veto_metadata":      decision.metadata if not decision.approved else {},
        }).encode()

        producer.produce(RISK_APPROVED_TOPIC, value=result)
        producer.flush()
        consumer.commit(message=msg, asynchronous=False)

    consumer.close()
    await engine.stop()
    logger.info("risk_engine_kafka_stopped")


async def run() -> None:
    """
    Launch the HTTP server (FastAPI) and Kafka consumer loop concurrently.
    The HTTP server is always available even if Kafka is down.
    """
    config = uvicorn.Config(
        http_app,
        host="0.0.0.0",
        port=HTTP_PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        kafka_consumer_loop(),
    )


if __name__ == "__main__":
    asyncio.run(run())
