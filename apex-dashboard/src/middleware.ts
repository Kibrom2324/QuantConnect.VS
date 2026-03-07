/**
 * APEX Dashboard — API Security Middleware
 *
 * Protects write/destructive API routes with a shared secret header:
 *   X-APEX-API-Key: <DASHBOARD_API_KEY env var>
 *
 * Read-only routes (health, positions, signals, etc.) are public
 * because they expose no sensitive write operations.
 *
 * Protected routes (POST/DELETE actions that change state):
 *   /api/trade
 *   /api/kill-switch/enable
 *   /api/kill-switch/disable
 *   /api/weights        (POST)
 *   /api/trading-mode   (POST)
 *   /api/models/:id/promote
 *   /api/models/:id/demote
 */

import { NextRequest, NextResponse } from "next/server";

// Routes that require the API key header
const PROTECTED_PATHS = [
  "/api/trade",
  "/api/kill-switch/enable",
  "/api/kill-switch/disable",
  "/api/trading-mode",
  "/api/weights",
  "/api/models",          // covers promote/demote sub-routes
];

// Write methods that need protection (GET is always allowed)
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const method = req.method;

  // Only apply to protected paths + write methods
  const isProtected =
    WRITE_METHODS.has(method) &&
    PROTECTED_PATHS.some((p) => pathname.startsWith(p));

  if (!isProtected) {
    return NextResponse.next();
  }

  const apiKey = process.env.DASHBOARD_API_KEY;

  // If no key is configured, warn in production but allow in dev
  if (!apiKey) {
    if (process.env.NODE_ENV === "production") {
      return NextResponse.json(
        { error: "Server misconfiguration: DASHBOARD_API_KEY not set." },
        { status: 500 }
      );
    }
    // Dev: allow without key but log a warning
    console.warn(
      "[APEX] DASHBOARD_API_KEY not set — API routes are unprotected (dev mode)"
    );
    return NextResponse.next();
  }

  // Check Authorization header (Bearer token)
  const authHeader = req.headers.get("authorization") ?? "";
  const bearerToken = authHeader.startsWith("Bearer ")
    ? authHeader.slice(7)
    : "";

  // Also accept X-APEX-API-Key header for non-browser clients
  const directKey = req.headers.get("x-apex-api-key") ?? "";

  const provided = bearerToken || directKey;

  if (!provided) {
    return NextResponse.json(
      { error: "Missing API key. Provide Authorization: Bearer <key> or X-APEX-API-Key header." },
      { status: 401 }
    );
  }

  // Constant-time comparison to prevent timing attacks
  if (provided.length !== apiKey.length || !timingSafeEqual(provided, apiKey)) {
    return NextResponse.json(
      { error: "Invalid API key." },
      { status: 403 }
    );
  }

  return NextResponse.next();
}

/** Constant-time string comparison (no early exit on mismatch) */
function timingSafeEqual(a: string, b: string): boolean {
  let result = 0;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    result |= (a.charCodeAt(i) ?? 0) ^ (b.charCodeAt(i) ?? 0);
  }
  return result === 0;
}

export const config = {
  matcher: ["/api/:path*"],
};
