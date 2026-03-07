import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

// ── Types ─────────────────────────────────────────────────────────────────
export interface AgentLogEntry {
  id:        string;
  type:      string;
  details:   string;
  symbol?:   string;
  price?:    number;
  timestamp: string;
}

// ── Redis helper ──────────────────────────────────────────────────────────
let redis: ReturnType<typeof createClient> | null = null;
const REDIS_KEY = "apex:agent_log";

async function getRedis() {
  if (!redis) {
    redis = createClient({ url: process.env.REDIS_URL ?? "redis://localhost:6379" });
    await redis.connect().catch(() => { redis = null; });
  }
  return redis;
}

// ── Mock data generator ────────────────────────────────────────────────────
function generateMockEntries(count: number): AgentLogEntry[] {
  const now   = Date.now();
  const types = [
    { type: "SIGNAL",    details: "NVDA UP 82% confidence",               symbol: "NVDA" },
    { type: "RISK_PASS", details: "Size $4,928 < $5,000 limit",           symbol: "NVDA" },
    { type: "SUBMITTED", details: "BUY 10 NVDA MARKET",                   symbol: "NVDA", price: 492.80 },
    { type: "FILLED",    details: "BUY 10 NVDA filled",                   symbol: "NVDA", price: 492.80 },
    { type: "SIGNAL",    details: "AAPL DOWN 71% confidence",             symbol: "AAPL" },
    { type: "EVALUATED", details: "AAPL — below threshold 71% < 75%",     symbol: "AAPL" },
    { type: "EVALUATED", details: "MSFT — market hours check passed",     symbol: "MSFT" },
    { type: "ENGINE",    details: "Auto-trading engine enabled (paper)",   symbol: undefined },
    { type: "MARKET",    details: "NYSE session opened",                   symbol: undefined },
    { type: "RISK_FAIL", details: "TSLA size $12,400 > $5,000 limit",     symbol: "TSLA" },
    { type: "SIGNAL",    details: "MSFT UP 77% confidence",               symbol: "MSFT" },
    { type: "SUBMITTED", details: "BUY 8 MSFT MARKET",                    symbol: "MSFT", price: 419.30 },
    { type: "FILLED",    details: "BUY 8 MSFT filled",                    symbol: "MSFT", price: 419.10 },
    { type: "RISK_PASS", details: "Portfolio risk within limits",          symbol: undefined },
    { type: "ENGINE",    details: "Signal evaluation cycle #47",          symbol: undefined },
    { type: "EVALUATED", details: "AMD — confidence 61% below threshold", symbol: "AMD" },
    { type: "SIGNAL",    details: "SPY HOLD 55% confidence",              symbol: "SPY" },
    { type: "EVALUATED", details: "SPY — HOLD signal, no action taken",   symbol: "SPY" },
    { type: "MARKET",    details: "Pre-market session started",            symbol: undefined },
    { type: "ENGINE",    details: "Kill switch check — inactive",         symbol: undefined },
  ];

  const entries: AgentLogEntry[] = [];
  for (let i = 0; i < count; i++) {
    const template = types[i % types.length];
    const ageMs    = i * 120_000 + Math.random() * 60_000; // 2min apart
    entries.push({
      id:        `mock-${i}-${Date.now()}`,
      type:      template.type,
      details:   template.details,
      symbol:    template.symbol,
      price:     template.price,
      timestamp: new Date(now - ageMs).toISOString(),
    });
  }
  return entries;
}

// ── GET — last N entries ────────────────────────────────────────────────────
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit = Math.min(parseInt(searchParams.get("limit") ?? "100"), 500);

  try {
    const r = await getRedis();
    if (r) {
      const raw = await r.lRange(REDIS_KEY, 0, limit - 1);
      const entries = raw
        .map(s => { try { return JSON.parse(s) as AgentLogEntry; } catch { return null; } })
        .filter(Boolean) as AgentLogEntry[];

      if (entries.length > 0) {
        return NextResponse.json({ entries, is_mock: false });
      }
    }
  } catch { /* Redis not available */ }

  return NextResponse.json({
    entries: generateMockEntries(Math.min(limit, 50)),
    is_mock: true,
  });
}

// ── POST — add new entry ────────────────────────────────────────────────────
export async function POST(req: NextRequest) {
  try {
    const body   = await req.json() as Omit<AgentLogEntry, "id" | "timestamp">;
    const entry: AgentLogEntry = {
      id:        crypto.randomUUID(),
      type:      body.type ?? "ENGINE",
      details:   body.details ?? "",
      symbol:    body.symbol,
      price:     body.price,
      timestamp: new Date().toISOString(),
    };

    try {
      const r = await getRedis();
      if (r) {
        await r.lPush(REDIS_KEY, JSON.stringify(entry));
        await r.lTrim(REDIS_KEY, 0, 999); // keep max 1000
      }
    } catch { /* Redis not available — log to console */ }

    console.log("[APEX Agent Log]", entry.type, entry.details);
    return NextResponse.json({ ok: true, entry }, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }
}
