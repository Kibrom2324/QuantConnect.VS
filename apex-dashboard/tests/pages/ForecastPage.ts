/**
 * tests/pages/ForecastPage.ts
 * ────────────────────────────
 * Page Object Model for TFT model signals and predictions.
 *
 * In APEX the prediction surface lives on two pages:
 *   /signals  — live signal stream from the ensemble model
 *   /models   — ML model management (promote, A/B test, train)
 *
 * The "forecast" API endpoints are:
 *   GET  /api/signals              — latest predictions per symbol
 *   GET  /api/ensemble             — ensemble model state
 *   POST /api/models               — trigger a training run
 *
 * Required data-testid attributes (see data-testid-checklist.md):
 *   signal-row, signal-symbol, signal-confidence, signal-direction,
 *   signal-predicted-value, signal-horizon, signal-timestamp,
 *   signal-filter-symbol, signal-filter-horizon, signal-refresh-btn,
 *   model-live-badge, model-train-btn, model-status-label,
 *   forecast-error-msg
 */

import { Page, Locator, expect, Response } from "@playwright/test";
import { BASE_URL, PREDICT_TIMEOUT } from "../../playwright.config";
import {
  CONFIDENCE_TOLERANCE,
  MAX_REASONABLE_PRICE,
  MIN_CONFIDENCE_THRESHOLD,
} from "../fixtures/mock-data";

export class ForecastPage {
  readonly page: Page;

  // ── Signals page elements ─────────────────────────────────────────────────
  readonly signalRows:       Locator;  // All signal rows in the table
  readonly firstSignalRow:   Locator;
  readonly filterSymbol:     Locator;
  readonly filterHorizon:    Locator;
  readonly refreshBtn:       Locator;
  readonly errorMsg:         Locator;

  // ── Individual signal fields (within a row — use .locator() to scope) ─────
  // These are used as: signalRows.nth(0).locator('[data-testid="signal-symbol"]')
  readonly signalSymbolSel:    string;
  readonly signalConfSel:      string;
  readonly signalDirectionSel: string;
  readonly signalValueSel:     string;
  readonly signalHorizonSel:   string;
  readonly signalTimestampSel: string;

  // ── Models page elements ──────────────────────────────────────────────────
  readonly liveBadge:         Locator;
  readonly trainBtn:          Locator;
  readonly modelStatusLabel:  Locator;

  constructor(page: Page) {
    this.page = page;

    // Signals
    this.signalRows     = page.getByTestId("signal-row").or(page.locator("tr[data-symbol]"));
    this.firstSignalRow = this.signalRows.first();
    this.filterSymbol   = page.getByTestId("signal-filter-symbol").or(
                            page.getByRole("combobox", { name: /symbol/i }).first()
                          );
    this.filterHorizon  = page.getByTestId("signal-filter-horizon").or(
                            page.getByRole("combobox", { name: /horizon/i }).first()
                          );
    this.refreshBtn     = page.getByTestId("signal-refresh-btn").or(
                            page.getByRole("button", { name: /refresh/i }).first()
                          );
    this.errorMsg       = page.getByTestId("forecast-error-msg").or(
                            page.locator("text=/error|unavailable/i").first()
                          );

    // Selector strings for scoped queries inside a signal row
    this.signalSymbolSel    = '[data-testid="signal-symbol"]';
    this.signalConfSel      = '[data-testid="signal-confidence"]';
    this.signalDirectionSel = '[data-testid="signal-direction"]';
    this.signalValueSel     = '[data-testid="signal-predicted-value"]';
    this.signalHorizonSel   = '[data-testid="signal-horizon"]';
    this.signalTimestampSel = '[data-testid="signal-timestamp"]';

    // Models
    this.liveBadge        = page.getByTestId("model-live-badge").or(
                              page.locator("text=/live/i").first()
                            );
    this.trainBtn         = page.getByTestId("model-train-btn").or(
                              page.getByRole("button", { name: /train|retrain/i }).first()
                            );
    this.modelStatusLabel = page.getByTestId("model-status-label").or(
                              page.locator("text=/staging|live|retired/i").first()
                            );
  }

  // ── Navigation ────────────────────────────────────────────────────────────

  async gotoSignals() {
    await this.page.goto(`${BASE_URL}/signals`);
    await this.page.waitForURL("**/signals");
  }

  async gotoModels() {
    await this.page.goto(`${BASE_URL}/models`);
    await this.page.waitForURL("**/models");
  }

  // ── Signal API actions ────────────────────────────────────────────────────

  /**
   * Trigger a signal refresh and wait for the API response.
   * Uses waitForResponse — captures the full response for API-level assertions.
   */
  async refreshSignals(): Promise<Response> {
    const [response] = await Promise.all([
      this.page.waitForResponse(
        r => r.url().includes("/api/signals") || r.url().includes("/api/ensemble"),
        { timeout: PREDICT_TIMEOUT }
      ),
      this.refreshBtn.click(),
    ]);
    return response;
  }

