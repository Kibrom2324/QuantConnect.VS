/**
 * tests/e2e/performance.spec.ts
 * ──────────────────────────────
 * Performance and timing tests.
 * Lives in a separate file from E2E functional tests for two reasons:
 *   1. Performance tests have a longer, different timeout ceiling
 *   2. Failures are SLO violations (not bugs) — different triage path
 *
 * All thresholds are documented with constants + comments explaining WHY.
 */

import { test, expect } from "@playwright/test";
import { BASE_URL, API_BASE, PREDICT_TIMEOUT } from "../../playwright.config";

// ─── Performance Budget Constants ────────────────────────────────────────────
// These are NOT arbitrary — each is calibrated to a specific user experience goal.

/**
 * 3,000ms — Dashboard load budget.
 * Google's "good" threshold for Time to Interactive.
 * Rationale: traders open the dashboard at market open under stress.
 * A 3s hang feels broken in that context.
 */
const DASHBOARD_LOAD_MS = 3_000;

/**
 * 2,000ms — Charts page load budget.
 * Chart rendering (recharts + canvas) is heavier than a data table.
 * But charts are navigated to less urgently than dashboard,
 * so 2s is acceptable (≈ 1s render + 1s API margin).
 */
const CHARTS_LOAD_MS = 2_000;

/**
 * 2,000ms — Signals page load budget.
 * Signals are a real-time feed — must render fast to feel "live".
 */
const SIGNALS_LOAD_MS = 2_000;

/**
 * PREDICT_TIMEOUT (10,000ms) — TFT model inference budget.
 * The TFT model runs on CPU in dev; 10s is aggressive but keeps the
 * UX from feeling broken. In production with GPU this should be <2s.
 */
const PREDICT_API_MS = PREDICT_TIMEOUT;

/**
 * 500ms — API health check budget.
 * Health endpoint should be instant (no DB, no ML). If it takes >500ms,
 * something is seriously wrong with the server process.
 */
const HEALTH_CHECK_MS = 500;

/**
 * Largest Contentful Paint threshold: 2,500ms.
 * Google Core Web Vital "good" threshold for LCP.
 */
const LCP_GOOD_MS = 2_500;

/**
 * Cumulative Layout Shift: 0.1.
 * Google Core Web Vital "good" threshold for CLS.
 * Trading dashboards with lots of numbers shifting =  bad UX + reading errors.
 */
