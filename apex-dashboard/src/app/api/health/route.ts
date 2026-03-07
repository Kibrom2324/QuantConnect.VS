import { NextResponse } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const USE_MOCK = process.env.USE_MOCK_DATA === "true";

const MOCK_HEALTH = {
  status: "degraded",
  is_mock: true,
  services: {
    "Signal Engine":  { status: "running",  latency_ms: 42,  uptime_pct: 99.8 },
    "Risk Manager":   { status: "running",  latency_ms: 18,  uptime_pct: 99.9 },
    "Data Feed":      { status: "degraded", latency_ms: 340, uptime_pct: 97.2 },
    "Order Router":   { status: "running",  latency_ms: 11,  uptime_pct: 99.7 },
    "Position Mgr":   { status: "running",  latency_ms: 9,   uptime_pct: 100  },
    "MLflow":         { status: "stopped",  latency_ms: null, uptime_pct: 0   },
    "TimescaleDB":    { status: "running",  latency_ms: 5,   uptime_pct: 100  },
    "Redis":          { status: "running",  latency_ms: 2,   uptime_pct: 100  },
  },
};

export async function GET() {
  try {
    const res = await fetch(`${APEX}/dashboard/health`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return NextResponse.json({ ...data, is_mock: false });
  } catch {
    if (USE_MOCK) return NextResponse.json(MOCK_HEALTH);
    return NextResponse.json({
      status: "offline",
      is_mock: false,
      services: {},
    });
  }
}
