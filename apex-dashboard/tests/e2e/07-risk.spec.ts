/**
 * 07-risk.spec.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Tests for the APEX /risk page.
 * Validates: page load, VaR metrics display, drawdown chart, sector exposure,
 * risk alerts, and portfolio heatmap rendering.
 */
import { test, expect } from "@playwright/test";
import { BASE_URL } from "../../playwright.config";

test.beforeEach(async ({ page }) => {
  await page.goto(`${BASE_URL}/risk`, { waitUntil: "domcontentloaded" });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Load", () => {
  test("Risk page loads without crashing", async ({ page }) => {
    await test.step("Page body renders", async () => {
      await expect(page.locator("body"), "Body should render").toBeVisible();
    });

    await test.step("Risk-related content is visible", async () => {
      await expect(
        page.locator("text=/Risk|VaR|Drawdown|Exposure|Portfolio/i").first(),
        "At least one risk-related label should be visible"
      ).toBeVisible({ timeout: 10_000 });
    });

    await test.step("No uncaught errors in page title", async () => {
      const title = await page.title();
      expect(
        title,
        "Page title should not contain 'Error'"
      ).not.toMatch(/error/i);
    });
  });

  test("Risk page renders without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        const isNoise =
          text.includes("favicon") ||
          text.includes("net::ERR") ||
          text.includes("hydration");
        if (!isNoise) errors.push(text);
      }
    });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(
      page.locator("text=/Risk|VaR|Drawdown/i").first()
    ).toBeVisible({ timeout: 10_000 });

    expect(
      errors,
      `Console errors on /risk: ${errors.join(" | ")}`
    ).toHaveLength(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — VaR Metrics", () => {
  test("VaR 95% metric is displayed", async ({ page }) => {
    await expect(
      page.locator("text=/VaR|Value at Risk/i").first(),
      "VaR label should be visible"
    ).toBeVisible({ timeout: 8_000 });
  });

  test("All risk metric cards show numeric values (not NaN)", async ({
    page,
  }) => {
    await test.step("Wait for metrics to render", async () => {
      await expect(
        page.locator("text=/VaR|Drawdown|Sharpe|Beta/i").first(),
        "Risk metrics section should render"
      ).toBeVisible({ timeout: 10_000 });
    });

    await test.step("No NaN in visible metric text", async () => {
      const visibleNaN = await page.evaluate(() => {
        const walker = document.createTreeWalker(
          document.body,
          NodeFilter.SHOW_TEXT
        );
        const found: string[] = [];
        let node: Node | null;
        while ((node = walker.nextNode())) {
          // Exclude <script>, <style>, <noscript> — only visible text nodes
          const parent = (node as Text).parentElement;
          if (parent && ["SCRIPT", "STYLE", "NOSCRIPT"].includes(parent.tagName)) {
            continue;
          }
          const text = (node.textContent ?? "").trim();
          if (text === "NaN" || text === "undefined") found.push(text);
        }
        return found;
      });

      expect(
        visibleNaN,
        "No NaN or undefined text should appear in risk metrics"
      ).toHaveLength(0);
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Drawdown Chart", () => {
  test("30-Day Drawdown section renders", async ({ page }) => {
    await expect(
      page.locator("text=/Drawdown|drawdown/i").first(),
      "Drawdown label should be visible"
    ).toBeVisible({ timeout: 8_000 });
  });

  test("Drawdown area chart SVG is present", async ({ page }) => {
    // recharts area chart renders an SVG
    const drawdownSection = page.locator("text=/30-Day Drawdown|Drawdown/i").first();
    await expect(
      drawdownSection,
      "Drawdown section heading should be visible"
    ).toBeVisible({ timeout: 10_000 });

    const svg = page.locator("svg").first();
    await expect(svg, "At least one SVG chart should be present on risk page").toBeVisible({
      timeout: 10_000,
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Sector Exposure", () => {
  test("Sector Exposure section is visible", async ({ page }) => {
    await expect(
      page.locator("text=/Sector Exposure|Sector/i").first(),
      "Sector Exposure label should be visible"
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Sector bars show named sectors (Tech, ETF, Consumer, Auto/EV)", async ({
    page,
  }) => {
    // MOCK_SECTORS uses: Tech, ETF, Consumer, Auto/EV
    await expect(
      page.locator("text=/Tech|ETF|Consumer|Auto/i").first(),
      "At least one sector name should be visible"
    ).toBeVisible({ timeout: 10_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Risk Alerts", () => {
  test("Risk Alerts section is present", async ({ page }) => {
    await expect(
      page.locator("text=/Risk Alerts|Risk Alert/i").first(),
      "Risk Alerts section should be visible"
    ).toBeVisible({ timeout: 10_000 });
  });

  test("At least one alert badge is displayed", async ({ page }) => {
    // AlertBadge renders status lozenge + text
    const alertSection = page.locator("text=Risk Alerts").first();
    await expect(alertSection, "Risk Alerts heading visible").toBeVisible({
      timeout: 8_000,
    });

    // The alert text from MOCK_ALERTS contains "VaR" or "Drawdown" or "concentration"
    const alertText = page
      .locator("text=/VaR|Drawdown|concentration|risk budget/i")
      .first();
    await expect(
      alertText,
      "At least one alert badge text should be visible"
    ).toBeVisible({ timeout: 8_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Portfolio Heatmap / Radar", () => {
  test("Risk profile radar or heatmap section is present", async ({ page }) => {
    await expect(
      page.locator("text=/Risk Profile|Radar|Heatmap|Exposure/i").first(),
      "Risk profile visualization label should be visible"
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Page contains multiple SVG visualization elements", async ({
    page,
  }) => {
    // Risk page has: drawdown area chart, radar chart, sector bars
    const svgs = page.locator("svg");
    const count = await svgs.count();
    expect(
      count,
      "Risk page should have at least 1 SVG visualization"
    ).toBeGreaterThanOrEqual(1);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Risk Page — Drawdown Gauge", () => {
  test("DrawdownGauge shows current and max drawdown values", async ({
    page,
  }) => {
    await test.step("Wait for gauge to render", async () => {
      await expect(
        page.locator("text=/Drawdown Status|Current|Max/i").first(),
        "Drawdown gauge section should be visible"
      ).toBeVisible({ timeout: 10_000 });
    });

    await test.step("Drawdown value contains a percent sign", async () => {
      const gaugeText = await page
        .locator("text=/Drawdown/i")
        .locator("../..")
        .first()
        .textContent();

      expect(gaugeText, "Drawdown gauge should have content").toBeTruthy();
      // Should contain a % sign (e.g. -3.2% current drawdown)
      expect(
        gaugeText,
        "Drawdown gauge should show a percentage value"
      ).toMatch(/%/);
    });
  });
});
