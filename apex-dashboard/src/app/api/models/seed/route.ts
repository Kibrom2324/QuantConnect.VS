/**
 * POST /api/models/seed
 *
 * Seeds Redis with realistic demo model versions so the Models page always
 * has something to show during development or when no real training has run.
 *
 * Returns the seeded models array regardless of whether Redis is available.
 */
import { NextResponse } from "next/server";
import { createClient } from "redis";

export const dynamic = "force-dynamic";

const REDIS_HOST = process.env.REDIS_HOST ?? "localhost";

interface ModelVersion {
  model_id:           string;
  model_type:         string;
  version:            string;
  status:             string;
  val_sharpe:         number;
  val_hit_rate:       number;
  created_at:         string;
  trained_by:         string;
  mlflow_run_id?:     string;
  artifact_path?:     string;
  auto_promoted?:     boolean;
  component_models?:  Record<string, string>;
}

const now = Date.now();
const d   = (days: number) => new Date(now - days * 86_400_000).toISOString();

function groupModels(models: ModelVersion[]) {
  const g: Record<string, ModelVersion[]> = { tft: [], xgb: [], lstm: [], ensemble: [] };
  for (const m of models) { if (g[m.model_type]) g[m.model_type].push(m); }
  return g;
}

const SEED_MODELS: ModelVersion[] = [  {
    model_id:          "ENS_v3",
    model_type:        "ensemble",
    version:           "3",
    status:            "live",
    val_sharpe:        1.87,
    val_hit_rate:      58.3,
    created_at:        d(1),
    trained_by:        "scheduler",
    auto_promoted:     true,
    component_models:  { tft: "TFT_v3", xgb: "XGB_v3", lstm: "LSTM_v3" },
  },
  {
    model_id:      "TFT_v3",
    model_type:    "tft",
    version:       "3",
    status:        "staging",
    val_sharpe:    1.72,
    val_hit_rate:  56.1,
    created_at:    d(2),
    trained_by:    "scheduler",
    mlflow_run_id: "tft-run-001",
    artifact_path: "mlflow-artifacts/tft_v3",
  },
  {
    model_id:      "XGB_v3",
    model_type:    "xgb",
    version:       "3",
    status:        "staging",
    val_sharpe:    1.61,
    val_hit_rate:  54.9,
    created_at:    d(2),
    trained_by:    "scheduler",
    mlflow_run_id: "xgb-run-001",
    artifact_path: "mlflow-artifacts/xgb_v3",
  },
  {
    model_id:      "LSTM_v3",
    model_type:    "lstm",
    version:       "3",
    status:        "staging",
    val_sharpe:    1.55,
    val_hit_rate:  53.4,
    created_at:    d(3),
    trained_by:    "scheduler",
    mlflow_run_id: "lstm-run-001",
  },
  {
    model_id:      "TFT_v2",
    model_type:    "tft",
    version:       "2",
    status:        "demoted",
    val_sharpe:    1.43,
    val_hit_rate:  51.8,
    created_at:    d(10),
    trained_by:    "manual",
    mlflow_run_id: "tft-run-000",
  },
  {
    model_id:      "ENS_v2",
    model_type:    "ensemble",
    version:       "2",
    status:        "retired",
    val_sharpe:    1.31,
    val_hit_rate:  50.2,
    created_at:    d(20),
    trained_by:    "manual",
    component_models: { tft: "TFT_v2" },
  },
];

export async function POST() {
  let redis: ReturnType<typeof createClient> | null = null;
  let seededToRedis = false;

  try {
    redis = createClient({ socket: { host: REDIS_HOST, port: 6379, connectTimeout: 3000 } });
    await redis.connect();

    // Write each model into Redis
    for (const model of SEED_MODELS) {
      await redis.set(`apex:models:${model.model_id}`, JSON.stringify(model));
      await redis.sAdd("apex:models:all", model.model_id);
    }

    // Set live model pointer
    await redis.set("apex:signal_engine:active_model", "ENS_v3");

    await redis.disconnect();
    seededToRedis = true;
  } catch (err) {
    if (redis) try { await (redis as any).disconnect(); } catch (_) {}
    console.warn("[models/seed] Redis unavailable — returning mock data only:", err);
  }

  return NextResponse.json({
    seeded:          SEED_MODELS.length,
    seeded_to_redis: seededToRedis,
    models:          SEED_MODELS,
    grouped:         groupModels(SEED_MODELS),
    live_model:      SEED_MODELS[0],
    ab_test_active:  null,
    alerts_count:    0,
    _mock:           !seededToRedis,
  });
}
