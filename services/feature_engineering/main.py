"""
APEX Feature Engineering Service  v2.0
========================================
Reads raw OHLCV bars from TimescaleDB and writes
computed feature vectors into the features table.

Indicators computed:
  returns_1 / 5 / 15 / 60        — log returns
  rsi_14 / rsi_28                 — Wilder RSI
  ema_20 / ema_50 / ema_200       — exponential moving averages
  macd / macd_signal / macd_hist  — MACD line, signal, histogram
  bb_upper / bb_lower / bb_pct    — Bollinger Bands (20,2)
  atr_14                          — Average True Range (Wilder)
  stoch_k / stoch_d               — Stochastic oscillator
  volume_ratio                    — vs 20-bar average
  vwap_dev                        — deviation from bar VWAP
  adx_14                          — Average Directional Index
  regime                          — BULL / BEAR / SIDEWAYS

Three run modes:
  1. Startup backfill — processes all existing bars
  2. Redis pub/sub    — triggered by data_ingestion live bars
  3. Fallback poll    — every 5 min catches any gaps
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import numpy as np
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    generate_latest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("apex.feature_eng")

app = FastAPI(title="APEX Feature Engineering")

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS = [
    # Mega-cap (already trained)
    "NVDA", "AAPL", "MSFT", "TSLA", "AMZN",
    "META", "GOOGL", "AMD", "SPY", "QQQ",
    # Semiconductors
    "AVGO", "QCOM", "TXN", "INTC", "MRVL", "ON", "MCHP",
    "LRCX", "AMAT", "KLAC", "SWKS", "ARM", "TER", "ASML",
    # Large Software / Infrastructure
    "CRM", "NOW", "ADBE", "ORCL", "IBM", "NFLX", "CSCO", "ANET",
    # Cloud / SaaS
    "SNOW", "MDB", "DDOG", "HUBS", "WDAY", "VEEV", "TEAM",
    "DT", "NTNX", "GTLB", "PATH", "MNDY", "BL", "PCTY", "ZI",
    # Cybersecurity
    "PANW", "CRWD", "NET", "ZS", "OKTA", "S", "FTNT",
    # Consumer Internet / Apps
    "UBER", "ABNB", "DASH", "SNAP", "PINS", "RBLX",
    "SHOP", "LYFT", "U", "ZM", "DOCU", "TWLO", "PLTR", "TTD",
    # Fintech / Payments
    "PYPL", "SQ", "COIN", "HOOD", "SOFI", "AFRM", "MELI",
    # Hardware / Infrastructure
    "DELL", "HPE", "NTAP", "STX", "WDC", "SMCI", "VRT", "HPQ",
    # EDA / Test & Measurement
    "CDNS", "SNPS", "KEYS", "ANSS",
    # IT Services
    "CTSH", "INFY", "EPAM", "GLOB", "ACN",
    # Other ETFs
    "SMH", "XLK",
]
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://apex_user:apex_pass@timescaledb:5432/apex",
)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
MIN_BARS   = 30    # absolute minimum
WARM_BARS  = 210   # fully-warmed EMA-200 (200 + 10 buffer)
FETCH_BARS = 260   # bars fetched per live-update (>= WARM_BARS)

# ── Prometheus ────────────────────────────────────────────────────────────────
features_computed = Counter(
    "apex_features_computed_total",
    "Feature vectors written to DB",
    ["symbol"],
)
feature_errors = Counter(
    "apex_feature_errors_total",
    "Feature computation errors",
    ["error_type"],
)
backfill_progress = Gauge(
    "apex_feat_backfill_progress",
    "Backfill completion 0-1",
    ["symbol"],
)
service_up = Gauge("apex_feature_eng_up", "1 while service is running")

# ── State ─────────────────────────────────────────────────────────────────────
_db_pool: Optional[asyncpg.Pool] = None
_redis:   Optional[aioredis.Redis] = None
_is_ready = False
_backfill_done: set[str] = set()

# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_db() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    for attempt in range(15):
        try:
            _db_pool = await asyncpg.create_pool(
                DATABASE_URL, min_size=2, max_size=6, command_timeout=60
            )
            logger.info("✓ TimescaleDB connected")
            return _db_pool
        except Exception as exc:
            wait = min(2 ** attempt, 30)
            logger.warning("DB not ready (%d/15): %s — retry in %ds", attempt + 1, exc, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("Cannot connect to TimescaleDB")


async def fetch_bars(symbol: str, limit: int = FETCH_BARS) -> list[dict]:
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time, symbol, open, high, low, close, volume, vwap
            FROM ohlcv_bars
            WHERE symbol = $1
            ORDER BY time DESC
            LIMIT $2
            """,
            symbol, limit,
        )
    return [dict(r) for r in reversed(rows)]


