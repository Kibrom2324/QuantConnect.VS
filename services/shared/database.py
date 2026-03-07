"""
APEX Database Client
====================
Shared database access layer.
Used by: Data Ingestion, Feature Engineering,
         Signal Engine, Risk Engine,
         Execution, API routes.

Usage:
  from shared.database import db

  # Store bars
  await db.insert_bars(bars)

  # Get latest bars
  bars = await db.get_bars("NVDA", "15m", limit=200)

  # Store signal
  await db.insert_signal(signal)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://apex_user:apex_pass@timescaledb:5432/apex",
)


class Database:
    def __init__(self) -> None:
        self._pool: Optional[object] = None  # asyncpg.Pool

    # ── Connection ──────────────────────────────────────────────────────────

    async def connect(self, max_retries: int = 10) -> None:
        """Connect with exponential backoff retry."""
        import asyncpg  # deferred — not installed in every container

        for attempt in range(max_retries):
            try:
                self._pool = await asyncpg.create_pool(
                    DATABASE_URL,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                )
                logger.info("✓ TimescaleDB connected")
                return
            except Exception as exc:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "DB not ready (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1,
                    max_retries,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError("Could not connect to TimescaleDB after %d attempts" % max_retries)

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()  # type: ignore[union-attr]

    # ── OHLCV Bars ──────────────────────────────────────────────────────────

    async def insert_bars(self, bars: list[dict]) -> int:
        """
        Insert OHLCV bars. Skips duplicates.
        Each bar: {time, symbol, open, high, low, close, volume, vwap?}
        Returns: number of rows inserted.
        """
        if not bars:
            return 0
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.executemany(
                """
                INSERT INTO ohlcv_bars
                  (time, symbol, open, high, low, close, volume, vwap, trade_count)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
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
                    )
                    for bar in bars
                ],
            )
        return len(bars)

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Get OHLCV bars for a symbol.
        timeframe: 1m | 5m | 15m | 1h | 4h | 1d
        """
        # Map timeframe to bucket interval or pre-agg view
        tf_map: dict[str, tuple[str, Optional[str]]] = {
            "1m":  ("ohlcv_bars", "1 minute"),
            "5m":  ("ohlcv_bars", "5 minutes"),
            "15m": ("ohlcv_bars", "15 minutes"),
            "1h":  ("ohlcv_1h",   None),
            "4h":  ("ohlcv_bars", "4 hours"),
            "1d":  ("ohlcv_1d",   None),
        }
        table, bucket = tf_map.get(timeframe, ("ohlcv_bars", "15 minutes"))

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            if bucket:
                rows = await conn.fetch(
                    """
                    SELECT
                      time_bucket($1::interval, time) AS time,
                      symbol,
                      FIRST(open,  time) AS open,
                      MAX(high)          AS high,
                      MIN(low)           AS low,
                      LAST(close,  time) AS close,
                      SUM(volume)        AS volume
                    FROM ohlcv_bars
                    WHERE symbol = $2
                      AND time >= NOW() - ($3 * $1::interval)
                    GROUP BY 1, symbol
                    ORDER BY 1 DESC
                    LIMIT $3
                    """,
                    bucket,
                    symbol,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT bucket AS time, symbol,
                           open, high, low, close, volume
                    FROM {table}
                    WHERE symbol = $1
                    ORDER BY bucket DESC
                    LIMIT $2
                    """,
                    symbol,
                    limit,
                )

            return [dict(r) for r in reversed(rows)]

    async def get_latest_price(self, symbol: str) -> Optional[dict]:
        """Get the most recent price for a symbol."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT time, close AS price, volume
                FROM ohlcv_bars
                WHERE symbol = $1
                ORDER BY time DESC
                LIMIT 1
                """,
                symbol,
            )
            return dict(row) if row else None

    # ── Features ────────────────────────────────────────────────────────────

    async def insert_features(self, features: dict) -> None:
        """Store computed feature vector."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
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
                ON CONFLICT (time, symbol)
                DO UPDATE SET
                  rsi_14  = EXCLUDED.rsi_14,
                  ema_20  = EXCLUDED.ema_20,
                  macd    = EXCLUDED.macd,
                  regime  = EXCLUDED.regime
                """,
                features["time"],
                features["symbol"],
                features.get("returns_1"),
                features.get("returns_5"),
                features.get("returns_15"),
                features.get("returns_60"),
                features.get("rsi_14"),
                features.get("rsi_28"),
                features.get("ema_20"),
                features.get("ema_50"),
                features.get("ema_200"),
                features.get("macd"),
                features.get("macd_signal"),
                features.get("macd_hist"),
                features.get("bb_upper"),
                features.get("bb_lower"),
                features.get("bb_pct"),
                features.get("atr_14"),
                features.get("stoch_k"),
                features.get("stoch_d"),
                features.get("volume_ratio"),
                features.get("vwap_dev"),
                features.get("adx_14"),
                features.get("regime"),
            )

    async def get_features(self, symbol: str, limit: int = 500) -> list[dict]:
        """Get recent feature vectors for model input."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT * FROM features
                WHERE symbol = $1
                ORDER BY time DESC
                LIMIT $2
                """,
                symbol,
                limit,
            )
            return [dict(r) for r in reversed(rows)]

    # ── Signals ─────────────────────────────────────────────────────────────

    async def insert_signal(self, signal: dict) -> int:
        """Store generated signal. Returns signal row ID."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO signals (
                  time, symbol, direction,
                  score, confidence, model_id, regime,
                  tft_score, xgb_score, lstm_score,
                  tft_weight, xgb_weight, lstm_weight
                ) VALUES (
                  $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13
                )
                ON CONFLICT (time, symbol) DO UPDATE
                  SET score      = EXCLUDED.score,
                      direction  = EXCLUDED.direction,
                      confidence = EXCLUDED.confidence
                RETURNING id
                """,
                signal.get("time", datetime.now(timezone.utc)),
                signal["symbol"],
                signal["direction"],
                signal["score"],
                signal["confidence"],
                signal.get("model_id"),
                signal.get("regime"),
                signal.get("tft_score"),
                signal.get("xgb_score"),
                signal.get("lstm_score"),
                signal.get("tft_weight"),
                signal.get("xgb_weight"),
                signal.get("lstm_weight"),
            )
            return row["id"]  # type: ignore[index]

    async def get_signals(
        self,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 50,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Get recent signals with optional filters."""
        conditions = ["score >= $1"]
        params: list = [min_score]
        idx = 2

        if symbol:
            conditions.append(f"symbol = ${idx}")
            params.append(symbol)
            idx += 1

        if direction:
            conditions.append(f"direction = ${idx}")
            params.append(direction)
            idx += 1

        params.append(limit)
        where = " AND ".join(conditions)

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                f"""
                SELECT * FROM signals
                WHERE {where}
                ORDER BY time DESC
                LIMIT ${idx}
                """,
                *params,
            )
            return [dict(r) for r in rows]

    # ── Orders ──────────────────────────────────────────────────────────────

    async def insert_order(self, order: dict) -> int:
        """Store order. Returns order ID."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO orders (
                  alpaca_order_id, symbol, side, qty,
                  order_type, limit_price, status, source, model_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (alpaca_order_id) DO UPDATE
                  SET status       = EXCLUDED.status,
                      filled_price = EXCLUDED.filled_price,
                      filled_qty   = EXCLUDED.filled_qty,
                      filled_at    = EXCLUDED.filled_at
                RETURNING id
                """,
                order.get("alpaca_order_id"),
                order["symbol"],
                order["side"],
                order["qty"],
                order.get("order_type", "MARKET"),
                order.get("limit_price"),
                order.get("status", "PENDING"),
                order.get("source", "MANUAL"),
                order.get("model_id"),
            )
            return row["id"]  # type: ignore[index]

    async def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent orders with optional filters."""
        conditions: list[str] = []
        params: list = []
        idx = 1

        if symbol:
            conditions.append(f"symbol = ${idx}")
            params.append(symbol)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                f"""
                SELECT * FROM orders
                {where}
                ORDER BY time DESC
                LIMIT ${idx}
                """,
                *params,
            )
            return [dict(r) for r in rows]

    # ── Portfolio ────────────────────────────────────────────────────────────

    async def snapshot_portfolio(self, portfolio: dict) -> None:
        """Store portfolio value snapshot."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO portfolio_snapshots (
                  time, portfolio_value, cash_balance,
                  buying_power, total_pnl, daily_pnl, open_positions
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (time) DO NOTHING
                """,
                portfolio.get("time", datetime.now(timezone.utc)),
                portfolio["portfolio_value"],
                portfolio["cash_balance"],
                portfolio.get("buying_power"),
                portfolio.get("total_pnl"),
                portfolio.get("daily_pnl"),
                portfolio.get("open_positions", 0),
            )

    async def get_portfolio_history(self, days: int = 30) -> list[dict]:
        """Get portfolio value history for the P&L chart."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT
                  time_bucket('1 hour', time) AS time,
                  AVG(portfolio_value) AS portfolio_value,
                  AVG(daily_pnl)       AS daily_pnl
                FROM portfolio_snapshots
                WHERE time >= NOW() - INTERVAL '1 day' * $1
                GROUP BY 1
                ORDER BY 1 ASC
                """,
                days,
            )
            return [dict(r) for r in rows]

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check database connectivity and table row counts."""
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                tables: dict[str, int] = {}
                for table in [
                    "ohlcv_bars",
                    "features",
                    "signals",
                    "orders",
                    "positions",
                ]:
                    tables[table] = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {table}"
                    )

                oldest_bar = await conn.fetchval("SELECT MIN(time) FROM ohlcv_bars")
                newest_bar = await conn.fetchval("SELECT MAX(time) FROM ohlcv_bars")

                return {
                    "status": "healthy",
                    "tables": tables,
                    "data_range": {
                        "oldest": str(oldest_bar),
                        "newest": str(newest_bar),
                    },
                }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


# ── Singleton ────────────────────────────────────────────────────────────────
db = Database()
