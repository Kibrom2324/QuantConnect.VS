/**
 * tests/fixtures/constants.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * All tuneable test constants in one place.
 * Edit these values without touching individual test files.
 */

// ── Server ────────────────────────────────────────────────────────────────────
export const BASE_URL =
  process.env.BASE_URL ?? "http://localhost:3001";

export const API_BASE =
  process.env.API_BASE ?? `${BASE_URL}/api`;

// ── Timeouts (ms) ─────────────────────────────────────────────────────────────
/** Maximum time to wait for /api/predict to respond (TFT is slow). */
export const PREDICT_TIMEOUT = 10_000;

/** Maximum time to wait for any page to be interactive. */
export const PAGE_LOAD_TIMEOUT = 30_000;

/** Maximum time to wait for a single API call (non-predict). */
export const API_TIMEOUT = 5_000;

// ── Symbols & Horizons ────────────────────────────────────────────────────────
export const SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA"] as const;
export const HORIZONS = ["next_1h", "next_1d", "next_1w"] as const;

// ── Auth (read from env → CI secrets; fallback for local dev only) ────────────
export const TEST_EMAIL =
  process.env.TEST_EMAIL ?? "test@apex.com";

export const TEST_PASSWORD =
  process.env.TEST_PASSWORD ?? "testpass123";

// ── Numeric tolerances ────────────────────────────────────────────────────────
/** Floating-point tolerance for confidence comparisons. */
export const CONFIDENCE_TOLERANCE = 0.0001;

/** Tolerance for price comparisons (±1 cent). */
export const PRICE_TOLERANCE = 0.01;

/** Maximum plausible stock price for sanity checks. */
export const MAX_REASONABLE_PRICE = 5_000;

/** Minimum confidence threshold that signals UI filters allow. */
export const MIN_CONFIDENCE_THRESHOLD = 0.30;

// ── Performance budgets (ms) ──────────────────────────────────────────────────
export const PERF = {
  DASHBOARD_LOAD:  3_000,
  CHARTS_LOAD:     2_000,
  SIGNALS_LOAD:    2_000,
  WALLET_LOAD:     2_000,
  RISK_LOAD:       2_000,
  PREDICT_API:    10_000,
  HEALTH_API:        500,
  ORDERS_API:      2_000,
  ANY_PAGE_MAX:    5_000,
  LCP_GOOD:        2_500,
  CLS_GOOD:          0.1,
} as const;
