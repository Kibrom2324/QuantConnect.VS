/**
 * tests/e2e/timesfm.spec.ts
 * ─────────────────────────
 * End-to-end integration tests for the APEX TimesFM inference service.
 *
 * Tests hit the real service endpoints — run AFTER:
 *   docker compose up -d timesfm-service
 *   (wait for /health to return { "model_loaded": true })
 *
 * Environment:
 *   TIMESFM_URL  — base URL of the TimesFM service (default: http://localhost:8010)
 *   BASE_URL     — APEX dashboard base URL           (default: http://localhost:3001)
 */

import { test, expect, APIRequestContext } from "@playwright/test";

// ── Config ────────────────────────────────────────────────────────────────────

const TIMESFM_URL = process.env.TIMESFM_URL ?? "http://localhost:8010";
const BASE_URL    = process.env.BASE_URL    ?? "http://localhost:3001";

/** Maximum acceptable latency for a single TimesFM prediction (ms). */
const MAX_LATENCY_MS = 5_000;

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** 30 synthetic NVDA OHLCV bars at ~480 price level. */
const NVDA_BARS = Array.from({ length: 30 }, (_, i) => ({
  time:   new Date(Date.now() - (30 - i) * 60_000).toISOString(),
  open:   480 + Math.sin(i * 0.3) * 5,
  high:   485 + Math.sin(i * 0.3) * 5,
  low:    475 + Math.sin(i * 0.3) * 5,
  close:  482 + Math.sin(i * 0.3) * 5,
  volume: 1_200_000 + i * 10_000,
}));

const VALID_PREDICT_PAYLOAD = {
  symbol:   "NVDA",
  horizon:  "next_1h",
  bars:     NVDA_BARS,
  model_id: "timesfm_v1",
};

// ── Helper ────────────────────────────────────────────────────────────────────

/**
 * Poll the TimesFM /ready endpoint until the model is loaded.
 * Times out after 180 s (model download can take ~2 min on first run).
 */
async function waitUntilReady(request: APIRequestContext, timeoutMs = 180_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await request.get(`${TIMESFM_URL}/ready`, { timeout: 5_000 });
      if (res.status() === 200) return;
    } catch {
      // service not yet up — keep polling
    }
    await new Promise((r) => setTimeout(r, 5_000));
  }
  throw new Error(`TimesFM service not ready after ${timeoutMs / 1000}s`);
}

// ── Suite: Health & Liveness ──────────────────────────────────────────────────

test.describe("TimesFM Service — Health", () => {
  test("GET /health returns HTTP 200", async ({ request }) => {
    const res = await request.get(`${TIMESFM_URL}/health`);
    expect(res.status(), `Expected 200, got ${res.status()}`).toBe(200);
  });

  test("GET /health body has expected shape", async ({ request }) => {
    const res  = await request.get(`${TIMESFM_URL}/health`);
    const body = await res.json() as Record<string, unknown>;

    expect(body).toHaveProperty("status");
    expect(body).toHaveProperty("model_loaded");
    expect(body).toHaveProperty("service");
    expect(body["service"]).toBe("timesfm_service");
  });
});

// ── Suite: Readiness ──────────────────────────────────────────────────────────

test.describe("TimesFM Service — Readiness", () => {
  test("GET /ready returns HTTP 200 when model is loaded", async ({ request }) => {
    // Wait up to 3 minutes for model initialisation
    await waitUntilReady(request);

    const res = await request.get(`${TIMESFM_URL}/ready`);
    expect(res.status(), "/ready should return 200 once model is loaded").toBe(200);
  });

  test("GET /ready body confirms model loaded", async ({ request }) => {
    await waitUntilReady(request);

    const res  = await request.get(`${TIMESFM_URL}/ready`);
    const body = await res.json() as Record<string, unknown>;

    expect(body["ready"]).toBe(true);
    expect(body).toHaveProperty("model_id");
  });
});

// ── Suite: Prediction ─────────────────────────────────────────────────────────

