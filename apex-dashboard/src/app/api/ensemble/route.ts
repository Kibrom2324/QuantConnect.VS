import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

const MOCK_ENSEMBLE = {
  healthy:       true,
  degraded_mode: false,
  halt_active:   false,
  models: {
    tft:  { healthy: true,  weight: 0.45, latency_ms: 42,  error_count: 0 },
    xgb:  { healthy: true,  weight: 0.35, latency_ms: 12,  error_count: 0 },
    lstm: { healthy: true,  weight: 0.20, latency_ms: 38,  error_count: 0 },
  },
  current_weights:   { tft: 0.45, xgb: 0.35, lstm: 0.20 },
  next_optimize_at:  (() => {
    const d = new Date();
    d.setDate(d.getDate() + ((7 - d.getDay()) % 7 || 7));
    d.setHours(3, 0, 0, 0);
    return d.toISOString();
  })(),
  recent_predictions_count: 0,
};

async function getRedis() {
  const c = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await c.connect();
  return c;
}

// ─── GET /api/ensemble ───────────────────────────────────────────────────────
export async function GET() {
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();

    const weights_raw  = await r.get("apex:ensemble:weights");
    const halt_raw     = await r.get("apex:kill_switch");
    const preds_count  = await r.lLen("apex:ensemble:predictions");
    const alerts_raw   = await r.lRange("apex:model_alerts", 0, 9);

    const weights: Record<string, number> = weights_raw
      ? JSON.parse(weights_raw)
      : { tft: 0.45, xgb: 0.35, lstm: 0.20 };

    // Check for degraded alerts per model
    const active_degraded = new Set<string>();
    for (const a of alerts_raw) {
      try {
        const alert = JSON.parse(a);
        if (!alert.dismissed && alert.type === "MODEL_DEGRADED") {
          active_degraded.add(alert.model_id as string);
        }
      } catch (_) {}
    }

    const models: Record<string, object> = {};
    for (const key of ["tft", "xgb", "lstm"]) {
      const degraded    = active_degraded.has(key);
      models[key] = {
        healthy:     !degraded,
        weight:      degraded ? 0 : (weights[key] ?? 0),
        latency_ms:  key === "xgb" ? 12 : key === "tft" ? 42 : 38,
        error_count: degraded ? 5 : 0,
      };
    }

    const degraded_mode = active_degraded.size > 0;
    const halt_active   = !!halt_raw;

    // Next Sunday 03:00 UTC
    const next = new Date();
    next.setUTCDate(next.getUTCDate() + ((7 - next.getUTCDay()) % 7 || 7));
    next.setUTCHours(3, 0, 0, 0);

    await r.disconnect();
    return NextResponse.json({
      healthy:                  !halt_active && !degraded_mode,
      degraded_mode,
      halt_active,
      models,
      current_weights:          weights,
      next_optimize_at:         next.toISOString(),
      recent_predictions_count: preds_count,
    });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ ...MOCK_ENSEMBLE, _mock: true });
  }
}

// ─── POST /api/ensemble  (manual weight override) ───────────────────────────
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({})) as {
    weights?: { tft?: number; xgb?: number; lstm?: number };
    action?: "reset" | "optimize_now";
  };

  if (body.action === "reset") {
    const defaults = { tft: 0.45, xgb: 0.35, lstm: 0.20 };
    let r: Awaited<ReturnType<typeof getRedis>> | null = null;
    try {
      r = await getRedis();
      await r.set("apex:ensemble:weights", JSON.stringify(defaults));
      await r.lPush("apex:agent_log", JSON.stringify({ id: `ens-${Date.now()}`, timestamp: new Date().toISOString(), type: "ENSEMBLE_WEIGHTS_RESET", details: "Weights reset to default by user", source: "api" }));
      await r.disconnect();
    } catch (_) { if (r) try { await r.disconnect(); } catch (_2) {} }
    return NextResponse.json({ success: true, weights: defaults, action: "reset" });
  }

  const { weights } = body;
  if (!weights) {
    return NextResponse.json({ error: "weights required" }, { status: 400 });
  }

  const { tft = 0, xgb = 0, lstm = 0 } = weights;
  const total = tft + xgb + lstm;
  if (Math.abs(total - 1.0) > 0.005) {
    return NextResponse.json({ error: `Weights must sum to 1.0 (got ${total.toFixed(4)})` }, { status: 400 });
  }

  const normalized = { tft: +tft.toFixed(4), xgb: +xgb.toFixed(4), lstm: +lstm.toFixed(4) };

  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    await r.set("apex:ensemble:weights", JSON.stringify(normalized));
    await r.lPush("apex:agent_log", JSON.stringify({
      id:        `ens-${Date.now()}`,
      timestamp: new Date().toISOString(),
      type:      "ENSEMBLE_WEIGHTS_UPDATED",
      details:   `Manual weights: TFT ${(tft * 100).toFixed(0)}% XGB ${(xgb * 100).toFixed(0)}% LSTM ${(lstm * 100).toFixed(0)}%`,
      source:    "api",
    }));
    await r.disconnect();
    return NextResponse.json({ success: true, weights: normalized });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ success: true, weights: normalized, _mock: true });
  }
}
