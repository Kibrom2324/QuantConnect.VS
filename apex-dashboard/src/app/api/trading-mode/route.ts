import { NextRequest, NextResponse } from "next/server";
import { readFileSync, writeFileSync, existsSync } from "fs";

const CONFIG_PATH = "/tmp/apex_trading_config.json";

interface TradingConfig {
  auto_trading_enabled: boolean;
  account_mode: "paper" | "live";
  min_confidence: number;
  market_hours_only: boolean;
  max_position_size_usd: number;
  max_daily_trades: number;
  trades_today: number;
  last_updated: string;
}

const DEFAULT_CONFIG: TradingConfig = {
  auto_trading_enabled: false,
  account_mode: "paper",
  min_confidence: 70,
  market_hours_only: true,
  max_position_size_usd: 5000,
  max_daily_trades: 10,
  trades_today: 0,
  last_updated: new Date().toISOString(),
};

function isMarketOpenFallback(): boolean {
  const now = new Date();
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return false;
  const utcH = now.getUTCHours();
  const utcM = now.getUTCMinutes();
  const total = utcH * 60 + utcM;
  // NYSE: 9:30–16:00 ET = 13:30–20:00 UTC (during EST)
  return total >= 870 && total < 1260;
}

async function isMarketOpen(): Promise<boolean> {
  const apiKey    = process.env.ALPACA_PAPER_KEY;
  const apiSecret = process.env.ALPACA_PAPER_SECRET;
  const baseUrl   = process.env.ALPACA_PAPER_URL ?? "https://paper-api.alpaca.markets";

  if (!apiKey || !apiSecret) return isMarketOpenFallback();

  try {
    const res = await fetch(`${baseUrl}/v2/clock`, {
      headers: {
        "APCA-API-KEY-ID":     apiKey,
        "APCA-API-SECRET-KEY": apiSecret,
      },
      signal: AbortSignal.timeout(2500),
      cache: "no-store",
    });
    if (res.ok) {
      const data = await res.json() as { is_open?: boolean };
      return data.is_open === true;
    }
  } catch { /* fall through */ }

  return isMarketOpenFallback();
}

function loadConfig(): TradingConfig {
  // Try Redis env first (if REDIS_URL is set, we'd call it — for now file fallback only)
  try {
    if (existsSync(CONFIG_PATH)) {
      const raw = readFileSync(CONFIG_PATH, "utf-8");
      const parsed = JSON.parse(raw) as Partial<TradingConfig>;
      return { ...DEFAULT_CONFIG, ...parsed };
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULT_CONFIG };
}

function saveConfig(cfg: TradingConfig): void {
  try {
    writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), "utf-8");
  } catch {
    /* ignore file write errors in read-only envs */
  }
}

export async function GET() {
  const cfg = loadConfig();
  return NextResponse.json({
    ...cfg,
    is_market_open: await isMarketOpen(),
    live_trading_available: !!(process.env.ALPACA_LIVE_KEY && process.env.ALPACA_LIVE_SECRET),
  });
}

export async function POST(req: NextRequest) {
  let body: Partial<TradingConfig>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const cfg = loadConfig();

  // Apply allowed fields
  if (typeof body.auto_trading_enabled === "boolean") {
    cfg.auto_trading_enabled = body.auto_trading_enabled;
  }
  if (body.account_mode === "paper" || body.account_mode === "live") {
    // Live mode only if ALPACA_LIVE_KEY is configured
    if (body.account_mode === "live" && !process.env.ALPACA_LIVE_KEY) {
      return NextResponse.json(
        { error: "ALPACA_LIVE_KEY not configured. Live trading unavailable." },
        { status: 403 }
      );
    }
    cfg.account_mode = body.account_mode;
  }
  if (typeof body.min_confidence === "number") {
    cfg.min_confidence = Math.max(50, Math.min(95, body.min_confidence));
  }
  if (typeof body.market_hours_only === "boolean") {
    cfg.market_hours_only = body.market_hours_only;
  }
  if (typeof body.max_position_size_usd === "number") {
    cfg.max_position_size_usd = Math.max(100, Math.min(50000, body.max_position_size_usd));
  }
  if (typeof body.max_daily_trades === "number") {
    cfg.max_daily_trades = Math.max(1, Math.min(100, body.max_daily_trades));
  }

  cfg.last_updated = new Date().toISOString();
  saveConfig(cfg);

  return NextResponse.json({ ...cfg, is_market_open: await isMarketOpen() });
}
