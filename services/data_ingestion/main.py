"""
APEX Data Ingestion Service
============================
Fetches OHLCV bars from Alpaca and stores them in TimescaleDB.

Three modes:
  1. Historical backfill (startup)  — 1 year of 15-min bars, runs once
  2. Polling (every 15 min, market hours) — fills gaps
  3. WebSocket streaming (live)     — real-time bar updates

Environment variables required:
  ALPACA_PAPER_KEY
  ALPACA_PAPER_SECRET
  ALPACA_PAPER_URL  (default: https://paper-api.alpaca.markets)
  DATABASE_URL      (default: postgresql://apex_user:apex_pass@timescaledb:5432/apex)
  REDIS_HOST        (default: redis)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import asyncpg
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
logger = logging.getLogger("apex.data_ingestion")

app = FastAPI(title="APEX Data Ingestion")

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
ALPACA_KEY    = os.getenv("ALPACA_PAPER_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_PAPER_SECRET", "")
ALPACA_BASE   = os.getenv("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA   = "https://data.alpaca.markets"
ALPACA_WS     = "wss://stream.data.alpaca.markets/v2/iex"
DATABASE_URL  = os.getenv(
    "DATABASE_URL",
    "postgresql://apex_user:apex_pass@timescaledb:5432/apex",
)
REDIS_HOST    = os.getenv("REDIS_HOST", "redis")

# ── Prometheus metrics ────────────────────────────────────────────────────────
bars_ingested = Counter(
    "apex_bars_ingested_total",
    "Total bars ingested",
    ["symbol", "source"],
)
ingestion_errors = Counter(
    "apex_ingestion_errors_total",
    "Ingestion errors",
    ["error_type"],
)
last_bar_age = Gauge(
    "apex_data_last_bar_age_seconds",
    "Seconds since most recent bar per symbol",
    ["symbol"],
)
ws_connected = Gauge("apex_data_ws_connected", "1 if WebSocket connected")
backfill_progress = Gauge(
    "apex_data_backfill_progress",
    "Backfill completion 0-1",
    ["symbol"],
)
db_rows = Gauge("apex_data_db_rows_total", "Total rows in ohlcv_bars")

# ── State ─────────────────────────────────────────────────────────────────────
_db_pool: Optional[asyncpg.Pool] = None
_redis: Optional[aioredis.Redis] = None
_is_ready: bool = False
_backfill_done: set[str] = set()

# ── Database ──────────────────────────────────────────────────────────────────

async def get_db() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    for attempt in range(15):
        try:
            _db_pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=8,
                command_timeout=30,
            )
            logger.info("✓ TimescaleDB connected")
            return _db_pool
        except Exception as exc:
            wait = min(2 ** attempt, 30)
            logger.warning("DB not ready (%d/15): %s — retry in %ds", attempt + 1, exc, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("Cannot connect to TimescaleDB after 15 attempts")


async def insert_bars(bars: list[dict], source: str = "rest") -> int:
    if not bars:
        return 0
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO ohlcv_bars
              (time, symbol, open, high, low, close, volume, vwap, trade_count, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (time, symbol) DO NOTHING
            """,
            [
                (
                    bar["time"],
                    bar["symbol"],
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                    bar.get("vwap"),
                    bar.get("trade_count"),
                    source,
                )
                for bar in bars
            ],
        )
    for bar in bars:
        bars_ingested.labels(symbol=bar["symbol"], source=source).inc()
    return len(bars)


# ── Alpaca REST helpers ───────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }


def _parse_bar(symbol: str, bar: dict) -> dict:
    return {
        "time":        datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
        "symbol":      symbol,
        "open":        float(bar["o"]),
        "high":        float(bar["h"]),
        "low":         float(bar["l"]),
        "close":       float(bar["c"]),
        "volume":      int(bar["v"]),
        "vwap":        float(bar["vw"]) if bar.get("vw") else None,
        "trade_count": int(bar["n"])    if bar.get("n")  else None,
    }


