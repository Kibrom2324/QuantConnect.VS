/**
 * Global teardown — runs once after the entire test suite.
 */
import { FullConfig } from "@playwright/test";

export default async function globalTeardown(_config: FullConfig) {
  console.log("\n🧹 APEX Playwright global teardown…");

  // Deactivate kill switch if any test left it armed
  try {
    await fetch("http://localhost:3001/api/kill-switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: false, reason: "Playwright teardown cleanup" }),
      signal: AbortSignal.timeout(3_000),
    });
    console.log("  ✓ Kill switch deactivated (cleanup)");
  } catch {
    console.warn("  ⚠ Could not deactivate kill switch (app may be down)");
  }

  console.log("🧹 Teardown complete\n");
}
