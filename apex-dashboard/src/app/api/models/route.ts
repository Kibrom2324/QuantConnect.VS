import { NextRequest, NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

interface ModelVersion {
  model_id: string;
  model_type: string;
  version: string;
  status: string;
  val_sharpe: number;
  val_hit_rate: number;
  created_at: string;
  trained_by: string;
  mlflow_run_id?: string;
  artifact_path?: string;
  auto_promoted?: boolean;
  component_models?: Record<string, string>;
}

const MOCK_MODELS: ModelVersion[] = [
  { model_id: "ENS_v5", model_type: "ensemble", version: "5", status: "live", val_sharpe: 1.87, val_hit_rate: 58.3, created_at: new Date(Date.now() - 86400000).toISOString(), trained_by: "scheduler", component_models: { tft: "TFT_v5", xgb: "XGB_v5", lstm: "LSTM_v4" } },
  { model_id: "TFT_v5",  model_type: "tft",      version: "5", status: "staging",  val_sharpe: 1.72, val_hit_rate: 56.1, created_at: new Date(Date.now() - 2*86400000).toISOString(), trained_by: "scheduler", mlflow_run_id: "abc123" },
  { model_id: "XGB_v5",  model_type: "xgb",      version: "5", status: "staging",  val_sharpe: 1.61, val_hit_rate: 54.9, created_at: new Date(Date.now() - 2*86400000).toISOString(), trained_by: "scheduler", mlflow_run_id: "def456" },
  { model_id: "LSTM_v4", model_type: "lstm",      version: "4", status: "retired",  val_sharpe: 1.45, val_hit_rate: 52.7, created_at: new Date(Date.now() - 9*86400000).toISOString(), trained_by: "manual" },
];

async function getRedis() {
  const client = createClient({ socket: { host: REDIS_HOST, port: 6379 } });
  await client.connect();
  return client;
}

function groupModels(models: ModelVersion[]) {
  const g: Record<string, ModelVersion[]> = { tft: [], xgb: [], lstm: [], ensemble: [] };
  for (const m of models) { if (g[m.model_type]) g[m.model_type].push(m); }
  return g;
}

export async function GET() {
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    const ids = await r.sMembers("apex:models:all");
    const models: ModelVersion[] = [];
    for (const id of ids) {
      const raw = await r.get(`apex:models:${id}`);
      if (raw) { try { models.push(JSON.parse(raw)); } catch (_) {} }
    }
    models.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    // Redis is reachable but the set is empty → fall back to mock data
    if (models.length === 0) {
      await r.disconnect();
      return NextResponse.json({ models: MOCK_MODELS, grouped: groupModels(MOCK_MODELS), live_model: MOCK_MODELS[0], ab_test_active: null, alerts_count: 0, _mock: true });
    }
    const live_model    = models.find((m) => m.status === "live") ?? null;
    const ab_raw        = await r.get("apex:signal_engine:ab_test");
    const alerts_all    = await r.lRange("apex:model_alerts", 0, -1);
    const alerts_count  = alerts_all.filter((x) => { try { return !JSON.parse(x).dismissed; } catch { return false; } }).length;
    await r.disconnect();
    return NextResponse.json({ models, grouped: groupModels(models), live_model, ab_test_active: ab_raw ? JSON.parse(ab_raw) : null, alerts_count });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ models: MOCK_MODELS, grouped: groupModels(MOCK_MODELS), live_model: MOCK_MODELS[0], ab_test_active: null, alerts_count: 0, _mock: true });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({})) as { model_type?: string; triggered_by?: string };
  const { model_type = "ensemble", triggered_by = "manual" } = body;
  const ts       = Date.now();
  const model_id = `${model_type.toUpperCase()}_v${ts}`;
  const job_id   = `job-${ts}`;
  let r: Awaited<ReturnType<typeof getRedis>> | null = null;
  try {
    r = await getRedis();
    const job = { job_id, model_id, model_type, version: String(ts), triggered_by, created_at: new Date().toISOString(), status: "queued" };
    await r.lPush("apex:training_jobs", JSON.stringify(job));
    await r.lPush("apex:agent_log", JSON.stringify({ id: job_id, timestamp: job.created_at, type: "TRAINING_QUEUED", details: `${model_type.toUpperCase()} training queued by ${triggered_by}`, source: "api" }));
    await r.disconnect();
    return NextResponse.json({ job_id, model_id, status: "queued" });
  } catch (_) {
    if (r) try { await r.disconnect(); } catch (_2) {}
    return NextResponse.json({ job_id, model_id, status: "queued", _mock: true });
  }
}