async def fetch_bars_rest(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "15Min",
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """Fetch historical bars from Alpaca REST. Handles pagination."""
    bars: list[dict] = []
    page_token: Optional[str] = None
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        while True:
            params: dict = {
                "symbols":   symbol,
                "timeframe": timeframe,
                "start":     start.isoformat(),
                "end":       end.isoformat(),
                "limit":     1000,
                "feed":      "iex",
                "sort":      "asc",
            }
            if page_token:
                params["page_token"] = page_token

            try:
                async with session.get(
                    f"{ALPACA_DATA}/v2/stocks/bars",
                    headers=_headers(),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(10)
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error("Alpaca REST %d: %s", resp.status, text[:200])
                        ingestion_errors.labels(error_type="api_error").inc()
                        break

                    data = await resp.json()
                    for bar in data.get("bars", {}).get(symbol, []):
                        bars.append(_parse_bar(symbol, bar))

                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
                    await asyncio.sleep(0.3)

            except asyncio.TimeoutError:
                logger.warning("Timeout fetching %s — retrying", symbol)
                await asyncio.sleep(5)
                break

    finally:
        if own_session:
            await session.close()

    return bars


# ── Historical backfill ───────────────────────────────────────────────────────

async def backfill_symbol(symbol: str, session: aiohttp.ClientSession) -> int:
    """Fetch up to 1 year of 15-min bars if we don't have recent data."""
    pool = await get_db()
    async with pool.acquire() as conn:
        newest = await conn.fetchval(
            "SELECT MAX(time) FROM ohlcv_bars WHERE symbol = $1", symbol
        )

    now = datetime.now(timezone.utc)

    if newest and newest > now - timedelta(hours=2):
        logger.info("%s: data is current (newest=%s), skipping backfill", symbol, newest)
        backfill_progress.labels(symbol=symbol).set(1.0)
        _backfill_done.add(symbol)
        return 0

    start = (newest + timedelta(minutes=15)) if newest else (now - timedelta(days=365))
    action = f"incremental from {newest}" if newest else "full 1-year"
    logger.info("%s: %s backfill starting", symbol, action)

    total_inserted = 0
    chunk_start = start

    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(days=30), now)
        bars = await fetch_bars_rest(symbol, chunk_start, chunk_end, session=session)

        if bars:
            n = await insert_bars(bars, "backfill")
            total_inserted += n
            logger.info(
                "%s: +%d bars  [%s → %s]",
                symbol,
                n,
                chunk_start.date(),
                chunk_end.date(),
            )

        elapsed  = (chunk_end - start).total_seconds()
        duration = max((now - start).total_seconds(), 1)
        backfill_progress.labels(symbol=symbol).set(min(elapsed / duration, 1.0))

        chunk_start = chunk_end
        await asyncio.sleep(0.5)

    _backfill_done.add(symbol)
    backfill_progress.labels(symbol=symbol).set(1.0)
    logger.info("%s: backfill complete — %d bars total", symbol, total_inserted)
    return total_inserted


async def run_backfill() -> None:
    logger.info("Starting historical backfill for %d symbols...", len(SYMBOLS))
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(SYMBOLS), 3):
            batch = SYMBOLS[i : i + 3]
            await asyncio.gather(*[backfill_symbol(s, session) for s in batch])
            await asyncio.sleep(1)

    await _update_db_row_count()
    logger.info("✓ Historical backfill complete for all symbols")


# ── Polling ───────────────────────────────────────────────────────────────────

async def poll_latest_bars() -> None:
    """Fetch last 30 min of bars every 15 minutes (market hours only)."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            is_weekday = now.weekday() < 5
            is_market  = 13 <= now.hour <= 21  # ~9:00 AM–5:30 PM ET (UTC-4/5)

            if is_weekday and is_market:
                logger.info("Polling latest bars...")
                start = now - timedelta(minutes=30)
                async with aiohttp.ClientSession() as session:
                    for symbol in SYMBOLS:
                        try:
                            bars = await fetch_bars_rest(symbol, start, now, session=session)
                            if bars:
                                n = await insert_bars(bars, "poll")
                                if n > 0:
                                    logger.info("Poll: %s +%d bars", symbol, n)
                        except Exception as exc:
                            ingestion_errors.labels(error_type="poll_error").inc()
                            logger.error("Poll error %s: %s", symbol, exc)
                        await asyncio.sleep(0.2)

                await _update_db_row_count()

        except Exception as exc:
            logger.error("Poll loop error: %s", exc)

        await asyncio.sleep(900)  # 15 minutes


# ── WebSocket streaming ───────────────────────────────────────────────────────

async def stream_live_bars() -> None:
    """Subscribe to Alpaca WebSocket with auto-reconnect."""
    backoff = 1
    while True:
        try:
            await _ws_connect()
            backoff = 1
        except Exception as exc:
            ws_connected.set(0)
            logger.warning("WebSocket disconnected: %s — reconnecting in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def _ws_connect() -> None:
    if not ALPACA_KEY or not ALPACA_SECRET:
        logger.warning("No Alpaca credentials — WebSocket disabled")
        return

    logger.info("Connecting to Alpaca WebSocket: %s", ALPACA_WS)
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            ALPACA_WS, timeout=aiohttp.ClientTimeout(total=30)
        ) as ws:
            await ws.send_json({"action": "auth", "key": ALPACA_KEY, "secret": ALPACA_SECRET})
            # IEX free tier caps at ~30 symbols per subscribe call — send in chunks
            chunk_size = 25
            for i in range(0, len(SYMBOLS), chunk_size):
                await ws.send_json({"action": "subscribe", "bars": SYMBOLS[i:i+chunk_size]})
            ws_connected.set(1)
            logger.info("✓ WebSocket subscribed to %d symbols (in chunks of %d)", len(SYMBOLS), chunk_size)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await _handle_ws_messages(json.loads(msg.data))
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    ws_connected.set(0)
                    break


async def _handle_ws_messages(messages: list) -> None:
    for msg in messages:
        t = msg.get("T")
        if t == "b":
            symbol = msg.get("S", "")
            if symbol not in SYMBOLS:
                continue

            bar = _parse_bar(symbol, {
                "t": msg.get("t", ""),
                "o": msg.get("o", 0),
                "h": msg.get("h", 0),
                "l": msg.get("l", 0),
                "c": msg.get("c", 0),
                "v": msg.get("v", 0),
                "vw": msg.get("vw"),
                "n":  msg.get("n"),
            })
            await insert_bars([bar], "websocket")
            last_bar_age.labels(symbol=symbol).set(0)

            if _redis:
                payload = json.dumps({
                    "symbol": symbol,
                    "time":   bar["time"].isoformat(),
                    "open":   float(bar["open"]),
                    "high":   float(bar["high"]),
                    "low":    float(bar["low"]),
                    "close":  float(bar["close"]),
                    "volume": bar["volume"],
                })
                try:
                    await _redis.publish(f"apex:bars:{symbol}", payload)
                    await _redis.set(
                        f"apex:price:{symbol}",
                        json.dumps({"price": float(bar["close"]), "time": bar["time"].isoformat()}),
                        ex=300,
                    )
                except Exception:
                    pass

        elif t == "error":
            logger.error("WS error: %s", msg)
            ingestion_errors.labels(error_type="ws_error").inc()


# ── Age gauge updater ─────────────────────────────────────────────────────────

async def update_age_gauges() -> None:
    while True:
        try:
            pool = await get_db()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (symbol)
                      symbol,
                      EXTRACT(EPOCH FROM (NOW() - time)) AS age_seconds
                    FROM ohlcv_bars
                    ORDER BY symbol, time DESC
                    """
                )
                for row in rows:
                    last_bar_age.labels(symbol=row["symbol"]).set(float(row["age_seconds"]))
        except Exception as exc:
            logger.error("Age gauge error: %s", exc)
        await asyncio.sleep(60)


