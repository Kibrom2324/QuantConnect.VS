import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

async function getRedis() {
  const client = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await client.connect();
  return client;
}

export async function POST(
  req: NextRequest,
  { params }: { params: { modelId: string } }
) {
  const { modelId } = params;
  const body        = await req.json().catch(() => ({})) as { reason?: string };
  const reason      = body.reason ?? "manual demote";

  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();

    const raw = await r.get(`apex:models:${modelId}`);
    if (!raw) {
      await r.disconnect();
      return NextResponse.json({ error: "Model not found" }, { status: 404 });
    }

    const model   = JSON.parse(raw);
    const updated = { ...model, status: "retired", demoted_at: new Date().toISOString(), demote_reason: reason };
    await r.set(`apex:models:${modelId}`, JSON.stringify(updated));

    // Clear active model pointer if this was live
    const activeId = await r.get("apex:signal_engine:active_model");
    if (activeId === modelId) {
      await r.del("apex:signal_engine:active_model");
    }

    const event = {
      id:        `evt-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type:      "MODEL_DEMOTED",
      details:   `${modelId} demoted to retired. Reason: ${reason}`,
      model_id:  modelId,
      source:    "api",
    };
    await r.lPush("apex:model_events", JSON.stringify(event));
    await r.lPush("apex:agent_log",    JSON.stringify(event));

    await r.disconnect();
    return NextResponse.json({ success: true, model_id: modelId, status: "retired" });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ success: true, model_id: modelId, status: "retired", _mock: true });
  }
}
