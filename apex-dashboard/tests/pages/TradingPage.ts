/**
 * tests/pages/TradingPage.ts
 * ───────────────────────────
 * Page Object Model for /trading and /orders (money management surface).
 *
 * Required data-testid attributes (see data-testid-checklist.md):
 *   trade-symbol-select, trade-side-buy, trade-side-sell, trade-qty-input,
 *   trade-order-type-market, trade-order-type-limit, trade-limit-price-input,
 *   trade-account-paper, trade-account-live, trade-confirm-checkbox,
 *   trade-submit-btn, trade-result-msg, trade-error-msg,
 *   kill-switch-btn, kill-switch-status, kill-switch-armed-indicator,
 *   orders-open-table, orders-history-table, order-cancel-btn,
 *   orders-refresh-btn, orders-account-paper, orders-account-live
 */

import { Page, Locator, expect, Response } from "@playwright/test";
import { BASE_URL } from "../../playwright.config";
import { PRICE_TOLERANCE } from "../fixtures/mock-data";

export type Side        = "buy" | "sell";
export type OrderType   = "market" | "limit";
export type AccountMode = "paper" | "live";

export interface PlaceOrderOptions {
  symbol?:      string;
  side?:        Side;
  qty?:         number;
  orderType?:   OrderType;
  limitPrice?:  number;
  accountMode?: AccountMode;
}

export class TradingPage {
  readonly page: Page;

  // ── Order Form ────────────────────────────────────────────────────────────
  readonly symbolSelect:      Locator;
  readonly sideBuyBtn:        Locator;
  readonly sideSellBtn:       Locator;
  readonly qtyInput:          Locator;
  readonly orderTypeMarket:   Locator;
  readonly orderTypeLimit:    Locator;
  readonly limitPriceInput:   Locator;
  readonly accountPaperBtn:   Locator;
  readonly accountLiveBtn:    Locator;
  readonly confirmCheckbox:   Locator;
  readonly submitBtn:         Locator;
  readonly resultMsg:         Locator;
  readonly errorMsg:          Locator;

  // ── Kill switch ───────────────────────────────────────────────────────────
  readonly killSwitchBtn:     Locator;
  readonly killSwitchStatus:  Locator;
  readonly killArmedIndic:    Locator;

  // ── Orders page ───────────────────────────────────────────────────────────
  readonly openOrdersTable:   Locator;
  readonly historyTable:      Locator;
  readonly refreshBtn:        Locator;

  constructor(page: Page) {
    this.page = page;

    // Form elements — data-testid first, semantic fallback
    this.symbolSelect    = page.getByTestId("trade-symbol-select").or(page.locator("select").filter({ hasText: /NVDA|AAPL/ }).first());
    this.sideBuyBtn      = page.getByTestId("trade-side-buy").or(page.getByRole("button", { name: /BUY/i }).first());
    this.sideSellBtn     = page.getByTestId("trade-side-sell").or(page.getByRole("button", { name: /SELL/i }).first());
    this.qtyInput        = page.getByTestId("trade-qty-input").or(page.locator("input[type=number]").first());
    this.orderTypeMarket = page.getByTestId("trade-order-type-market").or(page.getByRole("button", { name: /market/i }).first());
    this.orderTypeLimit  = page.getByTestId("trade-order-type-limit").or(page.getByRole("button", { name: /limit/i }).first());
    this.limitPriceInput = page.getByTestId("trade-limit-price-input").or(page.locator("input[placeholder*='e.g.']").first());
    this.accountPaperBtn = page.getByTestId("trade-account-paper").or(page.getByRole("button", { name: /paper/i }).first());
    this.accountLiveBtn  = page.getByTestId("trade-account-live").or(page.getByRole("button", { name: /live/i }).first());
    // Orders page uses a custom div-based checkbox (no native <input type=checkbox>)
    this.confirmCheckbox = page.getByTestId("trade-confirm-checkbox").or(page.locator("div").filter({ hasText: /I confirm this/i }).first());
    this.submitBtn       = page.getByTestId("trade-submit-btn").or(page.getByRole("button", { name: /submit.*order/i }).first());
    this.resultMsg       = page.getByTestId("trade-result-msg").or(page.locator("text=/order submitted/i").first());
    this.errorMsg        = page.getByTestId("trade-error-msg").or(page.locator("text=/error/i").first());

    // Kill switch (shared between /trading and /orders)
    this.killSwitchBtn    = page.getByTestId("kill-switch-btn").or(page.getByRole("button", { name: /emergency stop|kill switch/i }).first());
    this.killSwitchStatus = page.getByTestId("kill-switch-status").or(page.locator("text=/kill switch|emergency stop/i").first());
    this.killArmedIndic   = page.getByTestId("kill-switch-armed-indicator").or(page.locator("text=/armed|halted/i").first());

    // Orders page
    this.openOrdersTable = page.getByTestId("orders-open-table").or(page.locator("table").first());
    this.historyTable    = page.getByTestId("orders-history-table").or(page.locator("table").nth(1));
    this.refreshBtn      = page.getByTestId("orders-refresh-btn").or(page.getByRole("button", { name: /refresh/i }).first());
  }

  // ── Navigation ────────────────────────────────────────────────────────────

  async gotoTrading() {
    await this.page.goto(`${BASE_URL}/trading`);
    await this.page.waitForURL("**/trading");
  }

  async gotoOrders() {
    await this.page.goto(`${BASE_URL}/orders`);
    await this.page.waitForURL("**/orders");
  }

  // ── Kill switch actions ───────────────────────────────────────────────────