const CLS_GOOD = 0.1;

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Page Load Performance", () => {
  // Override timeout for all performance tests
  test.setTimeout(30_000);

  test(`Dashboard loads under ${DASHBOARD_LOAD_MS}ms`, async ({ page }) => {
    const start = Date.now();

    await test.step("Navigate and wait for DOM content", async () => {
      await page.goto(`${BASE_URL}/dashboard`, { waitUntil: "domcontentloaded" });
    });

    const loadTime = Date.now() - start;

    await test.step("Verify page rendered", async () => {
      await expect(page).toHaveURL(/\/dashboard/);
      const body = await page.locator("body").textContent();
      expect(body?.trim().length, "Dashboard body is empty").toBeGreaterThan(50);
    });

    await test.step(`Assert load time < ${DASHBOARD_LOAD_MS}ms`, async () => {
      test.info().annotations.push({
        type: "timing",
        description: `Dashboard load: ${loadTime}ms (budget: ${DASHBOARD_LOAD_MS}ms)`,
      });
      expect(
        loadTime,
        `Dashboard took ${loadTime}ms — over the ${DASHBOARD_LOAD_MS}ms budget`
      ).toBeLessThan(DASHBOARD_LOAD_MS);
    });
  });

  test(`Charts page loads under ${CHARTS_LOAD_MS}ms`, async ({ page }) => {
    const start    = Date.now();
    await page.goto(`${BASE_URL}/charts`, { waitUntil: "domcontentloaded" });
    const loadTime = Date.now() - start;

    test.info().annotations.push({
      type: "timing",
      description: `Charts load: ${loadTime}ms (budget: ${CHARTS_LOAD_MS}ms)`,
    });

    await expect(page).toHaveURL(/\/charts/);
    expect(loadTime, `Charts took ${loadTime}ms — over ${CHARTS_LOAD_MS}ms budget`).toBeLessThan(CHARTS_LOAD_MS);
  });

  test(`Signals page loads under ${SIGNALS_LOAD_MS}ms`, async ({ page }) => {
    const start    = Date.now();
    await page.goto(`${BASE_URL}/signals`, { waitUntil: "domcontentloaded" });
    const loadTime = Date.now() - start;

    test.info().annotations.push({
      type: "timing",
      description: `Signals load: ${loadTime}ms (budget: ${SIGNALS_LOAD_MS}ms)`,
    });

    await expect(page).toHaveURL(/\/signals/);
    expect(loadTime, `Signals took ${loadTime}ms — over ${SIGNALS_LOAD_MS}ms budget`).toBeLessThan(SIGNALS_LOAD_MS);
  });

  test("All 8 pages load under 5,000ms each (smoke)", async ({ page }) => {
    const PAGES = [
      "/dashboard", "/charts", "/signals", "/trading",
      "/orders", "/wallet", "/risk", "/backtest", "/models",
    ];
    const BUDGET = 5_000;
    const results: { path: string; ms: number }[] = [];

    for (const path of PAGES) {
      const start = Date.now();
      await page.goto(`${BASE_URL}${path}`, { waitUntil: "domcontentloaded" });
      const ms = Date.now() - start;
      results.push({ path, ms });
    }

    // Log all timings to report
    test.info().annotations.push({
      type: "page-load-timings",
      description: results.map(r => `${r.path}: ${r.ms}ms`).join(", "),
    });

    for (const { path, ms } of results) {
      expect(ms, `${path} took ${ms}ms — exceeds ${BUDGET}ms smoke budget`).toBeLessThan(BUDGET);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("API Response Time", () => {
  test.setTimeout(PREDICT_API_MS + 5_000);

  test(`GET /api/signals responds under ${PREDICT_API_MS}ms`, async ({ request }) => {
    const start = Date.now();
    const res   = await request.get(`${BASE_URL}/api/signals`);
    const ms    = Date.now() - start;

    test.info().annotations.push({
      type: "api-timing",
      description: `GET /api/signals: ${ms}ms`,
    });

    expect(res.status()).toBe(200);
    expect(ms, `GET /api/signals took ${ms}ms — over ${PREDICT_API_MS}ms budget`).toBeLessThan(PREDICT_API_MS);
  });

  test(`GET /api/health responds under ${HEALTH_CHECK_MS}ms (when implemented)`, async ({ request }) => {
    const start = Date.now();
    const res   = await request.get(`${BASE_URL}/api/health`);
    const ms    = Date.now() - start;

    test.skip(res.status() === 404, "Skipped — /api/health not implemented yet");

    test.info().annotations.push({
      type: "api-timing",
      description: `GET /api/health: ${ms}ms`,
    });

    expect(ms, `Health check took ${ms}ms — over ${HEALTH_CHECK_MS}ms budget`).toBeLessThan(HEALTH_CHECK_MS);
  });

  test(`GET /api/orders responds under 2,000ms`, async ({ request }) => {
    const BUDGET = 2_000;
    const start  = Date.now();
    const res    = await request.get(`${BASE_URL}/api/orders?limit=5&account_mode=paper`);
    const ms     = Date.now() - start;

    test.info().annotations.push({
      type: "api-timing",
      description: `GET /api/orders: ${ms}ms`,
    });

    expect(res.status()).toBe(200);
    expect(ms, `GET /api/orders took ${ms}ms — over 2000ms budget`).toBeLessThan(BUDGET);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
test.describe("Core Web Vitals", () => {
  test.setTimeout(20_000);

  test(`Dashboard: LCP < ${LCP_GOOD_MS}ms and CLS < ${CLS_GOOD}`, async ({ page }) => {
    // Inject PerformanceObserver to collect LCP and CLS before navigation
    await page.addInitScript(() => {
      (window as Window & { __lcp?: number; __cls?: number }).__lcp = 0;
      (window as Window & { __lcp?: number; __cls?: number }).__cls = 0;

      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const e = entry as PerformanceEntry & { startTime?: number };
          if (entry.entryType === "largest-contentful-paint" && e.startTime !== undefined) {
            (window as Window & { __lcp?: number }).__lcp = e.startTime;
          }
        }
      }).observe({ type: "largest-contentful-paint", buffered: true });

      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const e = entry as PerformanceEntry & { value?: number };
          if (entry.entryType === "layout-shift" && !(e as { hadRecentInput?: boolean }).hadRecentInput && e.value !== undefined) {
            (window as Window & { __cls?: number }).__cls = ((window as Window & { __cls?: number }).__cls ?? 0) + e.value;
          }
        }
      }).observe({ type: "layout-shift", buffered: true });
    });

    // Use "load" not "networkidle" — dashboard has background polling that keeps network busy
    await page.goto(`${BASE_URL}/dashboard`, { waitUntil: "load", timeout: 15_000 });
    // Wait for paint events to settle
    await page.waitForTimeout(1_000);

    const lcp = await page.evaluate(() => (window as Window & { __lcp?: number }).__lcp ?? 0);
    const cls = await page.evaluate(() => (window as Window & { __cls?: number }).__cls ?? 0);

    test.info().annotations.push({
      type: "core-web-vitals",
      description: `LCP: ${lcp.toFixed(0)}ms (budget: ${LCP_GOOD_MS}ms) | CLS: ${cls.toFixed(4)} (budget: ${CLS_GOOD})`,
    });

    expect(lcp, `LCP ${lcp.toFixed(0)}ms exceeds ${LCP_GOOD_MS}ms threshold`).toBeLessThan(LCP_GOOD_MS);
    expect(cls, `CLS ${cls.toFixed(4)} exceeds ${CLS_GOOD} threshold`).toBeLessThan(CLS_GOOD);
  });
});
