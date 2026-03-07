import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

const DEFAULT_SCHEDULE = {
  daily_retrain: { enabled: true,  cron: "0 2 * * *",   description: "Daily full retrain at 02:00 UTC" },
  weekly_optimize: { enabled: true, cron: "0 3 * * 0", description: "Sunday 03:00 UTC ensemble optimization" },
  hourly_monitor: { enabled: true,  cron: "0 * * * *",  description: "Hourly live model performance check" },
  auto_promote:   { enabled: false, threshold_sharpe: 1.2, threshold_hit_rate: 52, improvement_pct: 5 },
};

async function getRedis() {
  const client = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await client.connect();
  return client;
}

export async function GET() {
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    const raw = await r.get("apex:model_schedule");
    await r.disconnect();
    const schedule = raw ? JSON.parse(raw) : DEFAULT_SCHEDULE;
    return NextResponse.json({ schedule });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ schedule: DEFAULT_SCHEDULE, _mock: true });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    const current = await r.get("apex:model_schedule");
    const merged  = { ...(current ? JSON.parse(current) : DEFAULT_SCHEDULE), ...body };
    await r.set("apex:model_schedule", JSON.stringify(merged));
    await r.lPush("apex:agent_log", JSON.stringify({
      id:        `sched-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type:      "SCHEDULE_UPDATED",
      details:   "Training schedule updated via API",
      source:    "api",
    }));
    await r.disconnect();
    return NextResponse.json({ success: true, schedule: merged });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ success: true, schedule: body, _mock: true });
  }
}
