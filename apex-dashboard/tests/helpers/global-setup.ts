/**
 * Global setup — runs once before the entire test suite.
 * Use this to verify the app is reachable before wasting test time.
 */
import { chromium, FullConfig } from "@playwright/test";
import { BASE_URL, API_BASE } from "../../playwright.config";

export default async function globalSetup(_config: FullConfig) {
  console.log("\n🔧 APEX Playwright global setup starting…");

  // ── 1. Verify UI is reachable ─────────────────────────────────────────────
  const browser = await chromium.launch();
  const page    = await browser.newPage();

  // Root redirects 307 → /dashboard; navigate directly to avoid false failure
  try {
    const res = await page.goto(`${BASE_URL}/dashboard`, { waitUntil: "domcontentloaded", timeout: 20_000 });
    if (!res || !res.ok()) {
      throw new Error(`APEX UI not reachable at ${BASE_URL}/dashboard — HTTP ${res?.status()}`);
    }
    console.log(`  ✓ UI reachable: ${BASE_URL}/dashboard`);
  } catch (e) {
    console.error(`  ✗ UI not reachable: ${e}`);
    throw e;
  } finally {
    await browser.close();
  }

  // ── 2. Verify backend health endpoint ────────────────────────────────────
  try {
    const apiRes = await fetch(`${BASE_URL}/api/health`, { signal: AbortSignal.timeout(5_000) });
    console.log(`  ✓ API health: ${apiRes.status}`);
  } catch {
    // Non-fatal — tests will individually handle API failures
    console.warn(`  ⚠ API health check failed (non-fatal) — tests will use mock data`);
  }

  console.log("🔧 Global setup complete\n");
}
