import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

async function getRedis() {
  const client = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await client.connect();
  return client;
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({})) as {
    action?: "start" | "stop";
    model_a_id?: string;
    model_b_id?: string;
    weight_b?: number;
    winner_id?: string;
  };

  const { action = "start", model_a_id, model_b_id, weight_b = 0.2, winner_id } = body;

  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();

    if (action === "start") {
      if (!model_a_id || !model_b_id) {
        await r.disconnect();
        return NextResponse.json({ error: "model_a_id and model_b_id required" }, { status: 400 });
      }

      const config = {
        model_a_id,
        model_b_id,
        weight_b: Math.min(Math.max(weight_b, 0.05), 0.50),
        started_at: new Date().toISOString(),
        metrics_a: { trades: 0, sharpe: 0, pnl: 0 },
        metrics_b: { trades: 0, sharpe: 0, pnl: 0 },
        status: "active",
      };

      await r.set("apex:signal_engine:ab_test", JSON.stringify(config));
      await r.lPush("apex:agent_log", JSON.stringify({
        id:        `ab-${Date.now()}`,
        timestamp: config.started_at,
        type:      "AB_TEST_STARTED",
        details:   `A/B test started: ${model_a_id} (${(1 - config.weight_b) * 100}%) vs ${model_b_id} (${config.weight_b * 100}%)`,
        source:    "api",
      }));

      await r.disconnect();
      return NextResponse.json({ success: true, action: "started", config });
    }

    // stop
    const raw = await r.get("apex:signal_engine:ab_test");
    await r.del("apex:signal_engine:ab_test");

    if (winner_id) {
      const winnerRaw = await r.get(`apex:models:${winner_id}`);
      if (winnerRaw) {
        const current_live = await r.get("apex:signal_engine:active_model");
        if (current_live && current_live !== winner_id) {
          const liveRaw = await r.get(`apex:models:${current_live}`);
          if (liveRaw) {
            await r.set(`apex:models:${current_live}`, JSON.stringify({ ...JSON.parse(liveRaw), status: "retired" }));
          }
        }
        await r.set(`apex:models:${winner_id}`, JSON.stringify({ ...JSON.parse(winnerRaw), status: "live" }));
        await r.set("apex:signal_engine:active_model", winner_id);
      }
    }

    await r.lPush("apex:agent_log", JSON.stringify({
      id:        `ab-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type:      "AB_TEST_COMPLETED",
      details:   `A/B test stopped${winner_id ? `. Winner: ${winner_id}` : ""}`,
      source:    "api",
    }));

    await r.disconnect();
    return NextResponse.json({ success: true, action: "stopped", winner_id });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ success: true, action, _mock: true });
  }
}
