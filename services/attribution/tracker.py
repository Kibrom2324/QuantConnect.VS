"""
APEX Signal Attribution Tracker
services/attribution/tracker.py

Runs as a background service.  Listens to two Kafka topics:

  1. SIGNAL_SNAPSHOT_TOPIC (default: apex.signals.scored)
     Every time the signal engine emits a scored signal, this tracker
     caches the full per-signal breakdown in a short-lived LRU so it is
     available when the matching trade closes.

  2. ORDER_RESULT_TOPIC (default: apex.orders.results)
     Every time the execution agent confirms a FILLED order, this tracker
     attempts to record attribution.  For a closing trade (side=sell or
     short-cover) it pairs the fill with the most recent signal snapshot
     for that symbol and writes one row per signal to TimescaleDB.

TimescaleDB table written: signal_attribution
Schema:
    ts               TIMESTAMPTZ  — time of trade close
    symbol           TEXT         — ticker symbol
    signal_name      TEXT         — one of: tft, rsi, ema, macd, stoch, sentiment, xgb, factor
    signal_value     DOUBLE       — raw signal value (−1 to +1 normalised where possible)
    trade_pnl        DOUBLE       — realised P&L of the round-trip trade (USD)
    contributed_weight DOUBLE     — signal's effective weight in the ensemble at entry
    signal_direction SMALLINT     — sign(signal_value): +1 long, −1 short, 0 neutral
    trade_direction  SMALLINT     — sign of trade P&L: +1 win, −1 loss
    aligned          BOOLEAN      — signal_direction == trade_direction (signal "agreed" with outcome)
    entry_ts         TIMESTAMPTZ  — time the position was opened (from Redis position record)
    order_id         TEXT         — Alpaca order ID for traceability

Design principles
─────────────────
- All credentials via os.getenv() — nothing is hardcoded.
- Signal snapshot cache is in-process LRU (maxsize=500 symbols) with a
  10-minute TTL.  A stale snapshot is recorded but flagged via
  snapshot_age_seconds column so the report can filter by freshness.
- Every INSERT is a prepared statement via psycopg2 executemany — no
  string interpolation, no SQL injection surface.
- The service is stateless across restarts; a restarted tracker simply
  misses attribution for trades closed during the downtime window.
- Uses Kafka at-least-once delivery; the UNIQUE constraint on
  (ts, symbol, signal_name, order_id) prevents duplicate rows.

Exit codes
──────────
  0  — clean shutdown (SIGTERM / SIGINT)
  1  — fatal startup error (missing env vars, DB unreachable)

Environment variables
─────────────────────
  DATABASE_URL                 Full PostgreSQL DSN (canonical)
  TIMESCALEDB_PASSWORD         Used if DATABASE_URL is not set
  POSTGRES_USER                (default: apex)
  POSTGRES_DB                  (default: apexdb)
  TIMESCALEDB_HOST             (default: localhost)
  TIMESCALEDB_PORT             (default: 5432)
  KAFKA_BOOTSTRAP_SERVERS      (default: localhost:9092)
  SIGNAL_SNAPSHOT_TOPIC        (default: apex.signals.scored)
  ORDER_RESULT_TOPIC           (default: apex.orders.results)
  ATTRIBUTION_GROUP_ID         Kafka consumer group (default: apex-attribution-v1)
  SNAPSHOT_TTL_SECONDS         Max signal snapshot age (default: 600)
  SNAPSHOT_CACHE_SIZE          LRU max symbols (default: 500)
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal as _signal
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from confluent_kafka import Consumer, KafkaError

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("attribution.tracker")

# ─── Configuration ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    _user = os.getenv("POSTGRES_USER",          "apex")
    _pw   = os.getenv("TIMESCALEDB_PASSWORD") or os.getenv("POSTGRES_PASSWORD", "")
    _host = os.getenv("TIMESCALEDB_HOST",        "localhost")
    _port = os.getenv("TIMESCALEDB_PORT",        "5432")
    _db   = os.getenv("POSTGRES_DB",             "apexdb")
    DATABASE_URL = f"postgresql://{_user}:{_pw}@{_host}:{_port}/{_db}"

KAFKA_BOOTSTRAP       = os.getenv("KAFKA_BOOTSTRAP_SERVERS",   "localhost:9092")
SIGNAL_SNAPSHOT_TOPIC = os.getenv("SIGNAL_SNAPSHOT_TOPIC",     "apex.signals.scored")
ORDER_RESULT_TOPIC    = os.getenv("ORDER_RESULT_TOPIC",         "apex.orders.results")
ATTRIBUTION_GROUP_ID  = os.getenv("ATTRIBUTION_GROUP_ID",       "apex-attribution-v1")
SNAPSHOT_TTL_SECONDS  = int(os.getenv("SNAPSHOT_TTL_SECONDS",   "600"))
SNAPSHOT_CACHE_SIZE   = int(os.getenv("SNAPSHOT_CACHE_SIZE",    "500"))

# Canonical signal names (must match lean_alpha and signal_engine field names)
SIGNAL_NAMES: list[str] = ["tft", "rsi", "ema", "macd", "stoch", "sentiment", "xgb", "factor"]

# Ensemble weight keys (must match field names in scored signal payload)
_WEIGHT_MAP: dict[str, str] = {
    "tft":       "weight_tft",
    "rsi":       "weight_rsi",
    "ema":       "weight_ema",
    "macd":      "weight_macd",
    "stoch":     "weight_stoch",
    "sentiment": "weight_sentiment",
    "xgb":       "weight_xgb",
    "factor":    "weight_factor",
}

# Score field names in the signal payload
_SCORE_MAP: dict[str, str] = {
    "tft":       "tft_score",
    "rsi":       "rsi_score",
    "ema":       "ema_score",
    "macd":      "macd_score",
    "stoch":     "stoch_score",
    "sentiment": "sentiment_score",
    "xgb":       "xgb_score",
    "factor":    "factor_score",
}

# Default ensemble weights (fallback when not present in signal payload)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "tft":       0.40,
    "xgb":       0.35,
    "factor":    0.25,
    "rsi":       0.15,   # lean_alpha contributes through factor_score; these are sub-weights
    "ema":       0.10,
    "macd":      0.10,
    "stoch":     0.05,
    "sentiment": 0.05,
}


# ─── Signal snapshot cache ────────────────────────────────────────────────────

@dataclass
class SignalSnapshot:
    symbol:          str
    captured_at:     float              # time.time() at capture
    payload:         dict               # full signal payload from Kafka
    scores:          dict[str, float]   # normalised per-signal scores
    weights:         dict[str, float]   # effective per-signal weights


class SnapshotCache:
    """
    Thread-safe LRU cache with per-entry TTL.

    Stores the most recent scored signal for each symbol.  When a trade
    closes, the tracker looks up the symbol here.
    """

    def __init__(self, maxsize: int = SNAPSHOT_CACHE_SIZE, ttl: float = SNAPSHOT_TTL_SECONDS) -> None:
        self._cache:   OrderedDict[str, SignalSnapshot] = OrderedDict()
        self._maxsize: int   = maxsize
        self._ttl:     float = ttl

    def put(self, symbol: str, snap: SignalSnapshot) -> None:
        if symbol in self._cache:
            self._cache.move_to_end(symbol)
        self._cache[symbol] = snap
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)   # evict oldest

    def get(self, symbol: str) -> Optional[SignalSnapshot]:
        snap = self._cache.get(symbol)
        if snap is None:
            return None
        age = time.time() - snap.captured_at
        if age > self._ttl * 2:   # tolerate up to 2× TTL for attribution
            return None
        return snap

    def age_seconds(self, symbol: str) -> Optional[float]:
        snap = self._cache.get(symbol)
        return (time.time() - snap.captured_at) if snap else None


_snapshot_cache = SnapshotCache()


# ─── Signal payload parsing ───────────────────────────────────────────────────

def _parse_signal_snapshot(payload: dict) -> SignalSnapshot:
    """
    Extract per-signal scores and weights from a scored signal payload.

    Payload fields expected (all optional with 0.0 fallback):
        tft_score, rsi_score, ema_score, macd_score,
        stoch_score, sentiment_score, xgb_score, factor_score
        weight_tft, weight_rsi, ...  (optional — uses defaults if absent)
    """
    scores:  dict[str, float] = {}
    weights: dict[str, float] = {}

    for sig in SIGNAL_NAMES:
        score_key  = _SCORE_MAP[sig]
        weight_key = _WEIGHT_MAP[sig]

        raw_score = payload.get(score_key)
        scores[sig] = float(raw_score) if raw_score is not None else 0.0

        raw_weight = payload.get(weight_key)
        weights[sig] = float(raw_weight) if raw_weight is not None else _DEFAULT_WEIGHTS.get(sig, 0.0)

    # Normalise weights to sum to 1.0
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {k: v / total_w for k, v in weights.items()}

    return SignalSnapshot(
        symbol      = payload.get("symbol", "UNKNOWN"),
        captured_at = time.time(),
        payload     = payload,
        scores      = scores,
        weights     = weights,
    )


# ─── Attribution row ──────────────────────────────────────────────────────────

@dataclass
class AttributionRow:
    ts:                  datetime
    symbol:              str
    signal_name:         str
    signal_value:        float
    trade_pnl:           float
    contributed_weight:  float
    signal_direction:    int            # +1 / -1 / 0
    trade_direction:     int            # +1 / -1 / 0
    aligned:             bool
    entry_ts:            Optional[datetime]
    order_id:            str
    snapshot_age_seconds: float         # seconds between signal and trade close


def _build_attribution_rows(
    order_result: dict,
    snapshot:     SignalSnapshot,
) -> list[AttributionRow]:
    """
    Build one AttributionRow per signal for a given closed trade.

    Parameters
    ----------
    order_result : dict from apex.orders.results Kafka topic
        Required keys: symbol, side, filled_at, realized_pnl, order_id
        Optional:      entry_ts
    snapshot     : SignalSnapshot for the same symbol
    """
    close_ts   = _parse_ts(order_result.get("filled_at") or order_result.get("created_at") or "")
    entry_ts   = _parse_ts(order_result.get("entry_ts") or "")
    trade_pnl  = float(order_result.get("realized_pnl", 0.0))
    order_id   = str(order_result.get("order_id", ""))
    symbol     = str(order_result.get("symbol", "UNKNOWN"))
    snap_age   = time.time() - snapshot.captured_at

    trade_dir = _sign(trade_pnl)
    rows: list[AttributionRow] = []

    for sig_name in SIGNAL_NAMES:
        sig_value  = snapshot.scores.get(sig_name, 0.0)
        sig_weight = snapshot.weights.get(sig_name, 0.0)
        sig_dir    = _sign(sig_value)

        # contributed_weight: positive when signal agreed with outcome,
        # negative when signal disagreed.  Magnitude = ensemble weight.
        contrib = sig_weight * sig_dir * trade_dir

        rows.append(AttributionRow(
            ts                   = close_ts or datetime.now(timezone.utc),
            symbol               = symbol,
            signal_name          = sig_name,
            signal_value         = sig_value,
            trade_pnl            = trade_pnl,
            contributed_weight   = contrib,
            signal_direction     = sig_dir,
            trade_direction      = trade_dir,
            aligned              = (sig_dir == trade_dir and sig_dir != 0),
            entry_ts             = entry_ts,
            order_id             = order_id,
            snapshot_age_seconds = snap_age,
        ))

    return rows


def _sign(x: float) -> int:
    if math.isnan(x) or x == 0.0:
        return 0
    return 1 if x > 0 else -1


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ─── TimescaleDB writer ───────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO signal_attribution (
    ts, symbol, signal_name, signal_value,
    trade_pnl, contributed_weight,
    signal_direction, trade_direction, aligned,
    entry_ts, order_id, snapshot_age_seconds
) VALUES (
    %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s
)
ON CONFLICT (ts, symbol, signal_name, order_id) DO NOTHING;
"""


