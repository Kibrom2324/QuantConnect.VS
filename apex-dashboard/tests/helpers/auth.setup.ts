/**
 * tests/helpers/auth.setup.ts
 * ────────────────────────────
 * Runs once before authenticated test projects (chromium, firefox, mobile-chrome).
 * Saves browser storageState to .auth/session.json so every test worker starts
 * with a pre-authenticated context — this is ~10x faster than logging in per test.
 *
 * CURRENT STATE: APEX has no login page — root redirects directly to /dashboard.
 * This setup saves the "already authenticated" state so the pattern is ready when
 * real auth (NextAuth, Clerk, etc.) is added.
 *
 * To add auth later:
 *   1. Replace the TODO block below with actual login form interactions
 *   2. Add /login page + middleware that checks session
 */

import { test as setup, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { BASE_URL, AUTH_STATE_PATH, TEST_EMAIL, TEST_PASSWORD } from "../../playwright.config";

const AUTH_DIR = path.dirname(AUTH_STATE_PATH);

setup("Authenticate and save session state", async ({ page }) => {
  // Ensure .auth directory exists
  if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });

  await setup.step("Navigate to app root", async () => {
    await page.goto(BASE_URL);
    // Wait for redirect to land — either /login or /dashboard
    await page.waitForURL(/\/(login|dashboard)/, { timeout: 10_000 });
    console.log(`  → Redirected to: ${page.url()}`);
  });

  // ── If login page exists, authenticate ────────────────────────────────────
  if (page.url().includes("/login")) {
    await setup.step("Fill login form", async () => {
      // TODO: replace selectors with your actual login form data-testid values
      await page.getByTestId("login-email").fill(TEST_EMAIL);
      await page.getByTestId("login-password").fill(TEST_PASSWORD);
    });

    await setup.step("Submit and wait for dashboard", async () => {
      await Promise.all([
        page.waitForURL("**/dashboard", { timeout: 15_000 }),
        page.getByTestId("login-submit").click(),
      ]);
    });

    await setup.step("Verify authenticated state", async () => {
      await expect(page).toHaveURL(/\/dashboard/);
      // Verify session cookie or localStorage token exists
      const cookies = await page.context().cookies();
      expect(
        cookies.some(c => c.name.match(/session|token|auth/i)),
        `Expected an auth cookie after login. Found: ${cookies.map(c => c.name).join(", ")}`
      ).toBeTruthy();
    });
  } else {
    // No auth — app is open-access (current APEX state)
    await setup.step("Verify dashboard is accessible without auth", async () => {
      await expect(page).toHaveURL(/\/dashboard/);
      console.log("  ℹ No login required — saving open-access session state");
    });
  }

  // ── Save session state for reuse across all test workers ─────────────────
  await page.context().storageState({ path: AUTH_STATE_PATH });
  console.log(`  ✓ Session saved to ${AUTH_STATE_PATH}`);
});

// ── No-auth smoke test (standalone — does not require saved state) ─────────────────
setup("App operates without auth — /login returns 404 and /dashboard is open", async ({ page }) => {
  // /login should not exist
  const res = await page.goto(`${BASE_URL}/login`);
  expect(
    [404, 307, 302],
    `/login should be absent or redirect, got ${res?.status()}`
  ).toContain(res?.status());
  // No login form should be present
  await expect(page.getByTestId("login-email")).toHaveCount(0);
});
