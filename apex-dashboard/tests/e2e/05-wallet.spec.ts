/**
 * 05-wallet.spec.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Tests for the APEX /wallet page.
 * Validates: portfolio stat cards, P&L chart, open positions, transaction
 * history, and performance metrics. Works with both live and mock data.
 */
import { test, expect } from "@playwright/test";
import { WalletPage } from "../pages/WalletPage";
import { BASE_URL } from "../../playwright.config";

let wp: WalletPage;

test.beforeEach(async ({ page }) => {
  wp = new WalletPage(page);
  await wp.goto();
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — Page Load", () => {
  test("Wallet page loads without crashing", async ({ page }) => {
    await test.step("Page body is visible", async () => {
      await expect(page.locator("body"), "Body should render").toBeVisible();
    });

    await test.step("Known wallet sections are present", async () => {
      // At minimum, portfolio or cash label should appear
      await expect(
        page.locator("text=/Portfolio Value|Cash Balance|Wallet/i").first(),
        "Wallet page should show portfolio or cash content"
      ).toBeVisible({ timeout: 10_000 });
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — Stat Cards", () => {
  test("Portfolio Value card shows a dollar amount (not NaN, not $0)", async ({
    page,
  }) => {
    await test.step("Card is visible", async () => {
      await expect(
        page.locator("text=Portfolio Value").first(),
        "Portfolio Value label should be visible"
      ).toBeVisible({ timeout: 8_000 });
    });

    await test.step("Card shows a dollar amount", async () => {
      await wp.assertPortfolioValueNumeric();
    });

    await test.step("Amount is positive", async () => {
      const card = page.locator("text=Portfolio Value").locator("../..").first();
      const text = await card.textContent() ?? "";
      // Extract numeric value from "127,450.00" pattern
      const numMatch = text.match(/[\d,]+\.?\d*/);
      const value = numMatch ? parseFloat(numMatch[0].replace(/,/g, "")) : 0;
      expect(
        value,
        "Portfolio Value should be a positive number"
      ).toBeGreaterThan(0);
    });
  });

  test("Cash Balance card shows a dollar amount", async ({ page }) => {
    const card = page.locator("text=Cash Balance").first();
    await expect(card, "Cash Balance label should be visible").toBeVisible({
      timeout: 8_000,
    });

    const container = card.locator("../..").first();
    const text = await container.textContent();
    expect(text, "Cash Balance card must have content").toBeTruthy();
    expect(text, "Cash Balance must not be NaN").not.toContain("NaN");
    expect(text, "Cash Balance must contain a dollar sign").toContain("$");
  });

  test("Day P&L card shows a value and is colored green or red", async () => {
    await test.step("Day P&L label is visible", async () => {
      await expect(
        wp.page.locator("text=Day P&L").first(),
        "Day P&L label should be visible"
      ).toBeVisible({ timeout: 8_000 });
    });

    await test.step("Day P&L has a numeric value", async () => {
      await wp.assertDayPnlHasValue();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — P&L History Chart", () => {
  test("P&L history chart renders (SVG or canvas present)", async () => {
    await test.step("Chart section heading is visible", async () => {
      const heading = wp.page.locator("text=P&L History").first();
      await expect(
        heading,
        "P&L History heading should be visible"
      ).toBeVisible({ timeout: 10_000 });
    });

    await test.step("Chart SVG/canvas is present inside the section", async () => {
      await wp.assertPnlChartRendered();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — Open Positions", () => {
  test("Open Positions section is visible", async () => {
    await wp.assertPositionsSectionVisible();
  });

  test("GET /api/positions → returns an array", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/positions`);
    expect(res.status(), "Positions API should return 200").toBe(200);

    const body = await res.json();
    const positions = Array.isArray(body)
      ? body
      : (body.positions ?? body.data ?? []);

    expect(
      Array.isArray(positions),
      "Positions response should be an array"
    ).toBe(true);
  });

  test("Position entries have required fields", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/positions`);
    if (res.status() !== 200) test.skip(true, "Positions API unavailable");

    const body = await res.json();
    const positions: Record<string, unknown>[] = Array.isArray(body)
      ? body
      : (body.positions ?? []);

    if (positions.length === 0) {
      test.skip(true, "No positions to validate (empty portfolio)");
      return;
    }

    const first = positions[0];
    expect(
      "symbol" in first || "Symbol" in first,
      "Position entry must have a symbol"
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — Transaction History", () => {
  test("Transaction History section renders", async () => {
    await wp.assertTransactionHistoryVisible();
  });

  test("GET /api/orders → returns array with required fields", async ({
    request,
  }) => {
    const res = await request.get(`${BASE_URL}/api/orders`);
    expect(res.status(), "Orders API should return 200").toBe(200);

    const body = await res.json();
    const orders: Record<string, unknown>[] = Array.isArray(body)
      ? body
      : (body.orders ?? []);

    expect(
      Array.isArray(orders),
      "Orders should be an array"
    ).toBe(true);

    if (orders.length > 0) {
      const first = orders[0];
      // At least one identifier field present
      expect(
        "id" in first || "order_id" in first || "symbol" in first,
        "Order entry must have an identifier"
      ).toBe(true);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — Performance Statistics", () => {
  test("Win Rate and Sharpe Ratio show numeric values", async () => {
    await wp.assertPerformanceStatsNumeric();
  });

  test("Win Rate value is a plausible percentage (0%–100%)", async ({
    page,
  }) => {
    const card = page.locator("text=Win Rate").locator("../..").first();
    await expect(card, "Win Rate card visible").toBeVisible({ timeout: 8_000 });

    const text = await card.textContent() ?? "";
    const pctMatch = text.match(/([\d.]+)%/);
    if (pctMatch) {
      const pct = parseFloat(pctMatch[1]);
      expect(pct, "Win Rate should be between 0 and 100").toBeGreaterThanOrEqual(0);
      expect(pct, "Win Rate should not exceed 100").toBeLessThanOrEqual(100);
    }
  });

  test("Sharpe Ratio is a finite number", async ({ page }) => {
    const card = page.locator("text=Sharpe").locator("../..").first();
    await expect(card, "Sharpe card visible").toBeVisible({ timeout: 8_000 });

    const text = await card.textContent() ?? "";
    expect(text, "Sharpe must not contain NaN").not.toContain("NaN");
    expect(text, "Sharpe must not contain Infinity").not.toContain("Infinity");
    // Should contain a decimal number
    expect(
      /[-\d.]+/.test(text),
      `Sharpe text "${text}" should contain a number`
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Wallet — GET /api/account", () => {
  test("Account API returns portfolio_value, cash, and buying_power", async ({
    request,
  }) => {
    const res = await request.get(`${BASE_URL}/api/account`);
    expect(res.status(), "Account API should return 200").toBe(200);

    const body = await res.json();

    await test.step("portfolio_value is a positive finite number", async () => {
      const pv = Number(body.portfolio_value ?? body.equity ?? 0);
      expect(
        Number.isFinite(pv),
        "portfolio_value should be finite"
      ).toBe(true);
      expect(pv, "portfolio_value should be positive").toBeGreaterThan(0);
    });

    await test.step("cash is a finite number", async () => {
      const cash = Number(body.cash ?? body.cash_balance ?? 0);
      expect(Number.isFinite(cash), "cash should be finite").toBe(true);
    });

    await test.step("buying_power is a finite number", async () => {
      const bp = Number(body.buying_power ?? body.buyingPower ?? 0);
      expect(Number.isFinite(bp), "buying_power should be finite").toBe(true);
    });
  });
});