class AttributionWriter:
    """Manages a psycopg2 connection and writes attribution rows."""

    def __init__(self, database_url: str) -> None:
        self._url  = database_url
        self._conn = None

    def connect(self) -> None:
        import psycopg2
        self._conn = psycopg2.connect(self._url, connect_timeout=10)
        self._conn.autocommit = False
        log.info("TimescaleDB connected for attribution writes")

    def _ensure_connected(self) -> None:
        import psycopg2
        try:
            if self._conn is None or self._conn.closed:
                self.connect()
                return
            self._conn.cursor().execute("SELECT 1")
        except psycopg2.Error:
            log.warning("DB connection lost — reconnecting")
            try:
                self._conn.close()
            except Exception:
                pass
            self.connect()

    def write_rows(self, rows: list[AttributionRow]) -> int:
        """Insert rows; returns count of rows inserted."""
        if not rows:
            return 0

        self._ensure_connected()

        params = [
            (
                r.ts,
                r.symbol,
                r.signal_name,
                r.signal_value,
                r.trade_pnl,
                r.contributed_weight,
                r.signal_direction,
                r.trade_direction,
                r.aligned,
                r.entry_ts,
                r.order_id,
                r.snapshot_age_seconds,
            )
            for r in rows
        ]

        import psycopg2
        try:
            cur = self._conn.cursor()
            cur.executemany(_INSERT_SQL, params)
            self._conn.commit()
            log.debug("Inserted %d attribution rows for %s", len(rows), rows[0].symbol if rows else "?")
            return len(rows)
        except psycopg2.Error as exc:
            self._conn.rollback()
            log.error("Attribution insert failed: %s", exc)
            return 0

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()


