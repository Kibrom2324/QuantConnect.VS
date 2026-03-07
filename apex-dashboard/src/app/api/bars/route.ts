import { NextRequest, NextResponse } from "next/server";

// ── TimescaleDB connection (pg) ────────────────────────────────────────────
const DB_URL =
  process.env.DATABASE_URL ??
  "postgresql://apex_user:apex_pass@localhost:15432/apex";

/** Map dashboard timeframe codes → PostgreSQL bucket interval strings */
const TF_TO_INTERVAL: Record<string, string> = {
  "1Min":  "1 minute",
  "5Min":  "5 minutes",
  "15Min": "15 minutes",
  "1Hour": "1 hour",
  "4Hour": "4 hours",
  "1Day":  "1 day",
};

async function queryTimescaleDB(
  symbol:    string,
  timeframe: string,
  limit:     number,
): Promise<{ time: number; open: number; high: number; low: number; close: number; volume: number }[] | null> {
  const interval = TF_TO_INTERVAL[timeframe];
  if (!interval) return null;

  try {
    const { Pool } = await import("pg") as typeof import("pg");
    const pool = new Pool({ connectionString: DB_URL, max: 2 });

    const { rows } = await pool.query(
      `SELECT
         time_bucket($1::interval, time) AS time,
         FIRST(open,  time)              AS open,
         MAX(high)                       AS high,
         MIN(low)                        AS low,
         LAST(close,  time)              AS close,
         SUM(volume)                     AS volume
       FROM ohlcv_bars
       WHERE symbol = $2
         AND time >= NOW() - ($3 * $1::interval)
       GROUP BY 1
       ORDER BY 1 ASC
       LIMIT $3`,
      [interval, symbol, limit],
    );

    await pool.end();

    if (rows.length === 0) return null;

    return rows.map((r: Record<string, unknown>) => ({
      time:   Math.floor(new Date(r.time as string).getTime() / 1000),
      open:   parseFloat(r.open as string),
      high:   parseFloat(r.high as string),
      low:    parseFloat(r.low  as string),
      close:  parseFloat(r.close as string),
      volume: parseInt(r.volume as string, 10),
    }));
  } catch {
    // DB unreachable — fall through to Alpaca/mock
    return null;
  }
}

// ── Starting prices per symbol ─────────────────────────────────────────────
const BASE_PRICES: Record<string, number> = {
  NVDA: 485,  AAPL: 179,  MSFT: 415,
  TSLA: 248,  AMZN: 198,  GOOGL: 172,
  META: 520,  AMD:  180,  SPY:   521,
  QQQ:  441,
};

// ── Mock OHLCV generator ───────────────────────────────────────────────────
function generateMockBars(
  symbol: string,
  timeframe: string,
  limit: number
): { time: number; open: number; high: number; low: number; close: number; volume: number }[] {
  const startPrice = BASE_PRICES[symbol.toUpperCase()] ?? 100;
  const bars: ReturnType<typeof generateMockBars> = [];

  const intervalMs = timeframeToMs(timeframe);
  const now = Math.floor(Date.now() / 1000);
  const startTs = now - (limit - 1) * Math.floor(intervalMs / 1000);

  let price = startPrice;
  const volatility = startPrice * 0.0015;

  for (let i = 0; i < limit; i++) {
    const ts = startTs + i * Math.floor(intervalMs / 1000);
    const drift = (Math.random() - 0.495) * volatility * 2;
    const open  = price;
    const move  = drift;
    const upWick   = Math.random() * volatility * 1.5;
    const downWick = Math.random() * volatility * 1.5;
    const close = Math.max(open * 0.97, Math.min(open * 1.03, open + move));
    const high  = Math.max(open, close) + upWick;
    const low   = Math.min(open, close) - downWick;
    const volume = Math.floor(500_000 + Math.random() * 1_500_000);

    bars.push({
      time:   ts,
      open:   Math.round(open  * 100) / 100,
      high:   Math.round(high  * 100) / 100,
      low:    Math.round(low   * 100) / 100,
      close:  Math.round(close * 100) / 100,
      volume,
    });

    price = close;
  }

  return bars;
}

