"""
APEX Signal Generator  v1.0.0
==============================
Periodically queries the TimescaleDB `features` table for the latest
pre-computed technical features, converts them into a multi-factor score,
and publishes raw signal payloads to the `apex.signals.raw` Kafka topic.

The Signal Engine consumes these payloads, applies Platt calibration +
ensemble weighting (TFT/XGB/Factor/LLM), and publishes scored signals.

Factor scoring model
────────────────────
  factor_score = normalised weighted sum of:
    - Momentum component  : returns_1, returns_5, returns_15, returns_60
    - RSI component       : normalised rsi_14 (50 → 0, 0/100 → ±1)
    - MACD component      : macd_hist direction & magnitude
    - Mean-reversion      : bb_pct distance from 0.5
    - Volume confirmation : volume_ratio signal
  Result clipped to [-1, +1]; positive = bullish bias, negative = bearish.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal as _signal
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import structlog
from confluent_kafka import Producer

# ── Logging ────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(pad_event=40),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), os.getenv("LOG_LEVEL", "INFO"))
    ),
)
log = structlog.get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://apex_user:apex_pass@localhost:15432/apex",
)
KAFKA_BOOTSTRAP   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
RAW_TOPIC         = os.getenv("SIGNAL_RAW_TOPIC", "apex.signals.raw")
SCAN_INTERVAL_SEC = float(os.getenv("SIGNAL_GEN_INTERVAL", "60"))   # seconds between full scan

# Override DATABASE_URL host/port for local vs Docker context
_DB_HOST = os.getenv("TIMESCALEDB_HOST", "localhost")
_DB_PORT = int(os.getenv("TIMESCALEDB_PORT", "15432"))
_DB_USER = os.getenv("TIMESCALEDB_USER", "apex_user")
_DB_PASS = os.getenv("TIMESCALEDB_PASSWORD", "apex_pass")
_DB_NAME = os.getenv("TIMESCALEDB_DB", "apex")

# If DATABASE_URL is not the default docker-internal one, use it directly.
_DATABASE_URL = (
    DATABASE_URL
    if "timescaledb:5432" not in DATABASE_URL
    else f"postgresql://{_DB_USER}:{_DB_PASS}@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}"
)


# ── Factor scoring helpers ─────────────────────────────────────────────────

def _safe_float(v: object, default: float = 0.0) -> float:
    """Convert Decimal / None to float safely."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sigmoid_center(x: float, scale: float = 3.0) -> float:
    """Symmetric sigmoid centred at 0: maps x→±1 smoothly."""
    return 2.0 / (1.0 + math.exp(-scale * x)) - 1.0


def compute_factor_score(row: asyncpg.Record) -> float:
    """
    Multi-factor score from TimescaleDB feature row.

    Returns a float in [-1, +1]:
      > 0  → bullish signal
      < 0  → bearish signal
      ~ 0  → neutral / no clear direction
    """
    # ── Momentum ────────────────────────────────────────────────────────────
    # Short-term returns carry more weight; long-term provide trend context.
    r1  = _safe_float(row["returns_1"])   # 1-bar return
    r5  = _safe_float(row["returns_5"])   # 5-bar return
    r15 = _safe_float(row["returns_15"])  # 15-bar return
    r60 = _safe_float(row["returns_60"])  # 60-bar return

    mom = _sigmoid_center(r1 * 20 + r5 * 5 + r15 * 2 + r60 * 1, scale=1.0)

    # ── RSI ─────────────────────────────────────────────────────────────────
    rsi14 = _safe_float(row["rsi_14"], default=50.0)
    # Normalise to [-1,1]; RSI=50 → 0, oversold (<30) → positive, overbought (>70) → negative
    rsi_score = _clamp((50.0 - rsi14) / 50.0)

    # ── MACD ────────────────────────────────────────────────────────────────
    macd_hist = _safe_float(row["macd_hist"])
    macd_sig  = _safe_float(row["macd_signal"])
    # Direction: histogram crossing zero is a strong signal
    macd_score = _clamp(math.copysign(1.0, macd_hist) * min(abs(macd_hist / max(abs(macd_sig), 1e-6)), 1.0))

    # ── Bollinger Band mean-reversion ────────────────────────────────────────
    bb_pct = _safe_float(row["bb_pct"], default=0.5)
    # bb_pct < 0.2 → oversold → bullish; bb_pct > 0.8 → overbought → bearish
    bb_score = _clamp((0.5 - bb_pct) * 2.0)

    # ── Volume confirmation ──────────────────────────────────────────────────
    vol_ratio = _safe_float(row["volume_ratio"], default=1.0)
    # High volume with positive momentum amplifies; high volume with negative dampens
    vol_scale = _clamp(math.log(max(vol_ratio, 0.1)) / math.log(3.0))  # log3(vol_ratio)→[-1,1]
    vol_confirmation = vol_scale * math.copysign(1, r5) * 0.5 if r5 != 0 else 0.0
    vol_confirmation = _clamp(vol_confirmation)

    # ── ADX trend-strength multiplier ──────────────────────────────────────
    adx = _safe_float(row["adx_14"], default=20.0)
    # ADX > 25 → strong trend → amplify directional scores
    adx_mult = _clamp(adx / 25.0, 0.5, 1.5)

    # ── VWAP deviation ──────────────────────────────────────────────────────
    vwap_dev = _safe_float(row["vwap_dev"])
    # Price below VWAP → potential mean-reversion buy; above → sell
    vwap_score = _clamp(-vwap_dev * 5.0)

    # ── Weighted combination ─────────────────────────────────────────────────
    weights = {
        "mom":    0.30,
        "rsi":    0.20,
        "macd":   0.20,
        "bb":     0.10,
        "vol":    0.10,
        "vwap":   0.10,
    }
    scores = {
        "mom":  mom,
        "rsi":  rsi_score,
        "macd": macd_score,
        "bb":   bb_score,
        "vol":  vol_confirmation,
        "vwap": vwap_score,
    }
    raw = sum(weights[k] * scores[k] for k in weights)
    # Apply ADX multiplier (trend-strength) to directional score
    final = _clamp(raw * adx_mult)
    return final


