/**
 * tests/fixtures/mock-data.ts
 * ───────────────────────────
 * Shared realistic test data used across all spec files.
 * These match the schema returned by APEX's API routes.
 */

// ─── API Response Shapes ─────────────────────────────────────────────────────

export interface SignalEntry {
  symbol:          string;
  horizon:         string;
  predicted_value: number;  // e.g. 875.42 (predicted price)
  confidence:      number;  // 0.0–1.0
  direction:       "up" | "down" | "flat";
  timestamp:       string;  // ISO 8601
  model_id:        string;
  features_hash?:  string;
}

export interface PredictResponse {
  symbol:          string;
  horizon:         string;
  predicted_value: number;
  confidence:      number;
  direction:       "up" | "down" | "flat";
  timestamp:       string;
  model_id:        string;
}

export interface OrderRecord {
  id:                string;
  symbol:            string;
  side:              "buy" | "sell";
  qty:               number | string;
  filled_qty?:       number | string;
  status:            string;
  filled_avg_price?: number | string | null;
  submitted_at?:     string | null;
  filled_at?:        string | null;
  order_type?:       string;
  source?:           string;
}

// ─── Mock Signal / Prediction Responses ──────────────────────────────────────

export const MOCK_SIGNALS: SignalEntry[] = [
  {
    symbol:          "NVDA",
    horizon:         "next_1h",
    predicted_value: 875.42,
    confidence:      0.7832,
    direction:       "up",
    timestamp:       new Date(Date.now() - 300_000).toISOString(),  // 5 minutes ago
    model_id:        "ENS_v5",
  },
  {
    symbol:          "AAPL",
    horizon:         "next_1d",
    predicted_value: 192.15,
    confidence:      0.6541,
    direction:       "flat",
    timestamp:       new Date(Date.now() - 600_000).toISOString(),
    model_id:        "ENS_v5",
  },
  {
    symbol:          "MSFT",
    horizon:         "next_1w",
    predicted_value: 415.80,
    confidence:      0.5923,
    direction:       "up",
    timestamp:       new Date(Date.now() - 900_000).toISOString(),
    model_id:        "ENS_v5",
  },
];

/** A single fresh prediction for NVDA next_1h */
export const MOCK_PREDICT_NVDA: PredictResponse = {
  symbol:          "NVDA",
  horizon:         "next_1h",
  predicted_value: 875.42,
  confidence:      0.7832,
  direction:       "up",
  timestamp:       new Date().toISOString(),
  model_id:        "ENS_v5",
};

/** A prediction response with suspiciously low confidence (edge case) */
export const MOCK_PREDICT_LOW_CONFIDENCE: PredictResponse = {
  symbol:          "AAPL",
  horizon:         "next_1w",
  predicted_value: 190.00,
  confidence:      0.3100,  // below typical threshold
  direction:       "flat",
  timestamp:       new Date().toISOString(),
  model_id:        "ENS_v5",
};

// ─── Mock Order Records ───────────────────────────────────────────────────────

export const MOCK_ORDERS_OPEN: OrderRecord[] = [
  {
    id:           "ord_open_001",
    symbol:       "NVDA",
    side:         "buy",
    qty:          5,
    filled_qty:   0,
    status:       "pending_new",
    filled_avg_price: null,
    submitted_at: new Date(Date.now() - 60_000).toISOString(),
    order_type:   "market",
    source:       "manual",
  },
];

export const MOCK_ORDERS_FILLED: OrderRecord[] = [
  {
    id:               "ord_fill_001",
    symbol:           "TSLA",
    side:             "sell",
    qty:              2,
    filled_qty:       2,
    status:           "filled",
    filled_avg_price: 248.55,
    submitted_at:     new Date(Date.now() - 3_600_000).toISOString(),
    filled_at:        new Date(Date.now() - 3_598_000).toISOString(),
    order_type:       "market",
    source:           "apex_auto",
  },
  {
    id:               "ord_fill_002",
    symbol:           "AAPL",
    side:             "buy",
    qty:              10,
    filled_qty:       10,
    status:           "filled",
    filled_avg_price: 192.30,
    submitted_at:     new Date(Date.now() - 7_200_000).toISOString(),
    filled_at:        new Date(Date.now() - 7_198_000).toISOString(),
    order_type:       "limit",
    source:           "manual",
  },
];

export const MOCK_ORDERS_ALL = [...MOCK_ORDERS_OPEN, ...MOCK_ORDERS_FILLED];

// ─── Invalid Payload Examples ─────────────────────────────────────────────────

/** Missing required fields */
export const INVALID_PAYLOAD_MISSING_FIELDS = {
  side: "buy",
  // Missing: symbol, qty, account_mode, confirmed
};

/** Symbol with special characters (XSS probe) */
export const INVALID_PAYLOAD_XSS_SYMBOL = {
  symbol:       '<script>alert("xss")</script>',
  side:         "buy",
  qty:          1,
  order_type:   "market",
  account_mode: "paper",
  confirmed:    true,
};

/** Negative quantity */
export const INVALID_PAYLOAD_NEGATIVE_QTY = {
  symbol:       "NVDA",
  side:         "buy",
  qty:          -5,
  order_type:   "market",
  account_mode: "paper",
  confirmed:    true,
};

/** `confirmed: false` (should be rejected) */
export const INVALID_PAYLOAD_NOT_CONFIRMED = {
  symbol:       "NVDA",
  side:         "buy",
  qty:          1,
  order_type:   "market",
  account_mode: "paper",
  confirmed:    false,  // should return 400
};

// ─── Tolerance constants for financial assertions ─────────────────────────────

/**
 * Absolute tolerance for floating-point price comparisons.
 * $0.01 — one cent — is the minimum tick size for most US equities.
 */
export const PRICE_TOLERANCE = 0.01;

/**
 * Absolute tolerance for confidence score comparisons.
 * 0.0001 — 4 decimal places, matching Alpaca/ML model output precision.
 */
export const CONFIDENCE_TOLERANCE = 0.0001;

/**
 * Maximum reasonable price for a single share of any APEX-tracked symbol.
 * Used to gate "is this a valid price?" assertions. Adjust if NVDA > $5000.
 */
export const MAX_REASONABLE_PRICE = 5_000;

/**
 * Minimum confidence threshold below which a model prediction is "suspicious".
 * Not a hard failure — just an annotation in the test report.
 */
export const MIN_CONFIDENCE_THRESHOLD = 0.30;

// ─── Shared valid trade payload ───────────────────────────────────────────────

export const VALID_TRADE_NVDA_BUY = {
  symbol:       "NVDA",
  side:         "buy" as const,
  qty:          1,
  order_type:   "market" as const,
  account_mode: "paper" as const,
  confirmed:    true,
};
