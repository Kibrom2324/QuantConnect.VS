import { NextRequest } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

// ── Types ──────────────────────────────────────────────────────────────────
interface AgentLogEntry {
  id:        string;
  type:      string;
  details:   string;
  symbol?:   string;
  price?:    number;
  timestamp: string;
}

// ── Redis helper ────────────────────────────────────────────────────────────
const REDIS_KEY = "apex:agent_log";

async function tryGetRedis() {
  try {
    const r = createClient({ url: process.env.REDIS_URL ?? "redis://localhost:6379" });
    await r.connect();
    return r;
  } catch {
    return null;
  }
}

// ── Mock entry generator ────────────────────────────────────────────────────
let mockCounter = 100;
const MOCK_TEMPLATES = [
  { type: "ENGINE",    details: "Signal evaluation cycle",         symbol: undefined },
  { type: "SIGNAL",    details: "NVDA UP 79% confidence (TFT)",   symbol: "NVDA"    },
  { type: "RISK_PASS", details: "Position sizing within limits",   symbol: "NVDA"    },
  { type: "FILLED",    details: "BUY 10 NVDA filled @ $492.80",   symbol: "NVDA", price: 492.80 },
  { type: "EVALUATED", details: "AAPL — below threshold 68%",     symbol: "AAPL"    },
  { type: "SIGNAL",    details: "SPY HOLD — no action taken",      symbol: "SPY"     },
  { type: "ENGINE",    details: "Kill switch check — inactive",   symbol: undefined },
  { type: "MARKET",    details: "NYSE session active",             symbol: undefined },
];

function makeMockEntry(): AgentLogEntry {
  const tpl = MOCK_TEMPLATES[mockCounter % MOCK_TEMPLATES.length];
  mockCounter++;
  return {
    id:        `mock-live-${mockCounter}-${Date.now()}`,
    type:      tpl.type,
    details:   tpl.details,
    symbol:    tpl.symbol,
    price:     tpl.price,
    timestamp: new Date().toISOString(),
  };
}

// ── GET — SSE stream ────────────────────────────────────────────────────────
export async function GET(_req: NextRequest) {
  const encoder = new TextEncoder();
  let lastId    = "";
  let closed    = false;

  const redisClient = await tryGetRedis();

  const stream = new ReadableStream({
    async start(controller) {
      // Send initial heartbeat so browser considers connection open
      const hb = encoder.encode(`: heartbeat\n\n`);
      controller.enqueue(hb);

      async function poll() {
        if (closed) return;

        try {
          let newEntries: AgentLogEntry[] = [];

          if (redisClient) {
            // Fetch latest 10 entries from Redis
            const raw = await redisClient.lRange(REDIS_KEY, 0, 9);
            const entries = raw
              .map(s => { try { return JSON.parse(s) as AgentLogEntry; } catch { return null; } })
              .filter(Boolean) as AgentLogEntry[];

            if (entries.length > 0 && entries[0]?.id !== lastId) {
              const lastIdx = lastId ? entries.findIndex(e => e.id === lastId) : entries.length;
              newEntries    = lastIdx > 0 ? entries.slice(0, lastIdx) : [];
              if (entries[0]) lastId = entries[0].id;
            }
          } else {
            // Mock mode — occasionally emit a fake log entry
            if (Math.random() < 0.35) {
              newEntries = [makeMockEntry()];
            }
          }

          if (newEntries.length > 0) {
            const payload = encoder.encode(`data: ${JSON.stringify(newEntries)}\n\n`);
            controller.enqueue(payload);
          } else {
            // Send keep-alive comment
            controller.enqueue(encoder.encode(`: ping\n\n`));
          }
        } catch {
          controller.enqueue(encoder.encode(`: error\n\n`));
        }

        if (!closed) {
          setTimeout(poll, 2000);
        }
      }

      // Start polling after a brief delay
      setTimeout(poll, 1000);
    },
    cancel() {
      closed = true;
      redisClient?.disconnect().catch(() => {});
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type":                "text/event-stream",
      "Cache-Control":               "no-cache, no-transform",
      "Connection":                  "keep-alive",
      "X-Accel-Buffering":           "no",   // disable nginx buffering
    },
  });
}
