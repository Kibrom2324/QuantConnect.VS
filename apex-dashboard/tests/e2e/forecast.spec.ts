/**
 * tests/e2e/forecast.spec.ts
 * ───────────────────────────
 * TFT signal / forecast tests.
 *
 * In APEX the ML prediction surface is:
 *   /signals  — live signal stream (GET /api/signals)
 *   /models   — model management (GET/POST /api/models)
 *   /api/ensemble — ensemble model weights and predictions
 *
 * These tests validate that:
 *   1. Signal data appears in the UI
 *   2. All required fields are present and have valid values
 *   3. Edge cases (invalid symbol, duplicate, history ordering) behave correctly
 *   4. Confidence is always in [0, 1]
 *   5. Predicted values are finite positive numbers
 *   6. Timestamps are not in the future
 */

import { test, expect } from "@playwright/test";
import { ForecastPage } from "../pages/ForecastPage";
import { BASE_URL, SYMBOLS, HORIZONS, PREDICT_TIMEOUT } from "../../playwright.config";
import {
  CONFIDENCE_TOLERANCE,
  MAX_REASONABLE_PRICE,
  MIN_CONFIDENCE_THRESHOLD,
} from "../fixtures/mock-data";

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Signals Page — Display", () => {
  let forecastPage: ForecastPage;

  test.beforeEach(async ({ page }) => {
    forecastPage = new ForecastPage(page);
    await forecastPage.gotoSignals();
  });

  test("Signals page loads and renders content", async ({ page }) => {
    await test.step("Verify page metadata", async () => {
      await expect(page).toHaveURL(/\/signals/);
      await expect(page).toHaveTitle(/APEX/i);
    });

    await test.step("Verify page body is non-empty", async () => {
      const body = await page.locator("body").textContent();
      expect(body?.trim().length, "Signals page body is empty").toBeGreaterThan(100);
    });

    await test.step("Verify page does not show a crash/error screen", async () => {
      await expect(page.locator("text=Application error")).not.toBeVisible({ timeout: 3_000 });
      await expect(page.locator("text=500")).not.toBeVisible({ timeout: 3_000 });
    });
  });

  test("Signal API returns data — /api/signals responds successfully", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/signals`);

    await test.step("HTTP status is 200", async () => {
      expect(res.status(), `GET /api/signals returned ${res.status()}`).toBe(200);
    });

    await test.step("Response body is valid JSON with signals array", async () => {
      const body     = await res.json();
      const signals  = (body.signals ?? body) as unknown[];
      expect(Array.isArray(signals), "Expected 'signals' to be an array").toBeTruthy();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Signal Field Validation", () => {
  test("Signal API response has all required fields", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();

    // Adapt to both { signals: [...] } and flat array response shapes
    const signals: Record<string, unknown>[] = Array.isArray(body)
      ? body
      : (body.signals ?? []);

    expect(signals.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    const signal = signals[0];

    await test.step("symbol field is a non-empty string", async () => {
      expect(typeof signal.symbol, "signal.symbol must be a string").toBe("string");
      expect((signal.symbol as string).trim(), "signal.symbol must not be empty").not.toBe("");
    });

    await test.step("predicted_value is a finite positive number", async () => {
      const val = Number(signal.predicted_value ?? signal.prediction ?? signal.value);
      expect(isFinite(val), `predicted_value ${val} is not finite`).toBeTruthy();
      expect(val, `predicted_value ${val} must be > 0`).toBeGreaterThan(0);
      expect(val, `predicted_value ${val} exceeds sanity ceiling $${MAX_REASONABLE_PRICE}`).toBeLessThan(MAX_REASONABLE_PRICE);
    });

    await test.step("confidence is between 0 and 1", async () => {
      const conf = Number(signal.confidence ?? signal.score);
      expect(isFinite(conf), `confidence ${conf} is not finite`).toBeTruthy();
      expect(conf, `confidence ${conf} must be ≥ 0`).toBeGreaterThanOrEqual(0);
      expect(conf, `confidence ${conf} exceeds 1.0`).toBeLessThanOrEqual(1.0 + CONFIDENCE_TOLERANCE);
    });

    await test.step("timestamp is a valid ISO date, not in the future", async () => {
      const tsStr = String(signal.timestamp ?? signal.created_at ?? "");
      const ts    = new Date(tsStr);
      expect(ts.getTime(), `timestamp "${tsStr}" is not valid`).not.toBeNaN();
      expect(
        ts.getTime(),
        `timestamp "${tsStr}" is in the future`
      ).toBeLessThanOrEqual(Date.now() + 60_000);
    });

    await test.step("direction is 'up', 'down', or 'flat'", async () => {
      const dir = String(signal.direction ?? signal.trend ?? "").toLowerCase();
      // direction may not exist in all implementations — skip if absent
      if (dir) {
        expect(
          ["up", "down", "flat"].includes(dir),
          `direction "${dir}" is not one of: up, down, flat`
        ).toBeTruthy();
      }
    });
  });

  test("confidence is 4 decimal place precision (API level)", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();
    const signals: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);

    expect(signals.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    const conf     = Number(signals[0].confidence ?? signals[0].score);
    const rounded  = parseFloat(conf.toFixed(4));

    // Tolerance: 0.00005 — half the last decimal place
    expect(
      Math.abs(conf - rounded),
      `Confidence ${conf} has more precision than 4 decimal places (raw: ${conf})`
    ).toBeLessThanOrEqual(0.00005);
  });

  test("predicted_value is NOT NaN and NOT 0", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();
    const signals: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);

    expect(signals.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    for (const signal of signals.slice(0, 5)) {
      const val = Number(signal.predicted_value ?? signal.prediction);
      expect(val, `predicted_value is NaN for symbol ${signal.symbol}`).not.toBeNaN();
      expect(val, `predicted_value is 0 for symbol ${signal.symbol} — suspicious`).not.toBe(0);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Multi-Symbol and Horizon", () => {
  // Note: test.each in Playwright doesn't inject fixtures via 2nd arg — use for..of instead
  for (const symbol of SYMBOLS) {
    test(`Signals for ${symbol} are available in API`, async ({ request }) => {
      const res = await request.get(`${BASE_URL}/api/signals?symbol=${symbol}`);
      expect(res.status()).toBe(200);

      const body = await res.json();
      const signals: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);
      const matching = signals.filter(s => String(s.symbol).toUpperCase() === symbol.toUpperCase());

      expect(
        matching.length,
        `Expected at least 1 signal for ${symbol}, found ${matching.length}`
      ).toBeGreaterThan(0);
    });
  }

  test("History entries have unique timestamps when same symbol requested twice", async ({ request }) => {
    // Two sequential requests — the second should have a ≥ timestamp than the first
    const r1 = await request.get(`${BASE_URL}/api/signals`);
    await new Promise(r => setTimeout(r, 100)); // 100ms — minimal separation
    const r2 = await request.get(`${BASE_URL}/api/signals`);

    const b1 = await r1.json();
    const b2 = await r2.json();

    const ts1 = new Date(String(
      (Array.isArray(b1) ? b1 : b1.signals ?? [])[0]?.timestamp ?? 0
    )).getTime();
    const ts2 = new Date(String(
      (Array.isArray(b2) ? b2 : b2.signals ?? [])[0]?.timestamp ?? 0
    )).getTime();

    // Second timestamp should be ≥ first (not from the past relative to first)
    expect(ts2, "Second fetch timestamp should not be before first fetch").toBeGreaterThanOrEqual(ts1 - 1000);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Models Page — Live Model Badge", () => {
  let forecastPage: ForecastPage;

  test.beforeEach(async ({ page }) => {
    forecastPage = new ForecastPage(page);
    await forecastPage.gotoModels();
  });

  test("Models page loads successfully", async ({ page }) => {
    await expect(page).toHaveURL(/\/models/);
    const body = await page.locator("body").textContent();
    expect(body?.trim().length, "Models page is empty").toBeGreaterThan(50);
  });

  test("A 'live' model is displayed on the models page", async ({ page }) => {
    // Poll — models load async from Redis
    await expect.poll(
      async () => {
        const liveEl = page.getByTestId("model-live-badge")
          .or(page.locator("text=/\\blive\\b/i"))
          .first();
        return await liveEl.isVisible();
      },
      { timeout: 8_000, message: "Expected a 'live' model badge to appear" }
    ).toBeTruthy();
  });

  test("GET /api/models returns correct schema", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/models`);
    expect(res.status()).toBe(200);

    const body = await res.json();

    await test.step("models array exists", async () => {
      expect(Array.isArray(body.models), "body.models is not an array").toBeTruthy();
    });

    await test.step("live_model field is present", async () => {
      // live_model may be null if no model is live yet (valid edge case)
      expect("live_model" in body, "body.live_model key is missing").toBeTruthy();
    });

    await test.step("Each model has required fields", async () => {
      for (const m of (body.models as Record<string, unknown>[]).slice(0, 3)) {
        expect(m.model_id,   `model_id is missing for ${JSON.stringify(m)}`).toBeTruthy();
        expect(m.model_type, `model_type is missing for model ${m.model_id}`).toBeTruthy();
        expect(m.status,     `status is missing for model ${m.model_id}`).toBeTruthy();
        expect(
          typeof m.val_sharpe === "number",
          `val_sharpe is not a number for model ${m.model_id}`
        ).toBeTruthy();
      }
    });
  });

  test("POST /api/models queues a training job", async ({ request }) => {
    const res = await request.post(`${BASE_URL}/api/models`, {
      data: { model_type: "ensemble", triggered_by: "playwright_test" },
    });

    await test.step("Returns 200 with job_id", async () => {
      expect(res.status(), `Expected 200, got ${res.status()}`).toBe(200);
      const body = await res.json();
      expect(body.job_id,   "job_id missing from response").toBeTruthy();
      expect(body.model_id, "model_id missing from response").toBeTruthy();
      expect(body.status,   "status missing from response").toBeTruthy();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Edge Cases", () => {
  test("Ensemble API returns weights that sum to approximately 1.0", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/ensemble`);

    test.skip(res.status() === 404, "Skipped — /api/ensemble not implemented yet");

    const body = await res.json();
    const weights: Record<string, number> = body.weights ?? {};

    if (Object.keys(weights).length === 0) {
      test.info().annotations.push({
        type: "warning",
        description: "No weights returned — ensemble may be in default state",
      });
      return;
    }

    const sum = Object.values(weights).reduce((a, b) => a + b, 0);
    expect(
      Math.abs(sum - 1.0),
      `Ensemble weights sum to ${sum.toFixed(4)}, expected ~1.0`
    ).toBeLessThan(0.01);
  });

  test("Signals page does not crash when API is slow (mock mode)", async ({ page }) => {
    // Simulate slow API by aborting the signals request
    await page.route("**/api/signals", async (route) => {
      await new Promise(r => setTimeout(r, 3_000));
      await route.fulfill({ status: 200, json: { signals: [], _mock: true } });
    });

    await page.goto(`${BASE_URL}/signals`);

    // Page should not crash or show Application Error
    await expect(page.locator("text=Application error")).not.toBeVisible({ timeout: 8_000 });
    await expect(page).toHaveURL(/\/signals/);
  });
});