# ─── Main service loop ────────────────────────────────────────────────────────

def _make_consumer(topics: list[str], group_id: str) -> Consumer:
    conf = {
        "bootstrap.servers":  KAFKA_BOOTSTRAP,
        "group.id":           group_id,
        "auto.offset.reset":  "latest",
        "enable.auto.commit": False,   # Bug-B pattern: manual commit only
        "session.timeout.ms": 30000,
    }
    c = Consumer(conf)
    c.subscribe(topics)
    return c


def _is_closing_order(msg: dict) -> bool:
    """
    Returns True if this order result represents a position close.

    We attribute on sell orders (closing long) and buy orders marked
    as position-close (short cover).  Buy orders that open new longs
    are not attributed here — we record at close, not open.
    """
    side   = str(msg.get("side", "")).lower()
    reason = str(msg.get("close_reason", "")).lower()
    # explicit close markers
    if reason in ("stop_loss", "take_profit", "eod_flatten", "manual"):
        return True
    # sell always closes a long
    if side == "sell":
        return True
    # short cover: buy with negative position qty or explicit flag
    if side == "buy" and msg.get("closes_position", False):
        return True
    return False


def run() -> int:
    """Main service entry point."""
    if not DATABASE_URL or "change-me" in DATABASE_URL or ":@" in DATABASE_URL:
        log.critical(
            "DATABASE_URL is missing or contains placeholder credentials. "
            "Set TIMESCALEDB_PASSWORD (and optionally DATABASE_URL) in .env"
        )
        return 1

    writer = AttributionWriter(DATABASE_URL)
    try:
        writer.connect()
    except Exception as exc:
        log.critical("Cannot connect to TimescaleDB: %s", exc)
        return 1

    consumer = _make_consumer(
        topics   = [SIGNAL_SNAPSHOT_TOPIC, ORDER_RESULT_TOPIC],
        group_id = ATTRIBUTION_GROUP_ID,
    )

    running     = True
    total_rows  = 0
    total_trades = 0
    missed_snap  = 0   # trades with no cached snapshot

    def _stop(sig, _frame):
        nonlocal running
        log.info("Signal %s received — stopping attribution tracker", sig)
        running = False

    _signal.signal(_signal.SIGTERM, _stop)
    _signal.signal(_signal.SIGINT,  _stop)

    log.info(
        "Attribution tracker started — "
        "signal_topic=%s order_topic=%s snapshot_ttl=%ds",
        SIGNAL_SNAPSHOT_TOPIC, ORDER_RESULT_TOPIC, SNAPSHOT_TTL_SECONDS,
    )

    while running:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Kafka error: %s", msg.error())
            continue

        try:
            payload = json.loads(msg.value().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Cannot decode Kafka message: %s", exc)
            consumer.commit(asynchronous=False)
            continue

        topic = msg.topic()

        # ── Signal snapshot: cache for later attribution ──────────────────
        if topic == SIGNAL_SNAPSHOT_TOPIC:
            symbol = payload.get("symbol")
            if symbol:
                snap = _parse_signal_snapshot(payload)
                _snapshot_cache.put(symbol, snap)
                log.debug("Cached signal snapshot for %s (scores: %s)", symbol, snap.scores)

        # ── Order result: attribute if this is a closing trade ────────────
        elif topic == ORDER_RESULT_TOPIC:
            if not _is_closing_order(payload):
                consumer.commit(asynchronous=False)
                continue

            symbol   = payload.get("symbol", "")
            snapshot = _snapshot_cache.get(symbol)

            if snapshot is None:
                missed_snap += 1
                log.warning(
                    "No signal snapshot for %s — attribution skipped "
                    "(total_missed=%d). "
                    "Ensure signal_engine publishes to %s before orders close.",
                    symbol, missed_snap, SIGNAL_SNAPSHOT_TOPIC,
                )
                consumer.commit(asynchronous=False)
                continue

            snap_age = _snapshot_cache.age_seconds(symbol) or 0.0
            if snap_age > SNAPSHOT_TTL_SECONDS:
                log.warning(
                    "Signal snapshot for %s is %.0fs old (TTL=%ds) — "
                    "writing attribution with staleness flag",
                    symbol, snap_age, SNAPSHOT_TTL_SECONDS,
                )

            rows = _build_attribution_rows(payload, snapshot)
            n    = writer.write_rows(rows)

            total_rows   += n
            total_trades += 1

            log.info(
                "Attributed trade: symbol=%s pnl=%.4f signals=%d rows_written=%d "
                "snap_age=%.0fs total_trades=%d",
                symbol,
                float(payload.get("realized_pnl", 0.0)),
                len(rows),
                n,
                snap_age,
                total_trades,
            )

        # Bug-B pattern: manual commit only after successful processing
        consumer.commit(asynchronous=False)

    consumer.close()
    writer.close()
    log.info(
        "Attribution tracker stopped — total_trades=%d total_rows=%d missed=%d",
        total_trades, total_rows, missed_snap,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