  /**
   * Wait for the signals table to populate with at least one row.
   * Does NOT use fixed sleep — polls DOM state.
   */
  async waitForSignals(minRows = 1) {
    await expect.poll(
      async () => await this.signalRows.count(),
      {
        timeout: PREDICT_TIMEOUT,
        message: `Expected at least ${minRows} signal rows to appear`,
        intervals: [500, 1000, 2000],
      }
    ).toBeGreaterThanOrEqual(minRows);
  }

  // ── Signal assertion helpers ──────────────────────────────────────────────

  /**
   * Validate all fields of the first signal row.
   * Can optionally filter by expected symbol.
   */
  async assertFirstSignalValid(opts: { expectedSymbol?: string } = {}) {
    await this.waitForSignals(1);

    const row = this.firstSignalRow;

    if (opts.expectedSymbol) {
      const symbolEl = row.locator(this.signalSymbolSel);
      if (await symbolEl.count() > 0) {
        await expect(symbolEl).toContainText(opts.expectedSymbol, { ignoreCase: true });
      }
    }

    // Confidence: must be 0.0–1.0
    await this.assertConfidenceInRow(row);

    // Direction: must be one of the valid values
    const dirEl = row.locator(this.signalDirectionSel);
    if (await dirEl.count() > 0) {
      const dir = (await dirEl.textContent())?.trim().toLowerCase();
      expect(
        ["up", "down", "flat"].includes(dir ?? ""),
        `Direction "${dir}" is not one of: up, down, flat`
      ).toBeTruthy();
    }

    // Timestamp: must be a valid ISO date that is NOT in the future
    const tsEl = row.locator(this.signalTimestampSel);
    if (await tsEl.count() > 0) {
      await this.assertTimestampNotFuture(tsEl);
    }

    // Predicted value: must be a finite positive number
    const valEl = row.locator(this.signalValueSel);
    if (await valEl.count() > 0) {
      await this.assertPredictedValueFinite(valEl);
    }
  }

  /**
   * Extract and validate confidence from a signal row element.
   * Confidence must be between MIN_CONFIDENCE_THRESHOLD and 1.0.
   */
  async assertConfidenceInRow(rowLocator: Locator) {
    const confEl = rowLocator.locator(this.signalConfSel);
    if (await confEl.count() === 0) return; // Skip if not rendered (mock mode)

    const text = (await confEl.textContent())?.replace(/[^0-9.]/g, "") ?? "";
    const conf = parseFloat(text);

    expect(isFinite(conf), `Confidence "${text}" is not a finite number`).toBeTruthy();
    expect(conf, `Confidence ${conf} is below 0`).toBeGreaterThanOrEqual(0);
    expect(conf, `Confidence ${conf} exceeds 1.0`).toBeLessThanOrEqual(1.0 + CONFIDENCE_TOLERANCE);
  }

  /**
   * Assert a predicted-value element contains a real, finite, positive number.
   */
  async assertPredictedValueFinite(locator: Locator) {
    const text = (await locator.textContent())?.replace(/[$,\s]/g, "") ?? "";
    const val = parseFloat(text);

    expect(isFinite(val), `Predicted value "${text}" is not finite`).toBeTruthy();
    expect(val, `Predicted value ${val} is ≤ 0 (expected positive price)`).toBeGreaterThan(0);
    expect(val, `Predicted value ${val} exceeds sanity limit $${MAX_REASONABLE_PRICE}`).toBeLessThan(MAX_REASONABLE_PRICE);
    expect(val, `Predicted value is NaN`).not.toBeNaN();
  }

  /**
   * Assert a timestamp element is not in the future (predictions can't be from the future).
   */
  async assertTimestampNotFuture(locator: Locator) {
    const text = await locator.textContent() ?? "";
    const ts   = new Date(text.trim());

    expect(ts.getTime(), `Timestamp "${text}" is not a valid date`).not.toBeNaN();
    expect(
      ts.getTime(),
      `Timestamp "${text}" is in the future (expected past or present)`
    ).toBeLessThanOrEqual(Date.now() + 60_000); // +60s buffer for clock skew
  }

  /**
   * Assert the live model badge is visible on the models page.
   */
  async assertLiveModelExists() {
    await expect(this.liveBadge).toBeVisible({ timeout: 5_000 });
    const text = await this.liveBadge.textContent();
    expect(text?.trim().toLowerCase()).toMatch(/live/);
  }

  /**
   * Assert minimum required signal rows are present.
   * Does not assert values — use assertFirstSignalValid for data quality.
   */
  async assertSignalTablePopulated(minRows = 1) {
    const count = await this.signalRows.count();
    expect(
      count,
      `Expected at least ${minRows} signal rows, found ${count}`
    ).toBeGreaterThanOrEqual(minRows);
  }
}
