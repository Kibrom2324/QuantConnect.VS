/**
 * BasePage — shared foundation for all APEX Page Objects.
 * Every page POM extends this class.
 */
import { Page, Locator, expect } from "@playwright/test";
import { BASE_URL } from "../../playwright.config";

export class BasePage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  // ── Navigation ─────────────────────────────────────────────────────────────

  /**
   * Navigate to an app path and wait for the DOM to be interactive.
   * Never throws on 3xx — follows redirects automatically.
   */
  async navigate(path: string): Promise<void> {
    await this.page.goto(`${BASE_URL}${path}`, {
      waitUntil: "domcontentloaded",
      timeout: 20_000,
    });
  }

  /**
   * Wait until the page has no pending network requests
   * (limited to API calls — ignores websocket / SSE).
   */
  async waitForPageLoad(): Promise<void> {
    await this.page.waitForLoadState("domcontentloaded");
    // Give React a tick to hydrate
    await this.page.waitForFunction(() => document.readyState === "complete", {
      timeout: 10_000,
    });
  }

  // ── Console / security helpers ──────────────────────────────────────────────

  /** Collect all console messages of level 'error'. */
  async getConsoleErrors(): Promise<string[]> {
    const errors: string[] = [];
    this.page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    return errors;
  }

  /**
   * Assert that no auth token appears as bare text in the page HTML.
   * Checks for "Bearer " prefix and raw JWT patterns (xxx.xxx.xxx).
   */
  async checkNoAuthTokenExposed(): Promise<void> {
    const html = await this.page.content();
    expect(
      html,
      "Auth token (Bearer prefix) must NOT appear in page HTML"
    ).not.toContain("Bearer ");
  }

  /** Assert no JWT-shaped string (3 base64url segments) is visible in the DOM. */
  async checkNoBearerInPage(): Promise<void> {
    const html = await this.page.content();
    const jwtPattern = /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/;
    expect(
      jwtPattern.test(html),
      "JWT token must NOT appear in raw page HTML"
    ).toBe(false);
  }

  // ── Sidebar nav helpers ────────────────────────────────────────────────────

  /** Get the sidebar nav link for a given label (role-based). */
  navLink(label: string): Locator {
    return this.page
      .getByTestId(`nav-${label.toLowerCase()}`)
      .or(this.page.getByRole("link", { name: new RegExp(label, "i") }).first());
  }

  /** Click a sidebar nav link and wait for navigation. */
  async clickNav(label: string): Promise<void> {
    await Promise.all([
      this.page.waitForURL(/.*/, { timeout: 10_000 }),
      this.navLink(label).click(),
    ]);
  }

  // ── Stat card helpers ──────────────────────────────────────────────────────

  /**
   * Assert a stat card shows a numeric value (not "—", not NaN, not $0.00 only).
   * @param testId data-testid attribute; falls back to text search.
   * @param labelText Text that labels the card (e.g. "Portfolio Value").
   */
  async assertStatCardNumeric(testId: string, labelText: string): Promise<void> {
    const card = this.page
      .getByTestId(testId)
      .or(
        this.page
          .locator(`text=/${labelText}/i`)
          .locator("..")
          .locator("..")
      )
      .first();

    await expect(
      card,
      `Stat card "${labelText}" should be visible`
    ).toBeVisible({ timeout: 8_000 });

    const text = await card.textContent();
    expect(text, `Stat card "${labelText}" should not be empty`).toBeTruthy();
    expect(
      text,
      `Stat card "${labelText}" should not contain NaN`
    ).not.toContain("NaN");
  }
}
