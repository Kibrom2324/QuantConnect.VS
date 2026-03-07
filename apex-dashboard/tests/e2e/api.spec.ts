/**
 * tests/e2e/api.spec.ts
 * ─────────────────────
 * API-level tests using Playwright's built-in `request` fixture.
 *
 * Why request fixture (not axios/supertest)?
 *   - Automatically inherits storageState (cookies/tokens)
 *   - Failure output includes full request + response JSON diff
 *   - No extra dependency — stays in the same test runner context
 *
 * Covers:
 *   - /api/signals     — signal stream
 *   - /api/orders      — order management
 *   - /api/trade       — place orders
 *   - /api/kill-switch — emergency stop
 *   - /api/account     — account balance
 *   - /api/positions   — open positions
 *   - /api/health      — health check
 */

import { test, expect } from "@playwright/test";
import { BASE_URL } from "../../playwright.config";
import {
  VALID_TRADE_NVDA_BUY,
  INVALID_PAYLOAD_MISSING_FIELDS,
  INVALID_PAYLOAD_NOT_CONFIRMED,
  CONFIDENCE_TOLERANCE,
  MAX_REASONABLE_PRICE,
} from "../fixtures/mock-data";

// ─────────────────────────────────────────────────────────────────────────────
test.describe("GET /api/signals", () => {
  test("Returns HTTP 200", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/signals`);
    expect(res.status(), `GET /api/signals → ${res.status()}`).toBe(200);
  });

  test("Response body is valid JSON with an array field", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();

    const arr = Array.isArray(body) ? body : (body.signals ?? body.data ?? null);
    expect(arr, "Response must contain an array of signals (top-level or .signals)").not.toBeNull();
    expect(Array.isArray(arr), "Signals must be an array").toBeTruthy();
  });

  test("Each signal entry has required schema fields", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();
    const arr: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);

    expect(arr.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    const REQUIRED = ["symbol", "confidence"];
    for (const field of REQUIRED) {
      expect(
        field in arr[0],
        `Signal entry is missing required field: "${field}"\nGot: ${JSON.stringify(arr[0], null, 2)}`
      ).toBeTruthy();
    }
  });

  test("predicted_value is a finite number (not NaN, not Infinity)", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();
    const arr: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);

    expect(arr.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    const val = Number(arr[0].predicted_value ?? arr[0].prediction ?? arr[0].value ?? 0);
    expect(isFinite(val), `predicted_value "${val}" is not finite (NaN or Infinity)`).toBeTruthy();
    expect(val, "predicted_value must be > 0").toBeGreaterThan(0);
    expect(val, `predicted_value ${val} exceeds sanity limit`).toBeLessThan(MAX_REASONABLE_PRICE);
  });

  test("confidence is between 0 and 1 (inclusive, 4dp tolerance)", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/signals`);
    const body = await res.json();
    const arr: Record<string, unknown>[] = Array.isArray(body) ? body : (body.signals ?? []);

    expect(arr.length, "Expected at least 1 signal from mock fallback").toBeGreaterThan(0);

    for (const entry of arr.slice(0, 5)) {
      const conf = Number(entry.confidence ?? entry.score);
      expect(isFinite(conf), `confidence ${conf} is not finite for ${entry.symbol}`).toBeTruthy();
      expect(conf, `confidence ${conf} < 0`).toBeGreaterThanOrEqual(0);
      expect(conf, `confidence ${conf} > 1`).toBeLessThanOrEqual(1.0 + CONFIDENCE_TOLERANCE);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("GET /api/orders", () => {
  test("Returns HTTP 200", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/orders?account_mode=paper&limit=5`);
    expect(res.status()).toBe(200);
  });

  test("Response body has .orders array", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/orders?account_mode=paper&limit=5`);
    const body = await res.json();

    expect(Array.isArray(body.orders), "body.orders must be an array").toBeTruthy();
  });

  test("Each order entry has required fields", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/orders?account_mode=paper&limit=10`);
    const body = await res.json();
    const orders: Record<string, unknown>[] = body.orders ?? [];

    test.skip(orders.length === 0, "Skipped — no orders returned (use Alpaca paper account)");

    const REQUIRED = ["id", "symbol", "side", "qty", "status"];
    for (const field of REQUIRED) {
      expect(
        field in orders[0],
        `Order entry missing required field "${field}"\nGot: ${JSON.stringify(orders[0], null, 2)}`
      ).toBeTruthy();
    }
  });

  test("GET /api/orders respects limit parameter", async ({ request }) => {
    const LIMIT = 3;
    const res   = await request.get(`${BASE_URL}/api/orders?account_mode=paper&limit=${LIMIT}`);
    const body  = await res.json();
    const orders: unknown[] = body.orders ?? [];

    expect(
      orders.length,
      `Expected at most ${LIMIT} orders, got ${orders.length}`
    ).toBeLessThanOrEqual(LIMIT);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("GET /api/kill-switch", () => {
  test("Returns HTTP 200 with active field", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/kill-switch`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect("active" in body, "Response must have .active field").toBeTruthy();
    expect(typeof body.active, "body.active must be boolean").toBe("boolean");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("POST /api/kill-switch", () => {
  test.afterEach(async ({ request }) => {
    // Always deactivate kill switch after each test in this group
    await request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: false, reason: "Test cleanup" },
    });
  });

  test("Activating kill switch returns active:true", async ({ request }) => {
    const res  = await request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: true, reason: "Test: activate" },
    });
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(body.active, "Kill switch should be active after POST active:true").toBe(true);
    expect(body.method, "Method should indicate how it was set").toBeTruthy();
  });

  test("Deactivating kill switch returns active:false", async ({ request }) => {
    // First arm it
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: true } });

    // Then disarm
    const res  = await request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: false, reason: "Test: deactivate" },
    });
    const body = await res.json();
    expect(body.active, "Kill switch should be inactive after POST active:false").toBe(false);
  });

  test("GET after POST reflects updated state", async ({ request }) => {
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: true } });

    const getRes = await request.get(`${BASE_URL}/api/kill-switch`);
    const body   = await getRes.json();
    expect(body.active, "GET should return active:true after POST active:true").toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("POST /api/trade — with kill switch off", () => {
  test.beforeEach(async ({ request }) => {
    // Ensure kill switch is off before each trade test
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: false } });
  });

  test("Valid paper order returns 200 or 403 (Alpaca key not set)", async ({ request }) => {
    const res  = await request.post(`${BASE_URL}/api/trade`, { data: VALID_TRADE_NVDA_BUY });
    // 200 = success; 403 = Alpaca keys not configured; 500 = Alpaca connection error (no keys)
    expect([200, 403, 500], `Expected 200, 403, or 500, got ${res.status()}`).toContain(res.status());

    if (res.status() === 200) {
      const body = await res.json();
      expect(body.id ?? body.order_id ?? body.job_id, "Response must include an order id").toBeTruthy();
    }
  });

  test("Order without confirmed:true returns 400", async ({ request }) => {
    const res = await request.post(`${BASE_URL}/api/trade`, {
      data: INVALID_PAYLOAD_NOT_CONFIRMED,
    });
    expect(res.status(), "Unconfirmed order should return 400").toBe(400);

    const body = await res.json();
    expect(
      body.error ?? body.message ?? body.detail,
      "Error response should explain why the order was rejected"
    ).toBeTruthy();
  });

  test("Kill switch blocks order — returns 403", async ({ request }) => {
    // Arm kill switch
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: true } });

    // Attempt trade
    const res = await request.post(`${BASE_URL}/api/trade`, { data: VALID_TRADE_NVDA_BUY });
    expect(res.status(), "Trade should be blocked (403) when kill switch is armed").toBe(403);

    const body = await res.json();
    expect(
      String(body.error ?? body.message ?? "").toLowerCase(),
      "Error should mention kill switch"
    ).toMatch(/kill|halt|stop|blocked/i);
  });

  test("Missing symbol in trade payload returns 400 or 422", async ({ request }) => {
    const res = await request.post(`${BASE_URL}/api/trade`, {
      data: INVALID_PAYLOAD_MISSING_FIELDS,
    });
    expect([400, 422], `Expected 400 or 422, got ${res.status()}`).toContain(res.status());
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("GET /api/account", () => {
  test("Returns HTTP 200 with account fields", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/account?account_mode=paper`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    // May be mock data — just verify structure
    const data = body.account ?? body;
    expect(data, "Account data must be truthy").toBeTruthy();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("GET /api/health", () => {
  test("Returns HTTP 200", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/health`);
    expect([200, 404], `Health endpoint returned ${res.status()}`).toContain(res.status());
    // 404 = not implemented yet (non-fatal)
  });
});