function timeframeToMs(tf: string): number {
  const map: Record<string, number> = {
    "1Min":  60_000,
    "5Min":  300_000,
    "15Min": 900_000,
    "1Hour": 3_600_000,
    "4Hour": 14_400_000,
    "1Day":  86_400_000,
    // Alpaca aliases
    "1T":    60_000,
    "5T":    300_000,
    "15T":   900_000,
    "60T":   3_600_000,
    "1D":    86_400_000,
  };
  return map[tf] ?? 900_000;
}

// ── Alpaca timeframe string ────────────────────────────────────────────────
function toAlpacaTimeframe(tf: string): string {
  const map: Record<string, string> = {
    "1Min":  "1Min",
    "5Min":  "5Min",
    "15Min": "15Min",
    "1Hour": "1Hour",
    "4Hour": "4Hour",
    "1Day":  "1Day",
  };
  return map[tf] ?? "15Min";
}

// ── Route handler ──────────────────────────────────────────────────────────
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const symbol    = (searchParams.get("symbol") ?? "NVDA").toUpperCase();
  const timeframe = searchParams.get("timeframe") ?? "15Min";
  const limit     = Math.min(parseInt(searchParams.get("limit") ?? "200"), 500);

  const USE_MOCK  = process.env.USE_MOCK_DATA === "true";
  const apiKey    = process.env.ALPACA_PAPER_KEY;
  const apiSecret = process.env.ALPACA_PAPER_SECRET;
  const dataBase  = process.env.ALPACA_DATA_URL ?? "https://data.alpaca.markets";

  // ── 1. Try TimescaleDB ─────────────────────────────────────────────────
  if (!USE_MOCK) {
    const dbBars = await queryTimescaleDB(symbol, timeframe, limit);
    if (dbBars && dbBars.length > 0) {
      return NextResponse.json({ bars: dbBars, symbol, timeframe, is_mock: false, source: "timescaledb" });
    }
  }

  // ── 2. Return mock immediately if configured ───────────────────────────
  if (USE_MOCK || !apiKey || !apiSecret) {
    return NextResponse.json({
      bars:    generateMockBars(symbol, timeframe, limit),
      symbol,
      timeframe,
      is_mock: true,
      source:  "mock",
    });
  }

  // ── 3. Try Alpaca ─────────────────────────────────────────────────────
  try {
    const alpacaTf = toAlpacaTimeframe(timeframe);
    const url = `${dataBase}/v2/stocks/${encodeURIComponent(symbol)}/bars?timeframe=${alpacaTf}&limit=${limit}&feed=iex&adjustment=raw`;

    const res = await fetch(url, {
      headers: {
        "APCA-API-KEY-ID":     apiKey,
        "APCA-API-SECRET-KEY": apiSecret,
        Accept:                "application/json",
      },
      signal: AbortSignal.timeout(5000),
      cache:  "no-store",
    });

    if (res.ok) {
      const data = await res.json() as {
        bars: { t: string; o: number; h: number; l: number; c: number; v: number }[];
      };

      const bars = (data.bars ?? []).map(b => ({
        time:   Math.floor(new Date(b.t).getTime() / 1000),
        open:   b.o,
        high:   b.h,
        low:    b.l,
        close:  b.c,
        volume: b.v,
      }));

      if (bars.length > 0) {
        return NextResponse.json({ bars, symbol, timeframe, is_mock: false, source: "alpaca" });
      }
    }
  } catch {
    // Alpaca unreachable — fall through to mock
  }

  // ── 4. Mock fallback ───────────────────────────────────────────────────
  return NextResponse.json({
    bars:    generateMockBars(symbol, timeframe, limit),
    symbol,
    timeframe,
    is_mock: true,
    source:  "mock",
  });
}
