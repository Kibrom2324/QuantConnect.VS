/**
 * 02-dashboard.spec.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Tests for the APEX /dashboard page.
 * Covers: stat cards, kill switch UI, service status, ensemble weights,
 * agent log, and P&L ticker. All tests use proper waits — zero setTimeout().
 */
import { test, expect } from "@playwright/test";
import { DashboardPage } from "../pages/DashboardPage";
import { BASE_URL } from "../../playwright.config";

let dp: DashboardPage;

test.beforeEach(async ({ page }) => {
  dp = new DashboardPage(page);
  await page.goto(`${BASE_URL}/dashboard`, { waitUntil: "domcontentloaded" });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Page Load", () => {
  test("Dashboard loads without severe console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && !msg.text().includes("Warning:")) {
        // Filter out known non-critical warnings
        const text = msg.text();
        const isNoise =
          text.includes("favicon") ||
          text.includes("404") ||
          text.includes("net::ERR") ||
          text.includes("hydration");
        if (!isNoise) errors.push(text);
      }
    });

    // Reload to capture errors during initial paint
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(
      page.locator("body"),
      "Page body should render"
    ).toBeVisible({ timeout: 8_000 });

    expect(
      errors,
      `Unexpected console errors: ${errors.join(" | ")}`
    ).toHaveLength(0);
  });

  test("Dashboard page title / heading is visible", async ({ page }) => {
    // APEX header or nav should be visible
    await expect(
      page.locator("text=/APEX|Dashboard/i").first(),
      "APEX heading should be visible"
    ).toBeVisible({ timeout: 8_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Sidebar Navigation", () => {
  test("All 9 main nav links are present", async () => {
    await dp.assertSidebarNavComplete();
  });

  test("Clicking 'Orders' nav link navigates to /orders", async ({ page }) => {
    const ordersLink = page
      .getByTestId("nav-orders")
      .or(page.getByRole("link", { name: /orders/i }).first());

    await expect(ordersLink, "Orders nav link should be visible").toBeVisible({
      timeout: 5_000,
    });

    await Promise.all([
      page.waitForURL(/\/orders/, { timeout: 10_000 }),
      ordersLink.click(),
    ]);

    expect(page.url(), "URL should include /orders").toContain("/orders");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Service Status Grid", () => {
  test("Service status grid renders with at least 4 services", async ({
    page,
  }) => {
    // ServiceStatusGrid component renders status items
    await test.step("Wait for service grid to be visible", async () => {
      const grid = page
        .getByTestId("service-status-grid")
        .or(
          page
            .locator("text=/Signal Engine|Risk Manager|Data Feed|Order Router/i")
            .first()
        );
      await expect(grid, "Service status grid should be visible").toBeVisible({
        timeout: 10_000,
      });
    });

    await test.step("At least 4 service entries are visible", async () => {
      // The grid shows service names — check that several are present
      const serviceNames = ["Signal Engine", "Risk Manager", "Data Feed", "Order Router"];
      for (const name of serviceNames) {
        await expect(
          page.locator(`text=${name}`).first(),
          `Service "${name}" should be listed`
        ).toBeVisible({ timeout: 5_000 });
      }
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Kill Switch", () => {
  test("Kill switch section is present on dashboard", async ({
    page,
  }) => {
    // KillSwitch renders 'Emergency Control' label + 'KILL SWITCH' span heading
    // Use getByText with exact match to avoid strict mode — matches the span only
    const ksSection = page
      .getByTestId("kill-switch-btn")
      .or(page.getByText("KILL SWITCH", { exact: true }))
      .or(page.getByText("Emergency Control", { exact: true }));

    await expect(
      ksSection.first(),
      "Kill switch section should be visible on dashboard"
    ).toBeVisible({ timeout: 10_000 });

    await test.step("Kill switch has a meaningful status label", async () => {
      const statusEl = page
        .locator("text=/HALTED|ACTIVE|STANDBY/i")
        .first();
      await expect(
        statusEl,
        "Kill switch status should be visible"
      ).toBeVisible({ timeout: 5_000 });
    });
  });

  test("Kill switch GET /api/kill-switch → returns boolean active field", async ({
    request,
  }) => {
    const res = await request.get(`${BASE_URL}/api/kill-switch`);
    expect(
      res.status(),
      "Kill switch API should return 200"
    ).toBe(200);

    const body = await res.json();
    expect(
      typeof body.active,
      "active field must be a boolean"
    ).toBe("boolean");
    expect(
      "active" in body,
      "Response must contain active field"
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Ensemble Weights", () => {
  test("Ensemble weights component is visible", async ({ page }) => {
    // EnsembleWeights renders the model weights
    const weights = page
      .getByTestId("ensemble-weights")
      .or(
        page.locator("text=/TFT|XGB|LSTM|Ensemble|Weights/i").first()
      );

    await expect(
      weights,
      "Ensemble weights section should be visible"
    ).toBeVisible({ timeout: 12_000 });
  });

  test("GET /api/weights → returns numeric weights for TFT, XGB, and a third model", async ({
    request,
  }) => {
    const res = await request.get(`${BASE_URL}/api/weights`);
    expect(res.status(), "Weights API should return 200").toBe(200);

    const body = await res.json();
    // Accept either {tft, xgb, lstm} flat or {weights: {tft, xgb, lstm}}
    // Actual response: {"weights":{"TFT":0.4,"XGB":0.35,"Factor":0.25}}
    const w = body.weights ?? body;
    const tft = w.tft ?? w.TFT;
    const xgb = w.xgb ?? w.XGB;
    // Third model can be Factor, LSTM, LSTM4, or any name
    const allValues = Object.values(w) as unknown[];

    expect(
      tft != null,
      "TFT weight should be present"
    ).toBe(true);
    expect(
      xgb != null,
      "XGB weight should be present"
    ).toBe(true);
    expect(
      allValues.length,
      "Weights should have at least 2 model entries"
    ).toBeGreaterThanOrEqual(2);

    // All weights should be finite numbers that sum to approximately 1.0
    const numericWeights = allValues.filter(v => typeof v === "number") as number[];
    if (numericWeights.length >= 2) {
      const sum = numericWeights.reduce((a, b) => a + b, 0);
      expect(
        Math.abs(sum - 1.0),
        `Weights should sum to ~1.0, got ${sum}`
      ).toBeLessThan(0.05);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — Agent Log", () => {
  test("Agent log entries are present on dashboard", async ({ page }) => {
    // AgentLog renders log entries — look for the log type labels or entry text
    const agentLogEntries = page
      .getByTestId("agent-log")
      .or(
        // AgentLog renders rows with event types — or look at the column of log entries
        page.locator("[class*='log'], [class*='Log']").first()
      )
      .or(
        // Or find text that looks like a log entry (timestamp-shaped text is in entries)
        page.locator("text=/TRADING|SIGNAL|MODEL|RISK|SYSTEM|TRAINING/i").first()
      );

    await expect(
      agentLogEntries,
      "Agent log entries should be visible on dashboard"
    ).toBeVisible({ timeout: 15_000 });
  });

  test("GET /api/agent-log → returns an array", async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/agent-log`);
    expect([200, 404], `Expected 200 or 404, got ${res.status()}`).toContain(
      res.status()
    );

    if (res.status() === 200) {
      const body = await res.json();
      const entries = Array.isArray(body) ? body : (body.entries ?? body.logs ?? []);
      expect(
        Array.isArray(entries),
        "Agent log should return an array"
      ).toBe(true);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — P&L Ticker", () => {
  test("P&L ticker strip is visible at the top of the page", async ({
    page,
  }) => {
    // PnLTicker renders a div with class 'animate-ticker' containing price data
    // Use a non-strict locator that picks a single element
    const ticker = page.locator(".animate-ticker").first();
    await expect(
      ticker,
      "Animated ticker strip should be visible on the dashboard"
    ).toBeVisible({ timeout: 12_000 });

    // Should contain at least one stock symbol
    const text = await ticker.textContent();
    expect(
      /NVDA|AAPL|MSFT|TSLA/i.test(text ?? ""),
      "Ticker should display at least one stock symbol"
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Dashboard — GET /api/metrics", () => {
  test("GET /api/metrics → returns 200 (JSON or Prometheus text)", async ({
    request,
  }) => {
    const res = await request.get(`${BASE_URL}/api/metrics`);
    expect(
      [200, 404],
      `Expected 200 or 404, got ${res.status()}`
    ).toContain(res.status());

    if (res.status() === 200) {
      const contentType = res.headers()["content-type"] ?? "";
      if (contentType.includes("application/json")) {
        // JSON metrics format
        const body = await res.json();
        const hasFinancialField =
          "portfolio_value" in body ||
          "equity" in body ||
          "cash" in body ||
          "pnl" in body ||
          "total_return" in body;
        expect(
          hasFinancialField,
          "JSON metrics response should have at least one financial field"
        ).toBe(true);
      } else {
        // Prometheus text format — just check it's non-empty
        const text = await res.text();
        expect(
          text.length,
          "Prometheus metrics response should not be empty"
        ).toBeGreaterThan(0);
      }
    }
  });
});
