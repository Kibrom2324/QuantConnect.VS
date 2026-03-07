"""
APEX Exit Monitor — services/exit_monitor/main.py

Monitors open positions against stop-loss and take-profit thresholds.
Consumes live bar events from `market.raw`, compares to position entry
prices stored in Redis, and emits exit orders to `apex.risk.approved`
(bypassing signal scoring — exits are always approved).

Stop-loss / take-profit are read from `configs/app.yaml` at startup and
can be overridden per-position via Redis hash fields.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml
import structlog

logger = structlog.get_logger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP   = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
BAR_TOPIC         = os.environ.get("EXIT_BAR_TOPIC",    "market.raw")
EXIT_TOPIC        = os.environ.get("EXIT_ORDER_TOPIC",  "apex.risk.approved")
GROUP_ID          = os.environ.get("EXIT_GROUP_ID",     "apex-exit-monitor-v1")
REDIS_URL         = os.environ.get("REDIS_URL",         "redis://localhost:6379/0")
CONFIG_PATH       = os.environ.get(
    "APEX_CONFIG_PATH",
    str(__import__("pathlib").Path(__file__).parent.parent.parent / "configs" / "app.yaml"),
)


@dataclass
class PositionEntry:
    symbol:     str
    side:       str    # "LONG" | "SHORT"
    entry_price: float
    quantity:   float
    stop_loss_pct:    float  # e.g. 0.02 = 2%
    take_profit_pct:  float  # e.g. 0.04 = 4%


def _load_default_thresholds() -> tuple[float, float]:
    """Load default SL/TP from configs/app.yaml."""
    try:
        with open(CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh)
        risk = cfg.get("risk", {})
        sl = float(risk.get("stop_loss_pct", 0.02))
        tp = float(risk.get("take_profit_pct", 0.04))
        return sl, tp
    except Exception as exc:
        logger.warning("config_load_failed", error=str(exc))
        return 0.02, 0.04  # safe defaults


def _should_exit(
    bar_close: float,
    position: PositionEntry,
) -> tuple[bool, str]:
    """
    Returns (should_exit, reason).
    Checks stop-loss and take-profit for both LONG and SHORT.
    """
    entry = position.entry_price
    if entry <= 0:
        return False, ""

    pnl_pct = (bar_close - entry) / entry  # positive = price went up

    if position.side == "LONG":
        if pnl_pct <= -position.stop_loss_pct:
            return True, f"stop_loss pnl={pnl_pct:.4f}"
        if pnl_pct >= position.take_profit_pct:
            return True, f"take_profit pnl={pnl_pct:.4f}"
    else:  # SHORT
        short_pnl = -pnl_pct
        if short_pnl <= -position.stop_loss_pct:
            return True, f"stop_loss pnl={short_pnl:.4f}"
        if short_pnl >= position.take_profit_pct:
            return True, f"take_profit pnl={short_pnl:.4f}"

    return False, ""


async def _load_positions_from_redis(redis_client: Any) -> dict[str, PositionEntry]:
    """Load all open positions from Redis hash `apex:positions`."""
    raw = await redis_client.hgetall("apex:positions")
    default_sl, default_tp = _load_default_thresholds()
    positions: dict[str, PositionEntry] = {}
    for symbol_bytes, data_bytes in raw.items():
        symbol = symbol_bytes.decode() if isinstance(symbol_bytes, bytes) else symbol_bytes
        try:
            d = json.loads(data_bytes)
            positions[symbol] = PositionEntry(
                symbol=symbol,
                side=d.get("side", "LONG"),
                entry_price=float(d.get("avg_price", 0)),
                quantity=float(d.get("quantity", 0)),
                stop_loss_pct=float(d.get("stop_loss_pct", default_sl)),
                take_profit_pct=float(d.get("take_profit_pct", default_tp)),
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("position_parse_failed", symbol=symbol, error=str(exc))
    return positions


async def run() -> None:
    """Main exit monitor loop: consume bars → check SL/TP → emit exits."""
    try:
        from confluent_kafka import Consumer, Producer, KafkaError
        import redis.asyncio as aioredis
    except ImportError as exc:
        logger.error("missing_dependency", error=str(exc))
        return

    redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id":           GROUP_ID,
        "auto.offset.reset":  "latest",
        "enable.auto.commit": False,
    })
    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "acks": "all"})
    consumer.subscribe([BAR_TOPIC])

    shutdown = False

    def _on_signal(*_: Any) -> None:
        nonlocal shutdown
        shutdown = True

    import signal as _sig
    _sig.signal(_sig.SIGTERM, _on_signal)
    _sig.signal(_sig.SIGINT, _on_signal)

    logger.info("exit_monitor_started", topic=BAR_TOPIC)

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
            bar = json.loads(msg.value().decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("bad_json", error=str(exc))
            consumer.commit(message=msg, asynchronous=False)
            continue

        symbol = bar["symbol"]
        close  = float(bar["close"])

        positions = await _load_positions_from_redis(redis_client)
        position  = positions.get(symbol)

        if position is not None:
            should_exit, reason = _should_exit(close, position)
            if should_exit:
                exit_order = json.dumps({
                    "symbol":   symbol,
                    "side":     "sell" if position.side == "LONG" else "buy",
                    "quantity": position.quantity,
                    "reason":   reason,
                    "ts":       datetime.now(timezone.utc).isoformat(),
                    "source":   "exit_monitor",
                }).encode()
                producer.produce(EXIT_TOPIC, key=symbol.encode(), value=exit_order)
                logger.info(
                    "exit_triggered",
                    symbol=symbol, reason=reason,
                    entry=position.entry_price, bar_close=close,
                )

        producer.flush()
        consumer.commit(message=msg, asynchronous=False)

    consumer.close()
    await redis_client.aclose()
    logger.info("exit_monitor_stopped")


if __name__ == "__main__":
    asyncio.run(run())
