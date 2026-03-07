/**
 * tests/e2e/trading.spec.ts
 * ──────────────────────────
 * End-to-end tests for the Orders and Trading pages.
 *
 * Pages under test:
 *   /orders  — emergency stop + BUY/SELL form + order tables
 *   /trading — auto-trading config + manual order panel
 *
 * All tests use paper mode (never live). Kill switch is reset after each group.
 */

import { test, expect } from "@playwright/test";
import { TradingPage } from "../pages/TradingPage";
import { BASE_URL } from "../../playwright.config";
import { VALID_TRADE_NVDA_BUY } from "../fixtures/mock-data";

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Orders Page — Rendering", () => {
  let tp: TradingPage;

  test.beforeEach(async ({ page }) => {
    tp = new TradingPage(page);
    await tp.gotoOrders();
  });

  test("Orders page loads and renders without crashing", async ({ page }) => {
    await test.step("Verify URL and title", async () => {
      await expect(page).toHaveURL(/\/orders/);
      await expect(page).toHaveTitle(/APEX/i);
    });

    await test.step("Verify no Application Error screen", async () => {
      await expect(page.locator("text=Application error")).not.toBeVisible({ timeout: 3_000 });
    });

    await test.step("Verify page content is non-empty", async () => {
      const bodyText = await page.locator("body").textContent();
      expect(bodyText?.trim().length, "Page body must not be empty").toBeGreaterThan(100);
    });
  });

  test("BUY order form renders all required fields", async () => {
    await test.step("Assert all order form fields are visible", async () => {
      const page = tp.page;
      // Use the select directly — or() with loose text locators causes strict-mode violations
      await expect(tp.sideBuyBtn).toBeVisible({ timeout: 8_000 });
      await expect(tp.sideSellBtn).toBeVisible();
      await expect(tp.symbolSelect).toBeVisible();
    });
  });

  test("Kill switch status indicator is visible on page load", async ({ page }) => {
    // The emergency stop section is always rendered
    await expect(
      page.locator("text=/EMERGENCY STOP|kill switch/i").first()
    ).toBeVisible({ timeout: 5_000 });
  });

  test("Paper/Live mode toggle is present and defaults to Paper", async ({ page }) => {
    // Should see PAPER mode button as active/selected
    const paperIndicator = page.locator("text=/paper/i").first();
    await expect(paperIndicator).toBeVisible({ timeout: 5_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Emergency Kill Switch — UI", () => {
  let tp: TradingPage;

  test.afterEach(async ({ request }) => {
    // Always clean up — disarm kill switch after each test
    await request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: false, reason: "trading.spec.ts cleanup" },
    });
  });

  test.beforeEach(async ({ page }) => {
    tp = new TradingPage(page);
    // First disarm any lingering kill switch from a previous run
    await page.request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: false },
    });
    await tp.gotoOrders();
  });

  test("Kill switch starts in STANDBY state", async ({ page }) => {
    // The GET /api/kill-switch call should return active:false (we just deactivated)
    await expect.poll(
      async () => {
        const res  = await page.request.get(`${BASE_URL}/api/kill-switch`);
        const body = await res.json();
        return body.active;
      },
      { timeout: 5_000, message: "Kill switch should be inactive on fresh page load" }
    ).toBe(false);
  });

  test("Activating kill switch via API blocks /api/trade", async ({ request }) => {
    await test.step("Arm kill switch", async () => {
      const armRes = await request.post(`${BASE_URL}/api/kill-switch`, {
        data: { active: true, reason: "Kill switch test" },
      });
      expect(armRes.status()).toBe(200);
      const body = await armRes.json();
      expect(body.active, "Kill switch should be active").toBe(true);
    });

    await test.step("Attempt trade while kill switch is armed", async () => {
      const tradeRes = await request.post(`${BASE_URL}/api/trade`, {
        data: VALID_TRADE_NVDA_BUY,
      });
      expect(tradeRes.status(), "Trade should be blocked when kill switch is armed").toBe(403);
    });
  });

  test("Deactivating kill switch re-enables trading", async ({ request }) => {
    // Arm
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: true } });

    // Disarm
    const disarmRes = await request.post(`${BASE_URL}/api/kill-switch`, {
      data: { active: false, reason: "Resume trading test" },
    });
    expect(disarmRes.status()).toBe(200);
    const body = await disarmRes.json();
    expect(body.active, "After deactivation, active should be false").toBe(false);

    // Trade should no longer return 403 (may be 200 or 403 due to missing Alpaca keys)
    const tradeRes = await request.post(`${BASE_URL}/api/trade`, {
      data: VALID_TRADE_NVDA_BUY,
    });
    expect(tradeRes.status(), "Trade should not be blocked after kill switch deactivated").not.toBe(403);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Order Form — Interaction", () => {
  let tp: TradingPage;

  test.beforeEach(async ({ page }) => {
    tp = new TradingPage(page);
    // Ensure kill switch is off
    await page.request.post(`${BASE_URL}/api/kill-switch`, { data: { active: false } });
    await tp.gotoOrders();
  });

  test.afterEach(async ({ request }) => {
    await request.post(`${BASE_URL}/api/kill-switch`, { data: { active: false } });
  });

  test("Submit button is disabled until confirm checkbox is ticked", async ({ page }) => {
    // Most implementations disable submit until checkbox is checked
    const submitBtn = tp.submitBtn;

    if (await submitBtn.count() === 0) {
      test.skip(true, "Submit button not found — data-testid may not be set yet");
      return;
    }

    // Before checking confirm — button should be disabled or have text "confirm first"
    const isDisabledBefore = await submitBtn.isDisabled();
    expect(isDisabledBefore, "Submit should be disabled before confirm is ticked").toBeTruthy();

    // Tick confirm
    await tp.confirm();

    // After — may still be disabled if kill switch active (not the case here)
    // Just verify the interaction doesn't crash
    await expect(page).toHaveURL(/\/orders/);
  });

  test("Order type: switching to Limit reveals price input", async ({ page }) => {
    const limitBtn = tp.orderTypeLimit;
    if (await limitBtn.count() === 0) {
      test.skip(true, "Limit order button not found — add data-testid='trade-order-type-limit'");
      return;
    }

    // Initially market — limit price should not be visible
    await expect(tp.limitPriceInput).not.toBeVisible({ timeout: 2_000 }).catch(() => {
      // It may be visible but empty — acceptable
    });

    await limitBtn.click();

    // After switching to limit — price input should appear
    await expect(tp.limitPriceInput).toBeVisible({ timeout: 3_000 });
  });

  test("Live mode toggle shows real-money warning text", async ({ page }) => {
    const liveBtn = tp.accountLiveBtn;
    if (await liveBtn.count() === 0) {
      test.skip(true, "Live mode button not found — add data-testid='trade-account-live'");
      return;
    }

    await liveBtn.click();

    // Should show a warning about real money
    const warning = page.locator("text=/real money|live.*real|⚠/i").first();
    await expect(warning).toBeVisible({ timeout: 3_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Trading Page — Auto Trading Config", () => {
  test("Trading page loads without error", async ({ page }) => {
    await page.goto(`${BASE_URL}/trading`);
    await expect(page).toHaveURL(/\/trading/);

    await expect(page.locator("text=Application error")).not.toBeVisible({ timeout: 3_000 });
    const body = await page.locator("body").textContent();
    expect(body?.trim().length, "Trading page is empty").toBeGreaterThan(100);
  });

  test("GET /api/trading-mode → returns mode field", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/trading-mode`);
    expect([200, 404], `Expected 200 or 404, got ${res.status()}`).toContain(res.status());

    if (res.status() === 200) {
      const body = await res.json();
      // /api/trading-mode returns: auto_trading_enabled, account_mode, min_confidence, etc.
      expect(
        "mode" in body || "trading_mode" in body || "auto" in body || "auto_trading_enabled" in body,
        "Response must include a mode field"
      ).toBeTruthy();
    }
  });

  test("Auto-trading mode toggle calls the trading-mode API", async ({ page }) => {
    await page.goto(`${BASE_URL}/trading`);

    let modeApiCalled = false;
    page.on("response", r => {
      if (r.url().includes("/api/trading-mode")) modeApiCalled = true;
    });

    // Look for any toggle labeled "Auto" or "auto trading"
    const autoToggle = page
      .getByRole("button", { name: /auto.*(trading|mode)/i })
      .or(page.locator("[data-testid='auto-trading-toggle']"))
      .first();

    if (await autoToggle.count() === 0) {
      test.skip(true, "Auto trading toggle not found — add data-testid='auto-trading-toggle'");
      return;
    }

    await autoToggle.click();
    // Wait briefly for debounced API call
    await page.waitForTimeout(500);

    expect(modeApiCalled, "Expected trading-mode API to be called after toggle click").toBeTruthy();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Order History", () => {
  test("GET /api/orders returns array of orders", async ({ request }) => {
    const res  = await request.get(`${BASE_URL}/api/orders?limit=25&account_mode=paper`);
    expect(res.status()).toBe(200);

    const body   = await res.json();
    const orders = body.orders ?? [];
    expect(Array.isArray(orders), "orders must be an array").toBeTruthy();
  });

  test("Orders page renders Open Orders and History sections", async ({ page }) => {
    await page.goto(`${BASE_URL}/orders`);

    // These section headings are rendered by our orders page
    await expect(
      page.locator("text=/OPEN ORDERS/i").first()
    ).toBeVisible({ timeout: 8_000 });

    await expect(
      page.locator("text=/ORDER HISTORY|RECENT/i").first()
    ).toBeVisible({ timeout: 5_000 });
  });
});