# ── Main service ──────────────────────────────────────────────────────────

class SignalGenerator:
    def __init__(self) -> None:
        self._shutdown = False
        self._pool: Optional[asyncpg.Pool] = None
        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
            "enable.idempotence": "true",
        })
        _signal.signal(_signal.SIGTERM, self._handle_stop)
        _signal.signal(_signal.SIGINT,  self._handle_stop)

    def _handle_stop(self, *_) -> None:
        log.info("signal_generator_stopping")
        self._shutdown = True

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                _DATABASE_URL, min_size=2, max_size=4, command_timeout=30
            )
        return self._pool

    async def _fetch_symbols(self, pool: asyncpg.Pool) -> list[str]:
        rows = await pool.fetch(
            "SELECT DISTINCT symbol FROM features ORDER BY symbol"
        )
        return [r["symbol"] for r in rows]

    async def _fetch_latest_features(
        self, pool: asyncpg.Pool, symbol: str
    ) -> Optional[asyncpg.Record]:
        row = await pool.fetchrow(
            """
            SELECT *
            FROM features
            WHERE symbol = $1
            ORDER BY time DESC
            LIMIT 1
            """,
            symbol,
        )
        return row

    def _publish(self, symbol: str, factor_score: float, ts: datetime) -> None:
        payload = {
            "symbol":       symbol,
            "factor_score": factor_score,
            "ts":           ts.isoformat() if ts else datetime.now(timezone.utc).isoformat(),
            "source":       "signal_generator",
        }
        self._producer.produce(
            RAW_TOPIC,
            value=json.dumps(payload).encode("utf-8"),
            key=symbol.encode("utf-8"),
        )
        log.debug(
            "raw_signal_published",
            symbol=symbol,
            factor_score=round(factor_score, 4),
        )

    async def _scan_all_symbols(self, pool: asyncpg.Pool) -> int:
        symbols = await self._fetch_symbols(pool)
        published = 0
        for sym in symbols:
            row = await self._fetch_latest_features(pool, sym)
            if row is None:
                log.warning("no_features_for_symbol", symbol=sym)
                continue
            factor_score = compute_factor_score(row)
            ts = row["time"]
            self._publish(sym, factor_score, ts)
            published += 1

        self._producer.flush(timeout=10)
        return published

    async def run(self) -> None:
        log.info(
            "signal_generator_started",
            topic=RAW_TOPIC,
            kafka=KAFKA_BOOTSTRAP,
            interval_s=SCAN_INTERVAL_SEC,
        )

        pool = await self._get_pool()
        log.info("db_connected", url=_DATABASE_URL.split("@")[-1])

        while not self._shutdown:
            t0 = time.monotonic()
            try:
                n = await self._scan_all_symbols(pool)
                elapsed = time.monotonic() - t0
                log.info(
                    "scan_complete",
                    symbols_published=n,
                    elapsed_s=round(elapsed, 2),
                )
            except Exception as exc:
                log.error("scan_error", error=str(exc))

            # Sleep the remainder of the interval
            elapsed = time.monotonic() - t0
            sleep_for = max(0.0, SCAN_INTERVAL_SEC - elapsed)
            if not self._shutdown:
                await asyncio.sleep(sleep_for)

        await pool.close()
        self._producer.flush(30)
        log.info("signal_generator_stopped")


async def main() -> None:
    svc = SignalGenerator()
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
