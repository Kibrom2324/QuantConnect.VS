/**
 * GET /api/mlflow/metrics
 *
 * Tiny Prometheus exporter proxy for MLflow.
 * MLflow doesn't natively export /metrics, so this route calls
 * MLflow's /health endpoint and converts the result to Prometheus
 * exposition format.  Prometheus scrapes this instead of MLflow directly.
 *
 * Usage in prometheus.yml:
 *   - job_name: mlflow
 *     static_configs:
 *       - targets: ["apex-dashboard:3001"]
 *     metrics_path: /api/mlflow/metrics
 */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const MLFLOW_INTERNAL_URL = process.env.MLFLOW_URL ?? "http://mlflow:5000";

export async function GET() {
  let isUp = 0;

  try {
    const res = await fetch(`${MLFLOW_INTERNAL_URL}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) isUp = 1;
  } catch {
    isUp = 0;
  }

  const body = [
    "# HELP mlflow_up MLflow tracking server reachability (1 = up, 0 = down)",
    "# TYPE mlflow_up gauge",
    `mlflow_up ${isUp}`,
    "",
  ].join("\n");

  return new NextResponse(body, {
    status: 200,
    headers: { "Content-Type": "text/plain; version=0.0.4; charset=utf-8" },
  });
}
