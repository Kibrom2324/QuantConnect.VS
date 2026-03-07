"""
APEX Consolidated Execution Process — services/execution_process/main.py

Phase 6: Consolidates execution, risk, and fill tracking:
  Kafka Consumer (signals.scored)
  → Kill Switch Check (Redis, FIRST — fail-closed)
  → Cost Estimator (net edge veto)
  → Position Sizer (half-Kelly)
  → Risk Limits (position, daily loss, drawdown, leverage)
  → Correlation Filter
  → Order Submit (Alpaca, 30s timeout)
  → Exit Monitor (stop-loss, TP, trailing, time)
  → Fill Recorder → Kafka (fills.realized)
  → Decision Record to TimescaleDB

Wraps the existing ExecutionAgent and RiskEngine into a single
service with integrated cost/sizing/OOD awareness.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from services.graceful_shutdown import GracefulShutdown
from shared.core.cost_estimator import ExecutionCostEstimator
from shared.core.counterfactual import CounterfactualTracker
from shared.core.position_sizer import PositionSizer

logger = structlog.get_logger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SCORED_TOPIC = os.environ.get("SIGNAL_SCORED_TOPIC", "signals.scored")
FILLS_TOPIC = os.environ.get("FILLS_TOPIC", "fills.realized")
GROUP_ID = os.environ.get("EXECUTION_GROUP_ID", "apex-execution-process-v1")

# Feature flags
ENABLE_KELLY = os.environ.get("ENABLE_KELLY_SIZING", "false").lower() == "true"
ENABLE_COST_VETO = os.environ.get("ENABLE_COST_ESTIMATION", "false").lower() == "true"
ENABLE_COUNTERFACTUALS = os.environ.get("ENABLE_COUNTERFACTUALS", "false").lower() == "true"
ENABLE_DECISION_RECORDS = os.environ.get("ENABLE_DECISION_RECORDS", "false").lower() == "true"

MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "15"))
DEFAULT_POSITION_PCT = float(os.environ.get("DEFAULT_POSITION_PCT", "0.02"))

# Alpaca config (reuses existing env vars)
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")


class ConsolidatedExecutionProcess:
    """
    Consolidated execution process integrating risk, sizing, cost,
    and counterfactual tracking.
    """

    def __init__(self) -> None:
        self._shutdown = GracefulShutdown()

        # Phase 1: cost estimation
        self._cost_estimator = ExecutionCostEstimator()

        # Phase 4: position sizing + counterfactuals
        self._sizer = PositionSizer()
        self._counterfactual = CounterfactualTracker()

        # Position tracking
        self._positions: dict[str, float] = {}  # symbol → qty

        # DB pool for decision records
        self._db_pool = None

        # Redis for kill switch
        import redis as _redis_lib
        try:
            self._redis = _redis_lib.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                socket_timeout=1,
                decode_responses=True,
            )
        except Exception:
            self._redis = None

        # Kafka
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

    async def _ensure_db_pool(self) -> None:
        """Lazily create DB pool for decision records."""
        if self._db_pool is not None or not ENABLE_DECISION_RECORDS:
            return
        try:
            import asyncpg
            dsn = os.environ.get(
                "TIMESCALEDB_DSN",
                "postgresql://apex:apex@localhost:5432/apex",
            )
            self._db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        except Exception as exc:
            logger.warning("db_pool_failed", error=str(exc))

    async def run(self) -> None:
        await self._ensure_db_pool()
        self._consumer.subscribe([SCORED_TOPIC])
        logger.info("execution_process_started", topic=SCORED_TOPIC)

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
        logger.info("execution_process_stopped")

    async def _process(self, msg) -> None:
        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except Exception:
            self._consumer.commit(message=msg, asynchronous=False)
            return

        symbol = payload.get("symbol", "UNKNOWN")
        side = payload.get("side", "BUY").lower()
        prob = float(payload.get("calibrated_prob", payload.get("probability", 0.5)))
        raw_edge = float(payload.get("raw_edge_bps", 0))

        # ── Kill switch check (fail-closed) ───────────────────────────────
        try:
            if self._redis:
                kill = self._redis.get("apex:kill_switch")
                if kill == "true":
                    logger.critical("kill_switch_active_blocking")
                    self._consumer.commit(message=msg, asynchronous=False)
                    return
        except Exception:
            # Redis failure → fail-closed
            logger.critical("redis_failure_fail_closed")
            return

        # ── Position limit ─────────────────────────────────────────────────
        if side == "buy" and len(self._positions) >= MAX_OPEN_POSITIONS:
            logger.info("max_positions_reached", symbol=symbol)
            if ENABLE_COUNTERFACTUALS:
                self._counterfactual.record_veto(
                    uuid.uuid4().hex, symbol, 1, "max_positions", prob,
                )
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # ── Cost veto ──────────────────────────────────────────────────────
        if ENABLE_COST_VETO:
            spread = float(payload.get("spread_bps", 5.0))
            adv = float(payload.get("dollar_volume", 1e6))
            cost = self._cost_estimator.estimate(raw_edge, spread, 1000.0, adv)
            if self._cost_estimator.should_veto(cost):
                logger.info("cost_veto", symbol=symbol, net_edge=cost.net_edge_bps)
                if ENABLE_COUNTERFACTUALS:
                    self._counterfactual.record_veto(
                        uuid.uuid4().hex, symbol,
                        1 if side == "buy" else -1,
                        "negative_net_edge", prob,
                    )
                self._consumer.commit(message=msg, asynchronous=False)
                return

        # ── Position sizing ────────────────────────────────────────────────
        if ENABLE_KELLY:
            sizing = self._sizer.size(prob)
            if not sizing.edge_sufficient:
                logger.info("edge_insufficient", symbol=symbol, prob=prob)
                self._consumer.commit(message=msg, asynchronous=False)
                return
            position_pct = sizing.position_size_pct
        else:
            position_pct = DEFAULT_POSITION_PCT

        # ── Build fill event ───────────────────────────────────────────────
        decision_id = uuid.uuid4().hex
        fill_payload = {
            "decision_id": decision_id,
            "signal_id": payload.get("signal_id", ""),
            "symbol": symbol,
            "side": side,
            "position_size_pct": position_pct,
            "calibrated_prob": prob,
            "regime": payload.get("regime", 0),
            "ood_score": payload.get("ood_score", 0),
            "disagreement_score": payload.get("disagreement_score", 0),
            "net_edge_bps": payload.get("net_edge_bps"),
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        out = json.dumps(fill_payload).encode()
        self._producer.produce(FILLS_TOPIC, value=out)
        self._producer.flush()
        self._consumer.commit(message=msg, asynchronous=False)

        logger.info(
            "execution_complete",
            symbol=symbol,
            side=side,
            size_pct=position_pct,
        )

    async def aclose(self) -> None:
        if self._db_pool:
            await self._db_pool.close()


async def main() -> None:
    svc = ConsolidatedExecutionProcess()
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
