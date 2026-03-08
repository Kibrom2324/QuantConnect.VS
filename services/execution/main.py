"""
APEX Execution Agent — services/execution/main.py

Fixes implemented in this file
───────────────────────────────
  CF-7   Kafka commit race: producer.flush() BEFORE consumer.commit().
         Previously consumer.commit() could ack the signal before the order
         was actually flushed to the downstream order-result topic, creating
         a window where a crash would silently drop orders.

  CF-8   Alpaca HTTP timeout: httpx.Timeout(30.0) explicit end-to-end timeout.
         Previously no timeout was set, so a hung Alpaca API call would block
         the event loop indefinitely, starving all other coroutines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal as _signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from shared.contracts.schemas import DecisionRecord, generate_decision_id

import httpx
import structlog
import websockets
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from services.execution.dead_letter_queue import DeadLetterQueue
from services.graceful_shutdown import GracefulShutdown
from shared.core.env import optional_env, require_env

logger = structlog.get_logger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────
# Non-secret config: optional with safe defaults

KAFKA_BOOTSTRAP    = optional_env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SIGNAL_TOPIC       = optional_env("EXECUTION_SIGNAL_TOPIC",  "apex.signals.approved")
ORDER_RESULT_TOPIC = optional_env("EXECUTION_RESULT_TOPIC", "apex.orders.results")
ALPACA_BASE_URL    = optional_env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
GROUP_ID           = optional_env("EXECUTION_GROUP_ID", "apex-execution-v1")

# Phase 0: decision record feature flag
ENABLE_DECISION_RECORDS: bool = (
    os.environ.get("ENABLE_DECISION_RECORDS", "false").lower() == "true"
)

# Trading guard — execution engine will consume but NOT submit orders unless True
TRADING_ENABLED: bool = (
    os.environ.get("TRADING_ENABLED", "false").lower() == "true"
)

# Position risk controls
MAX_OPEN_POSITIONS = int(optional_env("MAX_OPEN_POSITIONS", "15"))   # max concurrent long+short positions
PAPER_ORDER_QTY    = float(optional_env("PAPER_ORDER_QTY", "1"))      # shares per order (override for larger sizing)
HEALTH_PORT        = int(optional_env("HEALTH_PORT", "8006"))          # liveness probe port


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal /health endpoint for liveness probes."""

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            body = json.dumps({
                "status": "healthy",
                "service": "execution_engine",
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

# Secrets: populated at module level from env; validated in ExecutionAgent.__init__
# (module-level optional_env allows clean import in tests — assertion at startup)
ALPACA_KEY_ID = optional_env("ALPACA_API_KEY",    "")
ALPACA_SECRET = optional_env("ALPACA_SECRET_KEY", "")

# CF-8 FIX 2026-02-27: explicit 30-second end-to-end timeout for all Alpaca calls
ALPACA_TIMEOUT = httpx.Timeout(30.0)

# Alpaca Trade Updates WebSocket URL (paper vs live auto-detected from base URL)
_WS_URL = (
    "wss://paper-api.alpaca.markets/stream"
    if "paper" in ALPACA_BASE_URL
    else "wss://api.alpaca.markets/stream"
)


# ─── Order dataclass ───────────────────────────────────────────────────────

@dataclass
class Order:
    symbol: str
    side:   str    # "buy" | "sell"
    qty:    float
    order_type: str = "market"
    time_in_force: str = "day"


@dataclass
class OrderResult:
    order_id: str
    symbol:   str
    side:     str
    qty:      float
    status:   str
    filled_at: str | None = None
    error:    str | None  = None


# ─── Alpaca Trade Updates WebSocket stream ───────────────────────────────────

class AlpacaTradeStream:
    """
    Persistent WebSocket connection to Alpaca's trade_updates stream.

    Maintains a live ``_positions`` dict (symbol → qty) that is updated on
    every fill/partial_fill event using the ``position_qty`` field returned
    by Alpaca.  This eliminates the need to REST-poll /v2/positions and gives
    sub-second position state with no rate-limit risk.

    Usage::

        stream = AlpacaTradeStream()
        await stream.start(seed={"AAPL": 5.0})   # seed from REST snapshot
        print(stream.position_count)              # always current, no REST
        await stream.stop()
    """

    def __init__(self) -> None:
        self._positions: dict[str, float] = {}   # symbol → current qty
        self._ready     = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def position_count(self) -> int:
        """Live count of open positions. No network call."""
        return len(self._positions)

    async def start(self, seed: dict[str, float] | None = None) -> None:
        """Seed initial positions then open the WebSocket listener."""
        if seed:
            self._positions = dict(seed)
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            logger.warning("alpaca_trade_stream_connect_timeout_using_seed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── internal ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Connect → auth → subscribe → process events.  Reconnects on any error."""
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    # 1. Authenticate
                    await ws.send(json.dumps({
                        "action": "auth",
                        "key":    ALPACA_KEY_ID,
                        "secret": ALPACA_SECRET,
                    }))
                    auth_raw = await ws.recv()
                    auth_msg = json.loads(auth_raw)
                    status   = auth_msg.get("data", {}).get("status", "")
                    if status != "authorized":
                        logger.error("alpaca_ws_auth_failed", response=auth_msg)
                        # Back off and retry — bad keys won't get better by hammering
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60.0)
                        continue

                    # 2. Subscribe to trade_updates
                    await ws.send(json.dumps({
                        "action": "listen",
                        "data":   {"streams": ["trade_updates"]},
                    }))

                    self._ready.set()
                    backoff = 1.0
                    logger.info("alpaca_trade_stream_connected",
                                positions_seeded=len(self._positions))

                    # 3. Event loop
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("stream") == "trade_updates":
                            self._handle_update(msg["data"])

            except asyncio.CancelledError:
                logger.info("alpaca_trade_stream_stopped")
                return
            except Exception as exc:
                logger.warning(
                    "alpaca_trade_stream_disconnected",
                    error=str(exc),
                    reconnect_in_s=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _handle_update(self, data: dict) -> None:
        event  = data.get("event", "")
        order  = data.get("order", {})
        symbol = order.get("symbol", "")
        if not symbol or event not in ("fill", "partial_fill"):
            return

        # position_qty is the resulting position size after this fill
        try:
            qty = float(data.get("position_qty", 0))
        except (TypeError, ValueError):
            qty = 0.0

        if qty != 0.0:
            self._positions[symbol] = qty
        else:
            self._positions.pop(symbol, None)

        logger.debug(
            "trade_update",
            event=event,
            symbol=symbol,
            position_qty=qty,
            open_positions=len(self._positions),
        )


# ─── Alpaca broker client ──────────────────────────────────────────────────

class AlpacaBroker:
    """
    Thin async wrapper around the Alpaca REST v2 API.
    CF-8 FIX: every request uses ALPACA_TIMEOUT (30 s).
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=ALPACA_BASE_URL,
            headers={
                "APCA-API-KEY-ID":     ALPACA_KEY_ID,
                "APCA-API-SECRET-KEY": ALPACA_SECRET,
                "Content-Type":        "application/json",
            },
            # CF-8 FIX 2026-02-27: 30-second timeout prevents event-loop starvation
            timeout=ALPACA_TIMEOUT,
        )
        self._stream = AlpacaTradeStream()

    @property
    def position_count(self) -> int:
        """Live position count from the WebSocket stream (no REST call)."""
        return self._stream.position_count

    async def start_stream(self) -> None:
        """
        One-time REST seed of existing positions, then hands off to WebSocket.
        Call this once before the main processing loop.
        """
        seed: dict[str, float] = {}
        try:
            resp = await self._client.get("/v2/positions")
            resp.raise_for_status()
            for pos in resp.json():
                sym = pos.get("symbol", "")
                qty = float(pos.get("qty", 0))
                if sym and qty:
                    seed[sym] = qty
            logger.info("positions_seeded_from_rest", count=len(seed))
        except Exception as exc:
            logger.warning("position_seed_failed_starting_empty", error=str(exc))
        await self._stream.start(seed=seed)

    async def submit_order(self, order: Order) -> OrderResult:
        payload = {
            "symbol":        order.symbol,
            "qty":           str(order.qty),
            "side":          order.side,
            "type":          order.order_type,
            "time_in_force": order.time_in_force,
        }
        try:
            resp = await self._client.post("/v2/orders", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(
                order_id  = data.get("id", "UNKNOWN"),
                symbol    = order.symbol,
                side      = order.side,
                qty       = order.qty,
                status    = data.get("status", "unknown"),
                filled_at = data.get("filled_at"),
            )
        except httpx.TimeoutException as e:
            # CF-8: timeout is expected; log and propagate so DLQ handles it
            logger.error("alpaca_timeout", symbol=order.symbol, error=str(e))
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "alpaca_http_error",
                symbol=order.symbol,
                status=e.response.status_code,
                body=e.response.text,
            )
            raise

    async def aclose(self) -> None:
        await self._stream.stop()
        await self._client.aclose()


# ─── Execution Agent ──────────────────────────────────────────────────────

class ExecutionAgent:
    """
    Consumes risk-approved signals from Kafka, submits orders to Alpaca,
    and publishes order results back to Kafka.

    CF-7 FIX: producer.flush() is called before consumer.commit().
    CF-8 FIX: Alpaca HTTP client has 30-second timeout.
    """

    def __init__(self) -> None:
        from shared.core.env import assert_secrets_present
        assert_secrets_present(["ALPACA_API_KEY", "ALPACA_SECRET_KEY"])
        self._broker   = AlpacaBroker()
        self._dlq      = DeadLetterQueue()
        self._shutdown = GracefulShutdown()
        self._last_order_ts: float = 0.0   # rate-limit: min gap between orders
        self._db_pool = None  # Phase 0: asyncpg pool, lazily initialized

        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
        })

        self._consumer = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           GROUP_ID,
            "auto.offset.reset":  "latest",
            # CF-7 note: manual commit is used, so auto-commit must be off
            "enable.auto.commit": False,
        })

    async def _ensure_db_pool(self) -> None:
        """Lazily initialize asyncpg connection pool for decision records."""
        if self._db_pool is not None or not ENABLE_DECISION_RECORDS:
            return
        try:
            import asyncpg
            dsn = os.environ.get(
                "TIMESCALEDB_DSN",
                "postgresql://apex:apex@localhost:5432/apex",
            )
            self._db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
            logger.info("decision_record_db_pool_created")
        except Exception as exc:
            logger.warning("decision_record_db_pool_failed", error=str(exc))
            self._db_pool = None

    async def _persist_decision(
        self,
        symbol: str,
        side: str,
        action: str,
        reason: str,
        payload: dict,
        order_id: str | None = None,
    ) -> None:
        """Write a decision record to TimescaleDB (best-effort, never blocks trading).

        Uses the canonical DecisionRecord contract from shared/contracts/schemas.py
        and inserts into the decision_records table defined in lineage_migration.sql.
        """
        if not ENABLE_DECISION_RECORDS or self._db_pool is None:
            return
        try:
            record = DecisionRecord(
                decision_id=generate_decision_id(),
                signal_id=payload.get("signal_id", ""),
                prediction_ids=payload.get("prediction_ids", []),
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                action=action,
                direction=1 if side == "buy" else -1,
                calibrated_prob=float(payload.get("calibrated_prob", payload.get("probability", 0.0)) or 0.0),
                raw_edge_bps=float(payload.get("raw_edge_bps", 0.0) or 0.0),
                net_edge_bps=float(payload.get("net_edge_bps", 0.0) or 0.0),
                estimated_cost_bps=float(payload.get("estimated_cost_bps", 0.0) or 0.0),
                position_size_pct=float(payload.get("suggested_size_pct", 0.0) or 0.0),
                ood_score=float(payload.get("ood_score", 0.0) or 0.0),
                disagreement_score=float(payload.get("disagreement_score", 0.0) or 0.0),
                regime=int(payload.get("regime", 0) or 0),
                model_weights=payload.get("model_weights", {}),
                veto_reason=reason if action != "TRADED" else None,
                order_id=order_id,
            )
            await self._db_pool.execute(
                """
                INSERT INTO decision_records (
                    decision_id, signal_id, prediction_ids, symbol, timestamp,
                    direction, calibrated_prob, raw_edge_bps, net_edge_bps,
                    ood_score, disagreement_score, regime, model_weights,
                    recommended_size_pct, action, veto_reason,
                    feature_version, signal_process_version, order_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                """,
                record.decision_id,
                record.signal_id,
                record.prediction_ids,
                record.symbol,
                record.timestamp,
                record.direction,
                record.calibrated_prob,
                record.raw_edge_bps,
                record.net_edge_bps,
                record.ood_score,
                record.disagreement_score,
                record.regime,
                json.dumps(record.model_weights),
                record.position_size_pct,
                record.action,
                record.veto_reason,
                payload.get("feature_version", "legacy"),
                payload.get("signal_process_version", "v0.1"),
                record.order_id,
            )
        except Exception as exc:
            logger.debug("decision_record_write_failed", error=str(exc))

    async def run(self) -> None:
        # Phase 0: initialize DB pool for decision records
        await self._ensure_db_pool()

        # Seed positions from REST then keep them live via WebSocket
        await self._broker.start_stream()

        self._consumer.subscribe([SIGNAL_TOPIC])
        logger.info("execution_agent_started", topic=SIGNAL_TOPIC)

        while not self._shutdown.is_shutdown:
            msg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._consumer.poll(timeout=1.0)
            )

            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("kafka_consumer_error", error=str(msg.error()))
                continue

            await self._process_message(msg)

        await self._broker.aclose()
        self._consumer.close()
        logger.info("execution_agent_stopped")

    async def _process_message(self, msg: Any) -> None:
        """
        CF-7 FIX 2026-02-27: flush producer BEFORE committing consumer offset.

        Order of operations:
          1. Parse signal
          2. Submit order to Alpaca
          3. Produce order result to Kafka topic
          4. producer.flush()   ← CF-7: ensure result is durably written first
          5. consumer.commit()  ← CF-7: only then advance the consumer offset

        If we crash between step 4 and 5, the consumer will re-process the
        signal but will find the order already submitted (idempotency layer needed
        long-term).  Crucially, we will NOT silently lose the order result.
        """
        raw = msg.value().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("invalid_signal_json", error=str(e), raw=raw[:200])
            # Bad message: commit offset so we don't loop on it indefinitely
            self._consumer.commit(message=msg, asynchronous=False)
            return

        symbol = payload["symbol"]
        side   = payload["side"].lower()
        qty    = float(payload.get("quantity", PAPER_ORDER_QTY))

        # Safety gate: reject all orders when trading is disabled
        if not TRADING_ENABLED:
            logger.warning(
                "trading_disabled_skipping_order",
                symbol=symbol, side=side,
            )
            await self._persist_decision(
                symbol, side, "VETOED", "TRADING_ENABLED=false", payload,
            )
            self._consumer.commit(message=msg, asynchronous=False)
            return

        # Rate limit: minimum 2 seconds between any orders
        _ORDER_GAP = float(os.environ.get("ORDER_MIN_GAP_S", "2.0"))
        elapsed = time.monotonic() - self._last_order_ts
        if elapsed < _ORDER_GAP:
            await asyncio.sleep(_ORDER_GAP - elapsed)

        # Gate: don't open more than MAX_OPEN_POSITIONS concurrent positions
        # position_count is maintained live by the WebSocket stream (no REST call)
        if side == "buy":
            if self._broker.position_count >= MAX_OPEN_POSITIONS:
                logger.info(
                    "max_positions_reached_skipping_buy",
                    symbol=symbol,
                    open=self._broker.position_count,
                    max=MAX_OPEN_POSITIONS,
                )
                # Phase 0: record position-limit veto
                await self._persist_decision(
                    symbol, side, "VETOED", "max_positions_reached", payload,
                )
                self._consumer.commit(message=msg, asynchronous=False)
                return

        order = Order(
            symbol = symbol,
            side   = side,
            qty    = qty,
        )

        try:
            result = await self._broker.submit_order(order)
            self._last_order_ts = time.monotonic()   # update rate-limit timestamp
            # Note: position_count is now maintained by the WebSocket stream;
            # no manual cache increment needed — the fill event will update it.
            logger.info(
                "order_submitted",
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                order_id=result.order_id,
                status=result.status,
            )
        except Exception as e:
            logger.error("order_submission_failed", symbol=order.symbol, error=str(e))
            # Phase 0: record order failure
            await self._persist_decision(
                symbol, side, "FAILED", str(e), payload,
            )
            await self._dlq.send(topic=SIGNAL_TOPIC, message=raw, error=str(e))
            # DO NOT commit — leave the offset so ops can investigate
            return

        # Step 3: produce order result
        result_payload = json.dumps({
            "order_id":  result.order_id,
            "symbol":    result.symbol,
            "side":      result.side,
            "qty":       result.qty,
            "status":    result.status,
            "filled_at": result.filled_at,
            "ts":        datetime.now(timezone.utc).isoformat(),
        }).encode()

        self._producer.produce(ORDER_RESULT_TOPIC, value=result_payload)

        # CF-7 FIX: flush before commit
        self._producer.flush()                                 # ← CF-7 FIX
        self._consumer.commit(message=msg, asynchronous=False) # ← safe only after flush

        # Phase 0: record successful trade
        await self._persist_decision(
            symbol, side, "TRADED", "order_submitted", payload,
            order_id=result.order_id,
        )


# ─── Position Reconciler ─────────────────────────────────────────────────────

class PositionReconciler:
    """
    Compares Alpaca's live position state against our internal tracking dict.

    Spec:
      Every 60 seconds, call reconcile().
      If any position diverges by more than 1 share OR $50, halt trading
      and increment apex_position_mismatch_total.

    Attributes:
        SHARE_TOLERANCE   Max allowed difference in shares before mismatch
        VALUE_TOLERANCE   Max allowed difference in USD market value
        INTERVAL_SECONDS  How often to reconcile (default: 60s)
    """

    SHARE_TOLERANCE:  float = 1.0
    VALUE_TOLERANCE:  float = 50.0
    INTERVAL_SECONDS: float = 60.0

    def __init__(
        self,
        alpaca_client: httpx.AsyncClient,
        alpaca_base_url: str,
        alpaca_key: str,
        alpaca_secret: str,
    ) -> None:
        self._client     = alpaca_client
        self._base_url   = alpaca_base_url.rstrip("/")
        self._key        = alpaca_key
        self._secret     = alpaca_secret
        self._halted     = False
        self._internal:  dict[str, dict] = {}     # symbol → {qty, market_value}

    def update_internal(self, symbol: str, qty: float, market_value: float = 0.0) -> None:
        """Call this after every order fill to keep internal state current."""
        if qty == 0:
            self._internal.pop(symbol, None)
        else:
            self._internal[symbol] = {"qty": qty, "market_value": market_value}

    async def _fetch_alpaca_positions(self) -> list[dict]:
        """Fetch all current positions from Alpaca REST API."""
        headers = {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }
        resp = await self._client.get(
            f"{self._base_url}/v2/positions",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def reconcile(self) -> list[dict]:
        """
        Compare Alpaca positions against internal state.

        Returns a list of mismatch dicts (empty if all positions agree).
        Side effect: sets self._halted = True and increments Prometheus
        counter if any mismatch exceeds tolerances.
        """
        try:
            alpaca_positions = await self._fetch_alpaca_positions()
        except Exception as exc:
            logger.error("reconcile_fetch_failed", error=str(exc))
            return []

        # Build lookup: symbol → {qty, market_value}
        alpaca_map: dict[str, dict] = {}
        for pos in alpaca_positions:
            sym = pos.get("symbol", "")
            alpaca_map[sym] = {
                "qty": float(pos.get("qty", 0)),
                "market_value": float(pos.get("market_value", 0)),
            }

        mismatches: list[dict] = []

        # Check all symbols appearing in either set
        all_symbols = set(self._internal) | set(alpaca_map)
        for sym in all_symbols:
            internal = self._internal.get(sym, {"qty": 0.0, "market_value": 0.0})
            live     = alpaca_map.get(sym, {"qty": 0.0, "market_value": 0.0})

            share_diff = abs(internal["qty"] - live["qty"])
            value_diff = abs(internal["market_value"] - live["market_value"])

            if share_diff > self.SHARE_TOLERANCE or value_diff > self.VALUE_TOLERANCE:
                mismatch = {
                    "symbol": sym,
                    "internal_qty": internal["qty"],
                    "live_qty": live["qty"],
                    "share_diff": share_diff,
                    "value_diff": value_diff,
                }
                mismatches.append(mismatch)
                logger.critical(
                    "position_mismatch_detected",
                    **mismatch,
                )

        if mismatches:
            self._halted = True
            # Emit Prometheus metric
            try:
                import sys as _sys  # noqa: PLC0415
                from pathlib import Path as _Path  # noqa: PLC0415
                _sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
                from shared.core.metrics import POSITION_MISMATCH  # noqa: PLC0415
                POSITION_MISMATCH.inc(len(mismatches))
            except Exception:  # noqa: BLE001
                pass  # metrics are best-effort — never let them crash reconciliation

        return mismatches

    @property
    def is_halted(self) -> bool:
        return self._halted

    async def run_loop(self) -> None:
        """Periodic reconciliation loop.  Run as asyncio.create_task()."""
        logger.info("reconciler_started", interval_s=self.INTERVAL_SECONDS)
        while True:
            await asyncio.sleep(self.INTERVAL_SECONDS)
            mismatches = await self.reconcile()
            if mismatches:
                logger.critical(
                    "trading_halted_due_to_position_mismatch",
                    count=len(mismatches),
                )
                break  # Halt the reconciliation loop — operator must intervene


# ─── Entrypoint ──────────────────────────────────────────────────────────────

async def main() -> None:
    # Start health HTTP server in a daemon thread
    health_server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    threading.Thread(target=health_server.serve_forever, name="health-http", daemon=True).start()
    logger.info("health_endpoint_started", port=HEALTH_PORT)

    agent = ExecutionAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