async def _update_db_row_count() -> None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM ohlcv_bars")
            db_rows.set(count)
    except Exception:
        pass


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
        logger.warning("Redis unavailable: %s — continuing without it", exc)
        _redis = None

    await get_db()
    _is_ready = True

    asyncio.create_task(run_backfill())
    asyncio.create_task(poll_latest_bars())
    asyncio.create_task(stream_live_bars())
    asyncio.create_task(update_age_gauges())

    logger.info("✓ Data Ingestion Service started")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status":        "healthy",
        "service":       "data_ingestion",
        "ready":         _is_ready,
        "symbols":       SYMBOLS,
        "backfill_done": sorted(_backfill_done),
        "ws_connected":  ws_connected._value.get() == 1.0,
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
                  COUNT(*)  AS bar_count,
                  MIN(time) AS oldest,
                  MAX(time) AS newest
                FROM ohlcv_bars
                GROUP BY symbol
                ORDER BY symbol
                """
            )
        return {
            "symbols":           [dict(r) for r in counts],
            "backfill_complete": sorted(_backfill_done),
            "ws_connected":      ws_connected._value.get() == 1.0,
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/backfill")
async def force_backfill(body: dict = None) -> dict:
    """Force historical backfill for specified symbols (ignores 'data is current' guard)."""
    if body and body.get("symbols"):
        targets = [s.upper() for s in body["symbols"] if s.upper() in SYMBOLS]
    else:
        targets = SYMBOLS

    async def _force_symbol(symbol: str, session: aiohttp.ClientSession) -> int:
        pool = await get_db()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            newest = await conn.fetchval(
                "SELECT MAX(time) FROM ohlcv_bars WHERE symbol = $1", symbol
            )
        start = (newest + timedelta(minutes=15)) if newest else (now - timedelta(days=365))
        # Force full year if we have fewer than 30 bars
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM ohlcv_bars WHERE symbol = $1", symbol
            )
        if count < 30:
            start = now - timedelta(days=365)
        total = 0
        chunk_start = start
        while chunk_start < now:
            chunk_end = min(chunk_start + timedelta(days=30), now)
            bars = await fetch_bars_rest(symbol, chunk_start, chunk_end, session=session)
            if bars:
                total += await insert_bars(bars, "force_backfill")
            chunk_start = chunk_end
            await asyncio.sleep(0.3)
        _backfill_done.add(symbol)
        logger.info("%s: force backfill done — %d bars inserted", symbol, total)
        return total

    results: dict[str, int] = {}
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(targets), 3):
            batch = targets[i : i + 3]
            counts = await asyncio.gather(*[_force_symbol(s, session) for s in batch])
            for sym, cnt in zip(batch, counts):
                results[sym] = cnt
            await asyncio.sleep(0.5)

    return {"status": "ok", "bars_inserted": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        log_level="info",
    )
