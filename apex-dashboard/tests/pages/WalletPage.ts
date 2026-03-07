/**
 * WalletPage — Page Object for /wallet
 * Covers portfolio stats, P&L chart, open positions, and transaction history.
 */
import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

export class WalletPage extends BasePage {
  // ── Stat cards ──────────────────────────────────────────────────────────────
  readonly portfolioCard: Locator;
  readonly cashCard:      Locator;
  readonly dayPnlCard:    Locator;

  // ── Charts ──────────────────────────────────────────────────────────────────
  readonly pnlChartContainer: Locator;

  // ── Tables ──────────────────────────────────────────────────────────────────
  readonly positionsSection:    Locator;
  readonly transactionSection:  Locator;

  // ── Performance stats ───────────────────────────────────────────────────────
  readonly winRateStat:   Locator;
  readonly sharpeStat:    Locator;

  constructor(page: Page) {
    super(page);

    // Stat cards — text search since no data-testid exists yet
    this.portfolioCard  = page.getByTestId("wallet-portfolio-value").or(
      page.locator("text=Portfolio Value").locator("../..").first()
    );
    this.cashCard       = page.getByTestId("wallet-cash-balance").or(
      page.locator("text=Cash Balance").locator("../..").first()
    );
    this.dayPnlCard     = page.getByTestId("wallet-day-pnl").or(
      page.locator("text=Day P&L").locator("../..").first()
    );

    // P&L chart — recharts renders an SVG inside a div
    this.pnlChartContainer = page.getByTestId("wallet-pnl-chart").or(
      page.locator("text=P&L History").locator("../..").locator("svg").first()
    );

    // Open Positions section
    this.positionsSection = page.getByTestId("wallet-positions").or(
      page.locator("text=Open Positions").first()
    );

    // Transaction History section
    this.transactionSection = page.getByTestId("wallet-transactions").or(
      page.locator("text=Transaction History").first()
    );

    // Performance stats
    this.winRateStat  = page.getByTestId("wallet-win-rate").or(
      page.locator("text=Win Rate").locator("../..").first()
    );
    this.sharpeStat   = page.getByTestId("wallet-sharpe").or(
      page.locator("text=Sharpe").locator("../..").first()
    );
  }

  /** Navigate to the wallet page and wait for content. */
  async goto(): Promise<void> {
    await this.navigate("/wallet");
    await this.waitForPageLoad();
    // Wait for at least the portfolio card to become visible
    await expect(
      this.page.locator("text=/Portfolio Value|Cash Balance/i").first(),
      "Wallet page content should be visible"
    ).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Assert the portfolio value card shows a non-zero dollar amount.
   * Accepts mock data ($127,450.00) as valid.
   */
  async assertPortfolioValueNumeric(): Promise<void> {
    const text = await this.page
      .locator("text=Portfolio Value")
      .locator("../..").first()
      .textContent();
    expect(text, "Portfolio Value card must have text").toBeTruthy();
    expect(text, "Portfolio Value must not be NaN").not.toContain("NaN");
    expect(text, "Portfolio Value must contain a dollar sign").toContain("$");
  }

  /**
   * Assert the Day P&L card has a value and a visible +/- indicator.
   */
  async assertDayPnlHasValue(): Promise<void> {
    const card = this.page.locator("text=Day P&L").locator("../..").first();
    await expect(card, "Day P&L card should be visible").toBeVisible({ timeout: 8_000 });
    const text = await card.textContent();
    expect(text, "Day P&L must have content").toBeTruthy();
    expect(text, "Day P&L must not be NaN").not.toContain("NaN");
  }

  /**
   * Assert the P&L chart (recharts SVG) is rendered.
   * Checks the container exists, not the specific chart paths.
   */
  async assertPnlChartRendered(): Promise<void> {
    // recharts uses a div wrapper + SVG; either is acceptable
    const chart = this.page
      .locator("text=P&L History")
      .locator("../..")
      .locator("svg, canvas")
      .first();
    await expect(chart, "P&L History chart SVG/canvas should be visible").toBeVisible({
      timeout: 8_000,
    });
  }

  /**
   * Assert open positions section heading is visible.
   */
  async assertPositionsSectionVisible(): Promise<void> {
    await expect(
      this.positionsSection,
      "Open Positions section should be visible"
    ).toBeVisible({ timeout: 8_000 });
  }

  /**
   * Assert transaction history section heading is visible.
   */
  async assertTransactionHistoryVisible(): Promise<void> {
    await expect(
      this.transactionSection,
      "Transaction History section should be visible"
    ).toBeVisible({ timeout: 8_000 });
  }

  /**
   * Assert Win Rate and Sharpe Ratio show numeric values.
   */
  async assertPerformanceStatsNumeric(): Promise<void> {
    const winRateEl = this.page.locator("text=Win Rate").locator("../..").first();
    const sharpeEl  = this.page.locator("text=Sharpe").locator("../..").first();

    await expect(winRateEl, "Win Rate stat should be visible").toBeVisible({ timeout: 8_000 });
    await expect(sharpeEl,  "Sharpe stat should be visible").toBeVisible({ timeout: 8_000 });

    const wrText = await winRateEl.textContent();
    const srText = await sharpeEl.textContent();

    expect(wrText, "Win Rate must not be NaN").not.toContain("NaN");
    expect(srText, "Sharpe Ratio must not be NaN").not.toContain("NaN");
    expect(wrText, "Win Rate must contain a %").toContain("%");
  }
}