test.describe("TimesFM Service — POST /predict", () => {
  test.beforeAll(async ({ request }) => {
    // Ensure model is loaded before running inference tests
    await waitUntilReady(request, 180_000);
  });

  test("Returns HTTP 200 for valid NVDA request", async ({ request }) => {
    const res = await request.post(`${TIMESFM_URL}/predict`, {
      data: VALID_PREDICT_PAYLOAD,
    });
    expect(res.status(), `Expected 200, got ${res.status()}: ${await res.text()}`).toBe(200);
  });

  test("predicted_value is a finite positive number", async ({ request }) => {
    const res  = await request.post(`${TIMESFM_URL}/predict`, { data: VALID_PREDICT_PAYLOAD });
    const body = await res.json() as Record<string, unknown>;

    const pv = Number(body["predicted_value"]);
    expect(Number.isFinite(pv), `predicted_value must be finite, got: ${pv}`).toBe(true);
    expect(pv, "predicted_value for a ~$480 stock should be > 0").toBeGreaterThan(0);
  });

  test("confidence is between 0 and 1 (inclusive)", async ({ request }) => {
    const res  = await request.post(`${TIMESFM_URL}/predict`, { data: VALID_PREDICT_PAYLOAD });
    const body = await res.json() as Record<string, unknown>;

    const conf = Number(body["confidence"]);
    expect(Number.isFinite(conf), `confidence must be numeric, got: ${conf}`).toBe(true);
    expect(conf, "confidence must be >= 0").toBeGreaterThanOrEqual(0);
    expect(conf, "confidence must be <= 1").toBeLessThanOrEqual(1);
  });

  test(`latency_ms is under ${MAX_LATENCY_MS} ms`, async ({ request }) => {
    const res  = await request.post(`${TIMESFM_URL}/predict`, { data: VALID_PREDICT_PAYLOAD });
    const body = await res.json() as Record<string, unknown>;

    const lat = Number(body["latency_ms"]);
    expect(Number.isFinite(lat), "latency_ms must be present and numeric").toBe(true);
    expect(lat, `latency_ms ${lat} exceeded budget of ${MAX_LATENCY_MS} ms`).toBeLessThan(MAX_LATENCY_MS);
  });

  test("Response contains all required fields", async ({ request }) => {
    const res  = await request.post(`${TIMESFM_URL}/predict`, { data: VALID_PREDICT_PAYLOAD });
    const body = await res.json() as Record<string, unknown>;

    const REQUIRED = ["symbol", "horizon", "predicted_value", "confidence", "timestamp", "model_id", "latency_ms"];
    for (const field of REQUIRED) {
      expect(body, `Response missing field: "${field}"`).toHaveProperty(field);
    }
    expect(body["symbol"]).toBe("NVDA");
    expect(body["horizon"]).toBe("next_1h");
  });

  test("Returns HTTP 422 when bars array is missing", async ({ request }) => {
    const res = await request.post(`${TIMESFM_URL}/predict`, {
      data: {
        symbol:   "NVDA",
        horizon:  "next_1h",
        // bars intentionally omitted
        model_id: "timesfm_v1",
      },
    });
    expect(res.status(), "Missing bars should return 422 Unprocessable Entity").toBe(422);
  });

  test("Returns HTTP 422 when bars array is empty", async ({ request }) => {
    const res = await request.post(`${TIMESFM_URL}/predict`, {
      data: { ...VALID_PREDICT_PAYLOAD, bars: [] },
    });
    expect(res.status(), "Empty bars array should return 422").toBe(422);
  });

  test("Returns HTTP 422 when symbol is missing", async ({ request }) => {
    const { symbol: _omit, ...noSymbol } = VALID_PREDICT_PAYLOAD;
    const res = await request.post(`${TIMESFM_URL}/predict`, { data: noSymbol });
    expect(res.status(), "Missing symbol should return 422").toBe(422);
  });
});

// ── Suite: Model API ──────────────────────────────────────────────────────────

test.describe("TimesFM Service — Model Info", () => {
  test("GET /model/info returns model metadata", async ({ request }) => {
    const res  = await request.get(`${TIMESFM_URL}/model/info`);
    expect(res.status()).toBe(200);

    const body = await res.json() as Record<string, unknown>;
    expect(body).toHaveProperty("model_id");
    expect(body).toHaveProperty("model_type");
    expect(body["model_type"]).toBe("timesfm");
    expect(body).toHaveProperty("huggingface_repo");
  });
});

// ── Suite: Dashboard /api/models shows timesfm_v1 ────────────────────────────

test.describe("APEX Dashboard — TimesFM registry visibility", () => {
  test("/api/models includes timesfm_v1 with status staging", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/models`);
    expect(res.status(), `GET ${BASE_URL}/api/models failed`).toBe(200);

    const body   = await res.json() as { models?: Record<string, unknown>[] };
    const models = body.models ?? [];

    const tfm = models.find(
      (m) => (m["model_id"] as string)?.startsWith("timesfm") ||
             (m["model_type"] as string) === "timesfm"
    );

    expect(
      tfm,
      "timesfm_v1 should appear in /api/models after running train_timesfm.py.\n" +
      `Current models: ${models.map((m) => m["model_id"]).join(", ")}`
    ).toBeDefined();

    const status = tfm?.["status"] as string;
    expect(
      ["staging", "live"].includes(status),
      `timesfm_v1 status should be 'staging' or 'live', got: ${status}`
    ).toBe(true);
  });
});

// ── Suite: Prometheus metrics ─────────────────────────────────────────────────

test.describe("TimesFM Service — Prometheus metrics", () => {
  test("GET /metrics returns 200 with text/plain content", async ({ request }) => {
    const res = await request.get(`${TIMESFM_URL}/metrics`);
    expect(res.status()).toBe(200);

    const ct = res.headers()["content-type"] ?? "";
    expect(
      ct.includes("text/plain"),
      `Expected text/plain content-type, got: ${ct}`
    ).toBe(true);
  });

  test("Metrics response contains timesfm_predictions_total counter", async ({ request }) => {
    // Make at least one prediction first so the counter is non-zero
    // (it may already exist from previous tests)
    await request.post(`${TIMESFM_URL}/predict`, { data: VALID_PREDICT_PAYLOAD })
      .catch(() => { /* ignore — we just want the counter to exist */ });

    const res  = await request.get(`${TIMESFM_URL}/metrics`);
    const text = await res.text();

    expect(
      text.includes("timesfm_predictions_total"),
      "Prometheus /metrics should expose timesfm_predictions_total counter"
    ).toBe(true);
  });

  test("Metrics response contains timesfm_model_loaded gauge", async ({ request }) => {
    const res  = await request.get(`${TIMESFM_URL}/metrics`);
    const text = await res.text();

    expect(
      text.includes("timesfm_model_loaded"),
      "Prometheus /metrics should expose timesfm_model_loaded gauge"
    ).toBe(true);
  });
});
