import { NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

async function getRedis() {
  const client = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await client.connect();
  return client;
}

const MOCK_ALERTS = [
  {
    id: "alert-mock-1",
    timestamp: new Date(Date.now() - 3600000).toISOString(),
    type: "MODEL_DEGRADED",
    severity: "MEDIUM",
    model_id: "TFT_v4",
    details: "Live Sharpe 0.92 is 22% below validation Sharpe 1.18",
    action_required: false,
    dismissed: false,
  },
];

export async function GET() {
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    const raw    = await r.lRange("apex:model_alerts", 0, 99);
    const alerts = raw
      .map((x) => { try { return JSON.parse(x); } catch { return null; } })
      .filter(Boolean);
    await r.disconnect();
    return NextResponse.json({ alerts, count: alerts.length });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ alerts: MOCK_ALERTS, count: MOCK_ALERTS.length, _mock: true });
  }
}