async def fetch_bars_since(symbol: str, since: datetime) -> list[dict]:
    """Fetch all bars after 'since', padded with WARM_BARS before it."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time, symbol, open, high, low, close, volume, vwap
            FROM ohlcv_bars
            WHERE symbol = $1
            ORDER BY time ASC
            """,
            symbol,
        )
    return [dict(r) for r in rows]


async def latest_feature_time(symbol: str) -> Optional[datetime]:
    pool = await get_db()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT MAX(time) FROM features WHERE symbol = $1", symbol
        )


async def insert_features(rows: list[dict]) -> int:
    if not rows:
        return 0
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO features (
              time, symbol,
              returns_1, returns_5, returns_15, returns_60,
              rsi_14, rsi_28,
              ema_20, ema_50, ema_200,
              macd, macd_signal, macd_hist,
              bb_upper, bb_lower, bb_pct,
              atr_14, stoch_k, stoch_d,
              volume_ratio, vwap_dev,
              adx_14, regime
            ) VALUES (
              $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
              $11,$12,$13,$14,$15,$16,$17,$18,
              $19,$20,$21,$22,$23,$24
            )
            ON CONFLICT (time, symbol) DO UPDATE SET
              rsi_14       = EXCLUDED.rsi_14,
              ema_20       = EXCLUDED.ema_20,
              ema_200      = EXCLUDED.ema_200,
              macd_hist    = EXCLUDED.macd_hist,
              regime       = EXCLUDED.regime
            """,
            [
                (
                    r["time"], r["symbol"],
                    r.get("returns_1"),  r.get("returns_5"),
                    r.get("returns_15"), r.get("returns_60"),
                    r.get("rsi_14"),     r.get("rsi_28"),
                    r.get("ema_20"),     r.get("ema_50"),    r.get("ema_200"),
                    r.get("macd"),       r.get("macd_signal"), r.get("macd_hist"),
                    r.get("bb_upper"),   r.get("bb_lower"),  r.get("bb_pct"),
                    r.get("atr_14"),     r.get("stoch_k"),   r.get("stoch_d"),
                    r.get("volume_ratio"), r.get("vwap_dev"),
                    r.get("adx_14"),     r.get("regime"),
                )
                for r in rows
            ],
        )
    return len(rows)


# ── Indicators ────────────────────────────────────────────────────────────────

def _safe(v: float) -> Optional[float]:
    """Convert nan/inf to None for DB insertion."""
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return float(v)


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    out = arr.copy().astype(float)
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int) -> np.ndarray:
    n   = len(closes)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    deltas = np.diff(closes.astype(float))
    ups    = np.maximum(deltas,  0.0)
    downs  = np.maximum(-deltas, 0.0)
    avg_up   = float(np.mean(ups[:period]))
    avg_down = float(np.mean(downs[:period]))
    for i in range(period, len(deltas)):
        avg_up   = (avg_up   * (period - 1) + ups[i])   / period
        avg_down = (avg_down * (period - 1) + downs[i]) / period
        out[i + 1] = 100.0 if avg_down == 0 else 100.0 - 100.0 / (1.0 + avg_up / avg_down)
    return out


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    n  = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
    out = np.full(n, np.nan)
    if n <= period:
        return out
    out[period] = float(np.mean(tr[1 : period + 1]))
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _adx(
    highs: np.ndarray,
    lows:  np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    if n < 2 * period + 1:
        return out

    tr       = np.zeros(n)
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i]  = up   if (up > down   and up   > 0) else 0.0
        minus_dm[i] = down if (down > up   and down > 0) else 0.0

    atr_s = np.full(n, np.nan)
    p14   = np.full(n, np.nan)
    m14   = np.full(n, np.nan)

    atr_s[period] = np.sum(tr[1 : period + 1])
    p14[period]   = np.sum(plus_dm[1 : period + 1])
    m14[period]   = np.sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / period + tr[i]
        p14[i]   = p14[i - 1]   - p14[i - 1]   / period + plus_dm[i]
        m14[i]   = m14[i - 1]   - m14[i - 1]   / period + minus_dm[i]

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di  = np.where(atr_s > 0, 100.0 * p14 / atr_s, 0.0)
        minus_di = np.where(atr_s > 0, 100.0 * m14 / atr_s, 0.0)

    di_sum = plus_di + minus_di
    dx     = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)

    start = 2 * period
    out[start] = float(np.mean(dx[period : start + 1]))
    for i in range(start + 1, n):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def compute_features(bars: list[dict], from_index: int = 0) -> list[dict]:
    """
    Compute all feature vectors for bars[from_index:].
    Requires at least MIN_BARS total bars in the input.
    Returns empty list if insufficient data.
    """
    n = len(bars)
    if n < MIN_BARS:
        return []

    closes  = np.array([b["close"]  for b in bars], dtype=float)
    highs   = np.array([b["high"]   for b in bars], dtype=float)
    lows    = np.array([b["low"]    for b in bars], dtype=float)
    volumes = np.array([b["volume"] for b in bars], dtype=float)

    # Log returns
    lc     = np.log(np.maximum(closes, 1e-10))
    ret1   = np.full(n, np.nan)
    ret5   = np.full(n, np.nan)
    ret15  = np.full(n, np.nan)
    ret60  = np.full(n, np.nan)
    if n >  1: ret1[1:]   = lc[1:]  - lc[:-1]
    if n >  5: ret5[5:]   = lc[5:]  - lc[:-5]
    if n > 15: ret15[15:] = lc[15:] - lc[:-15]
    if n > 60: ret60[60:] = lc[60:] - lc[:-60]

    # EMAs
    ema20  = _ema(closes, 20)
    ema50  = _ema(closes, 50)
    ema200 = _ema(closes, 200)

    # MACD (12/26/9)
    e12       = _ema(closes, 12)
    e26       = _ema(closes, 26)
    macd_line = e12 - e26
    macd_sig  = _ema(macd_line, 9)
    macd_hist = macd_line - macd_sig

    # RSI
    rsi14 = _rsi(closes, 14)
    rsi28 = _rsi(closes, 28)

    # Bollinger Bands (20, 2)
    bb_up  = np.full(n, np.nan)
    bb_dn  = np.full(n, np.nan)
    bb_pct = np.full(n, np.nan)
    for i in range(19, n):
        w   = closes[i - 19 : i + 1]
        sma = w.mean()
        std = w.std(ddof=1)
        bb_up[i]  = sma + 2 * std
        bb_dn[i]  = sma - 2 * std
        rng = bb_up[i] - bb_dn[i]
        if rng > 0:
            bb_pct[i] = (closes[i] - bb_dn[i]) / rng

    # ATR-14
    atr14 = _atr(highs, lows, closes, 14)

    # Stochastic %K/%D (14/3)
    stk = np.full(n, np.nan)
    std = np.full(n, np.nan)
    for i in range(13, n):
        h14 = highs[i - 13 : i + 1].max()
        l14 = lows[i  - 13 : i + 1].min()
        if h14 > l14:
            stk[i] = (closes[i] - l14) / (h14 - l14) * 100.0
    for i in range(15, n):
        if not np.isnan(stk[i]) and not np.isnan(stk[i - 1]) and not np.isnan(stk[i - 2]):
            std[i] = (stk[i] + stk[i - 1] + stk[i - 2]) / 3.0

    # ADX-14
    adx14 = _adx(highs, lows, closes, 14)

    # Volume ratio
    vol_ratio = np.full(n, np.nan)
    for i in range(20, n):
        avg = volumes[i - 20 : i].mean()
        if avg > 0:
            vol_ratio[i] = volumes[i] / avg

    # VWAP deviation
    def _vwap_dev(b: dict, close: float) -> Optional[float]:
        vwap = b.get("vwap")
        if vwap and float(vwap) > 0:
            return (close - float(vwap)) / float(vwap)
        return None

    # Regime
    def _regime(i: int) -> Optional[str]:
        if np.isnan(ema50[i]) or np.isnan(ema200[i]) or np.isnan(adx14[i]):
            return None
        adx = adx14[i]
        if adx < 20:
            return "SIDEWAYS"
        c, e50, e200 = closes[i], ema50[i], ema200[i]
        if c > e50 > e200:
            return "BULL"
        if c < e50 < e200:
            return "BEAR"
        return "SIDEWAYS"

    results = []
    start = max(from_index, 0)
    for i in range(start, n):
        b = bars[i]
        results.append({
            "time":         b["time"],
            "symbol":       b["symbol"],
            "returns_1":    _safe(ret1[i]),
            "returns_5":    _safe(ret5[i]),
            "returns_15":   _safe(ret15[i]),
            "returns_60":   _safe(ret60[i]),
            "rsi_14":       _safe(rsi14[i]),
            "rsi_28":       _safe(rsi28[i]),
            "ema_20":       _safe(ema20[i]),
            "ema_50":       _safe(ema50[i]),
            "ema_200":      _safe(ema200[i]) if i >= 199 else None,
            "macd":         _safe(macd_line[i]),
            "macd_signal":  _safe(macd_sig[i]),
            "macd_hist":    _safe(macd_hist[i]),
            "bb_upper":     _safe(bb_up[i]),
            "bb_lower":     _safe(bb_dn[i]),
            "bb_pct":       _safe(bb_pct[i]),
            "atr_14":       _safe(atr14[i]),
            "stoch_k":      _safe(stk[i]),
            "stoch_d":      _safe(std[i]),
            "volume_ratio": _safe(vol_ratio[i]),
            "vwap_dev":     _vwap_dev(b, float(closes[i])),
            "adx_14":       _safe(adx14[i]),
            "regime":       _regime(i),
        })
    return results


# ── Backfill ──────────────────────────────────────────────────────────────────

async def backfill_symbol(symbol: str) -> None:
    try:
        # Find latest existing feature
        latest_feat = await latest_feature_time(symbol)

        # Fetch ALL bars for this symbol
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT time, symbol, open, high, low, close, volume, vwap "
                "FROM ohlcv_bars WHERE symbol = $1 ORDER BY time ASC",
                symbol,
            )

        bars = [dict(r) for r in rows]
        n    = len(bars)
        if n < MIN_BARS:
            logger.info("%s: only %d bars — need %d minimum, skipping", symbol, n, MIN_BARS)
            _backfill_done.add(symbol)
            backfill_progress.labels(symbol=symbol).set(1.0)
            return

        # Determine from_index — only compute features for bars after the latest feature
        if latest_feat:
            from_index = next(
                (i for i, b in enumerate(bars) if b["time"] > latest_feat), n
            )
            if from_index >= n:
                logger.info("%s: features up to date (%s)", symbol, latest_feat)
                _backfill_done.add(symbol)
                backfill_progress.labels(symbol=symbol).set(1.0)
                return
            # Ensure we have enough warmup bars before from_index
            compute_from = max(from_index, MIN_BARS)
            logger.info(
                "%s: incremental backfill — %d new bars (from %s)",
                symbol, n - from_index, latest_feat,
            )
        else:
            compute_from = MIN_BARS
            logger.info("%s: full backfill — %d bars total", symbol, n)

        # Compute features in one vectorized pass
        feature_rows = compute_features(bars, from_index=compute_from)

        if not feature_rows:
            logger.info("%s: no new features to write", symbol)
            _backfill_done.add(symbol)
            backfill_progress.labels(symbol=symbol).set(1.0)
            return

        # Insert in batches of 500
        batch_sz  = 500
        inserted  = 0
        total     = len(feature_rows)
        for start in range(0, total, batch_sz):
            batch = feature_rows[start : start + batch_sz]
            inserted += await insert_features(batch)
            progress = min((start + batch_sz) / total, 1.0)
            backfill_progress.labels(symbol=symbol).set(progress)
            features_computed.labels(symbol=symbol).inc(len(batch))
            await asyncio.sleep(0.05)

        _backfill_done.add(symbol)
        backfill_progress.labels(symbol=symbol).set(1.0)
        logger.info("%s: backfill complete — %d features written", symbol, inserted)

    except Exception as exc:
        feature_errors.labels(error_type="backfill_error").inc()
        logger.error("%s: backfill error: %s", symbol, exc)
        _backfill_done.add(symbol)  # still mark done to unblock startup


async def run_backfill() -> None:
    logger.info("Starting feature backfill for %d symbols...", len(SYMBOLS))
    for i in range(0, len(SYMBOLS), 3):
        batch = SYMBOLS[i : i + 3]
        await asyncio.gather(*[backfill_symbol(s) for s in batch])
        await asyncio.sleep(0.5)
    logger.info("✓ Feature backfill complete")


# ── Live subscription (Redis pub/sub) ─────────────────────────────────────────

async def _compute_live(symbol: str) -> None:
    """Compute and insert features for the latest bar of a symbol."""
    try:
        bars = await fetch_bars(symbol, limit=FETCH_BARS)
        if len(bars) < MIN_BARS:
            return
        # Only compute the last bar
        rows = compute_features(bars, from_index=len(bars) - 1)
        if rows:
            await insert_features(rows)
            features_computed.labels(symbol=symbol).inc()
    except Exception as exc:
        feature_errors.labels(error_type="live_error").inc()
        logger.error("Live compute error %s: %s", symbol, exc)


async def subscribe_live_bars() -> None:
    """Subscribe to Redis pub/sub apex:bars:* channels."""
    backoff = 1
    while True:
        try:
            if _redis is None:
                await asyncio.sleep(10)
                continue
            pubsub = _redis.pubsub()
            await pubsub.psubscribe("apex:bars:*")
            logger.info("✓ Subscribed to Redis apex:bars:* for live feature updates")
            backoff = 1
            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                channel = message["channel"]
                # channel = "apex:bars:NVDA"
                symbol = channel.split(":")[-1]
                if symbol in SYMBOLS:
                    await _compute_live(symbol)
        except Exception as exc:
            logger.warning("Redis pub/sub error: %s — reconnecting in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


# ── Fallback poll ─────────────────────────────────────────────────────────────

async def poll_gaps() -> None:
    """Every 5 min, check for bars without features and fill gaps."""
    while True:
        await asyncio.sleep(300)
        try:
            pool = await get_db()
            async with pool.acquire() as conn:
                # Find symbols with bars newer than latest feature
                gaps = await conn.fetch(
                    """
                    SELECT b.symbol
                    FROM ohlcv_bars b
                    LEFT JOIN features f ON b.time = f.time AND b.symbol = f.symbol
                    WHERE f.time IS NULL
                    GROUP BY b.symbol
                    HAVING COUNT(*) > 50
                    """
                )
            for row in gaps:
                sym = row["symbol"]
                if sym in SYMBOLS:
                    logger.info("Gap fill: %s", sym)
                    await backfill_symbol(sym)
        except Exception as exc:
            feature_errors.labels(error_type="poll_error").inc()
            logger.error("Gap poll error: %s", exc)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global _redis, _is_ready

    try:
        _redis = await aioredis.from_url(
            f"redis://{REDIS_HOST}:6379", decode_responses=True
        )
        await _redis.ping()
        logger.info("✓ Redis connected")
    except Exception as exc:
        logger.warning("Redis unavailable: %s — live updates disabled", exc)
        _redis = None

    await get_db()
    service_up.set(1)
    _is_ready = True

    asyncio.create_task(run_backfill())
    asyncio.create_task(subscribe_live_bars())
    asyncio.create_task(poll_gaps())

    logger.info("✓ Feature Engineering Service v2.0 started")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status":        "healthy",
        "service":       "feature_engineering",
        "ready":         _is_ready,
        "backfill_done": sorted(_backfill_done),
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
async def ready():
    if not _is_ready:
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status() -> dict:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            counts = await conn.fetch(
                """
                SELECT
                  symbol,
                  COUNT(*)  AS feature_count,
                  MIN(time) AS oldest,
                  MAX(time) AS newest
                FROM features
                GROUP BY symbol
                ORDER BY symbol
                """
            )
        return {
            "symbols":      [dict(r) for r in counts],
            "backfill_done": sorted(_backfill_done),
        }
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8003")),
        log_level="info",
    )