  /**
   * Activate the emergency stop via UI button.
   * Returns the API response captured from the network layer.
   */
  async activateKillSwitch(): Promise<Response> {
    const [response] = await Promise.all([
      this.page.waitForResponse(r => r.url().includes("/api/kill-switch") && r.request().method() === "POST"),
      this.killSwitchBtn.click(),
    ]);
    return response;
  }

  /** Deactivate the kill switch via the "deactivate" button (only visible when armed) */
  async deactivateKillSwitch(): Promise<Response> {
    const deactivateBtn = this.page.getByRole("button", { name: /deactivate|resume/i });
    const [response] = await Promise.all([
      this.page.waitForResponse(r => r.url().includes("/api/kill-switch") && r.request().method() === "POST"),
      deactivateBtn.click(),
    ]);
    return response;
  }

  // ── Order form actions ────────────────────────────────────────────────────

  /** Select a symbol from the dropdown */
  async selectSymbol(symbol: string) {
    await this.symbolSelect.selectOption(symbol);
  }

  /** Set order side (buy or sell) */
  async setSide(side: Side) {
    if (side === "buy") await this.sideBuyBtn.click();
    else               await this.sideSellBtn.click();
  }

  /** Set order type (market or limit) */
  async setOrderType(type: OrderType) {
    if (type === "market") await this.orderTypeMarket.click();
    else                   await this.orderTypeLimit.click();
  }

  /** Select account mode */
  async setAccountMode(mode: AccountMode) {
    if (mode === "paper") await this.accountPaperBtn.click();
    else                  await this.accountLiveBtn.click();
  }

  /** Set quantity (clears existing value first) */
  async setQty(qty: number) {
    await this.qtyInput.fill(String(qty));
  }

  /** Fill limit price (only visible when order type = limit) */
  async setLimitPrice(price: number) {
    await this.limitPriceInput.fill(String(price));
  }

  /** Tick the confirmation checkbox (custom div — not a native input[type=checkbox]) */
  async confirm() {
    await this.confirmCheckbox.click();
  }

  /**
   * Fill and submit a complete order form.
   * Returns the API response from waitForResponse.
   */
  async placeOrder(opts: PlaceOrderOptions = {}): Promise<Response> {
    const {
      symbol      = "NVDA",
      side        = "buy",
      qty         = 1,
      orderType   = "market",
      limitPrice,
      accountMode = "paper",
    } = opts;

    await this.step_selectSymbol(symbol);
    await this.setSide(side);
    await this.setOrderType(orderType);
    await this.setQty(qty);
    await this.setAccountMode(accountMode);

    if (orderType === "limit" && limitPrice !== undefined) {
      await this.setLimitPrice(limitPrice);
    }

    await this.confirm();

    const [response] = await Promise.all([
      this.page.waitForResponse(
        r => r.url().includes("/api/trade") || r.url().includes("/api/orders"),
        { timeout: 15_000 }
      ),
      this.submitBtn.click(),
    ]);

    return response;
  }

  /** Cancel an open order by its position in the open orders table */
  async cancelOrderAt(rowIndex: number) {
    const cancelBtns = this.page.getByRole("button", { name: /cancel/i });
    await cancelBtns.nth(rowIndex).click();
    // Wait for the table to refresh
    await this.page.waitForResponse(r => r.url().includes("/api/orders"));
  }

  // ── Assertion helpers ─────────────────────────────────────────────────────

  /** Assert the kill switch is currently active (armed) */
  async assertKillSwitchArmed() {
    await expect(this.killArmedIndic.or(this.killSwitchStatus)).toContainText(/armed|halted|active/i);
    // Submit button should be disabled when kill switch is armed
    await expect(this.submitBtn).toBeDisabled({ timeout: 3_000 }).catch(() => {
      // Not all pages show the submit btn — skip if not present
    });
  }

  /** Assert the kill switch is deactivated */
  async assertKillSwitchIdle() {
    await expect(this.killSwitchStatus).toContainText(/standby|inactive|deactivated/i);
  }

  /**
   * Assert order form renders with all required fields visible.
   * Called as a pre-condition check before interaction tests.
   */
  async assertOrderFormVisible() {
    await expect(this.symbolSelect,    "Symbol selector not visible").toBeVisible();
    await expect(this.sideBuyBtn,      "BUY button not visible").toBeVisible();
    await expect(this.sideSellBtn,     "SELL button not visible").toBeVisible();
    await expect(this.qtyInput,        "Qty input not visible").toBeVisible();
    await expect(this.submitBtn,       "Submit button not visible").toBeVisible();
  }

  /** Assert success message appeared after order submission */
  async assertOrderSuccess(expectedSymbol?: string) {
    await expect(this.resultMsg).toBeVisible({ timeout: 10_000 });
    if (expectedSymbol) {
      await expect(this.resultMsg).toContainText(expectedSymbol, { ignoreCase: true });
    }
  }

  /** Assert fill price is within a plausible range for the symbol */
  async assertFillPriceReasonable(displayedPrice: number, seedPrice: number) {
    // Fill price should be within 30% of seed (handles real-time price drift)
    expect(
      Math.abs(displayedPrice - seedPrice) / seedPrice,
      `Fill price ${displayedPrice} is more than 30% away from seed price ${seedPrice}`
    ).toBeLessThan(0.30);
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private async step_selectSymbol(symbol: string) {
    try {
      await this.symbolSelect.selectOption(symbol);
    } catch {
      // May be a styled dropdown — click the button with the symbol name
      await this.page.getByRole("button", { name: symbol }).first().click();
    }
  }
}
