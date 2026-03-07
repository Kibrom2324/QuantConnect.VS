/**
 * APEX Playwright Configuration
 * ─────────────────────────────
 * Run all tests:           npx playwright test
 * Run headed:              npx playwright test --headed
 * Run single file:         npx playwright test tests/e2e/trading.spec.ts
 * Debug UI mode:           npx playwright test --ui
 * Update snapshots:        npx playwright test --update-snapshots
 * Show HTML report:        npx playwright show-report
 */

import { defineConfig, devices } from "@playwright/test";
import * as path from "path";

// ─── Constants — edit these for your environment ─────────────────────────────
export const BASE_URL        = process.env.BASE_URL        ?? "http://localhost:3001";
export const API_BASE        = process.env.API_BASE        ?? "http://localhost:8000";
export const TEST_EMAIL      = process.env.TEST_EMAIL      ?? "test@apex.com";
export const TEST_PASSWORD   = process.env.TEST_PASSWORD   ?? "testpass123";
export const PREDICT_TIMEOUT = parseInt(process.env.PREDICT_TIMEOUT ?? "10000", 10); // 10 s — TFT inference budget
export const SYMBOLS         = (process.env.TEST_SYMBOLS ?? "NVDA,AAPL,MSFT").split(",");
export const HORIZONS        = ["next_1h", "next_1d", "next_1w"];

// Path where login storageState is persisted across test workers
export const AUTH_STATE_PATH = path.join(__dirname, "tests/.auth/session.json");

export default defineConfig({
  // ── Test discovery ─────────────────────────────────────────────────────────
  testDir: "./tests",
  testMatch: "**/*.spec.ts",

  // ── Parallelism ────────────────────────────────────────────────────────────
  // Fully parallel within a file is disabled for trading tests (order side-effects).
  // Enable fullyParallel only for read-only tests if needed.
  fullyParallel: false,
  workers: process.env.CI ? 2 : 4,

  // ── Retry logic ────────────────────────────────────────────────────────────
  // Retry flaky tests once in CI; zero locally (fail fast).
  retries: process.env.CI ? 2 : 1,

  // ── Timeouts ───────────────────────────────────────────────────────────────
  timeout: 30_000,               // Default per-test timeout
  expect: {
    timeout: 8_000,              // Assertion polling timeout (waitForSelector, toBeVisible …)
  },

  // ── Global setup / teardown ────────────────────────────────────────────────
  globalSetup:    "./tests/helpers/global-setup.ts",
  globalTeardown: "./tests/helpers/global-teardown.ts",

  // ── Reporting ──────────────────────────────────────────────────────────────
  reporter: [
    // Always human-readable in terminal
    ["list"],
    // Rich HTML report — open with: npx playwright show-report
    ["html", { outputFolder: "playwright-report", open: "never" }],
    // JUnit XML for CI artifact parsing
    ...(process.env.CI ? [["junit", { outputFile: "test-results/junit.xml" }] as [string, Record<string, string>]] : []),
  ],

  // ── Output artifacts ───────────────────────────────────────────────────────
  outputDir: "test-results",

  // ── Shared browser context settings ────────────────────────────────────────
  use: {
    baseURL: BASE_URL,

    // --- Screenshots: always on failure ---
    screenshot: "only-on-failure",

    // --- Video: record only when a test fails (reduces storage) ---
    video: "retain-on-failure",

    // --- Traces: capture on first retry (CI) or on failure locally ---
    trace: process.env.CI ? "on-first-retry" : "retain-on-failure",

    // Headless by default; pass --headed from CLI to override
    headless: !process.env.HEADED,

    // Reasonable navigation timeout
    navigationTimeout: 15_000,

    // Realistic locale so date assertions are consistent
    locale: "en-US",
    timezoneId: "America/New_York",
  },

  // ── Browser projects ───────────────────────────────────────────────────────
  projects: [
    // ── Auth setup (runs once before authenticated suites) ───────────────────
    {
      name: "auth-setup",
      testMatch: "**/auth.setup.ts",
      use: { ...devices["Desktop Chrome"] },
    },

    // ── Chromium (primary — all tests) ──────────────────────────────────────
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        storageState: AUTH_STATE_PATH,
      },
      dependencies: ["auth-setup"],
      testIgnore: ["**/auth.setup.ts"],
    },

    // ── Firefox (secondary — skip performance on CI to reduce cost) ──────────
    {
      name: "firefox",
      use: {
        ...devices["Desktop Firefox"],
        storageState: AUTH_STATE_PATH,
      },
      dependencies: ["auth-setup"],
      testIgnore: ["**/auth.setup.ts", "**/performance.spec.ts"],
    },

    // ── Mobile Chrome (viewport regression only) ─────────────────────────────
    {
      name: "mobile-chrome",
      use: {
        ...devices["Pixel 7"],
        storageState: AUTH_STATE_PATH,
      },
      dependencies: ["auth-setup"],
      // Only run the navigation smoke tests on mobile
      testMatch: ["**/dashboard.spec.ts", "**/navigation.spec.ts"],
    },
  ],

  // ── Dev server — start Next.js before running tests ────────────────────────
  // Comment this out when testing against a running Docker container.
  // webServer: {
  //   command: "npm run dev",
  //   url: BASE_URL,
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 60_000,
  //   stdout: "pipe",
  //   stderr: "pipe",
  // },
});
