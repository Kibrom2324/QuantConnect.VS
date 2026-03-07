/**
 * tests/pages/DashboardPage.ts
 * ─────────────────────────────
 * Page Object Model for /dashboard
 *
 * Selectors use data-testid first, then fallback to ARIA roles.
 * Add the listed data-testid attributes to the app to unlock full testability.
 *
 * Required data-testid attributes (see data-testid-checklist.md):
 *   dashboard-root, stat-card-equity, stat-card-pnl, stat-card-positions,
 *   nav-dashboard, auto-trading-banner, market-status
 */

import { Page, Locator, expect } from "@playwright/test";
import { BASE_URL } from "../../playwright.config";

export class DashboardPage {
  readonly page:              Page;

  // ── Navigation ────────────────────────────────────────────────────────────
  readonly navDashboard:      Locator;
  readonly navCharts:         Locator;
  readonly navTrading:        Locator;
  readonly navOrders:         Locator;
  readonly navWallet:         Locator;
  readonly navRisk:           Locator;
  readonly navSignals:        Locator;
  readonly navBacktest:       Locator;
  readonly navModels:         Locator;

  // ── Dashboard-specific elements ───────────────────────────────────────────
  readonly pageRoot:          Locator;
  readonly statCardEquity:    Locator;
  readonly statCardPnl:       Locator;
  readonly statCardPositions: Locator;
  readonly marketStatus:      Locator;
  readonly autoTradingBanner: Locator;
  readonly killSwitchStatus:  Locator;

  constructor(page: Page) {
    this.page = page;

    // Navigation — fall back to link text if data-testid not present
    this.navDashboard = page.getByTestId("nav-dashboard").or(page.getByRole("link", { name: "Dashboard" })).first();
    this.navCharts    = page.getByTestId("nav-charts").or(page.getByRole("link", { name: "Charts" })).first();
    this.navTrading   = page.getByTestId("nav-trading").or(page.getByRole("link", { name: "Trading" })).first();
    this.navOrders    = page.getByTestId("nav-orders").or(page.getByRole("link", { name: "Orders" })).first();
    this.navWallet    = page.getByTestId("nav-wallet").or(page.getByRole("link", { name: "Wallet" })).first();
    this.navRisk      = page.getByTestId("nav-risk").or(page.getByRole("link", { name: "Risk" })).first();
    this.navSignals   = page.getByTestId("nav-signals").or(page.getByRole("link", { name: "Signals" })).first();
    this.navBacktest  = page.getByTestId("nav-backtest").or(page.getByRole("link", { name: "Backtest" })).first();
    this.navModels    = page.getByTestId("nav-models").or(page.getByRole("link", { name: "Models" })).first();

    // Dashboard stat cards (fall back to text match)
    this.pageRoot          = page.getByTestId("dashboard-root").or(page.locator("main")).first();
    this.statCardEquity    = page.getByTestId("stat-card-equity").or(page.locator("text=/equity|portfolio value/i").first());
    this.statCardPnl       = page.getByTestId("stat-card-pnl").or(page.locator("text=/P&L|profit/i").first());
    this.statCardPositions = page.getByTestId("stat-card-positions").or(page.locator("text=/position/i").first());
    this.marketStatus      = page.getByTestId("market-status").or(page.locator("text=/market|open|closed/i").first());
    this.autoTradingBanner = page.getByTestId("auto-trading-banner").or(page.locator("[aria-label*='auto trading']").first());
    this.killSwitchStatus  = page.getByTestId("kill-switch-status").or(page.locator("text=/kill switch/i").first());
  }

  // ── Navigation actions ────────────────────────────────────────────────────

  /** Navigate directly to /dashboard */
  async goto() {
    await this.page.goto(`${BASE_URL}/dashboard`);
    await this.page.waitForURL("**/dashboard");
  }

  /** Click a sidebar nav link by name and wait for URL change */
  async navigateTo(section: "charts" | "trading" | "orders" | "wallet" | "risk" | "signals" | "backtest" | "models") {
    const linkMap: Record<string, Locator> = {
      charts:   this.navCharts,
      trading:  this.navTrading,
      orders:   this.navOrders,
      wallet:   this.navWallet,
      risk:     this.navRisk,
      signals:  this.navSignals,
      backtest: this.navBacktest,
      models:   this.navModels,
    };
    await linkMap[section].click();
    await this.page.waitForURL(`**/${section}`);
  }

  // ── Assertion helpers ─────────────────────────────────────────────────────

  /** Verify the dashboard is visible and shows the key stat cards */
  async assertLoaded() {
    await expect(this.page).toHaveURL(/\/dashboard/);
    await expect(this.page).toHaveTitle(/APEX/i);
    // At least one stat card should be visible (equity or P&L)
    await expect(this.statCardEquity.or(this.statCardPnl)).toBeVisible({ timeout: 8_000 });
  }

  /** Assert the sidebar contains all expected nav links */
  async assertSidebarNavComplete() {
    const links = [
      this.navDashboard, this.navCharts, this.navSignals,
      this.navTrading, this.navOrders, this.navWallet,
      this.navRisk, this.navBacktest, this.navModels,
    ];
    for (const link of links) {
      await expect(link).toBeVisible();
    }
  }

  /** Assert market status indicator is present and non-empty */
  async assertMarketStatusVisible() {
    await expect(this.marketStatus).toBeVisible();
    const text = await this.marketStatus.textContent();
    expect(text?.trim(), "Market status indicator should not be empty").not.toBe("");
  }

  /** Assert no severe JavaScript console errors on load */
  async assertNoConsoleErrors(errors: string[]) {
    const severe = errors.filter(e =>
      !e.includes("favicon") &&
      !e.includes("net::ERR_FAILED") &&  // Expected for mock-mode API calls
      !e.includes("Warning:")
    );
    expect(severe, `Unexpected severe console errors:\n${severe.join("\n")}`).toHaveLength(0);
  }
}
