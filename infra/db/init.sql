-- ═══════════════════════════════════════════════════════════════════════════
-- APEX Database Schema  —  TimescaleDB + PostgreSQL
-- Safe to re-run: all statements use IF NOT EXISTS / OR REPLACE guards
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── 1. PRICE DATA ─────────────────────────────────────────────────────────

-- Raw OHLCV bars from Alpaca
CREATE TABLE IF NOT EXISTS ohlcv_bars (
  time        TIMESTAMPTZ     NOT NULL,
  symbol      VARCHAR(10)     NOT NULL,
  open        DECIMAL(12,4)   NOT NULL,
  high        DECIMAL(12,4)   NOT NULL,
  low         DECIMAL(12,4)   NOT NULL,
  close       DECIMAL(12,4)   NOT NULL,
  volume      BIGINT          NOT NULL,
  vwap        DECIMAL(12,4),
  trade_count INTEGER,
  source      VARCHAR(20)     DEFAULT 'alpaca',
  PRIMARY KEY (time, symbol)
);

SELECT create_hypertable(
  'ohlcv_bars', 'time',
  if_not_exists => TRUE,
  chunk_time_interval => INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time
  ON ohlcv_bars (symbol, time DESC);

-- ── 2. FEATURE DATA ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS features (
  time          TIMESTAMPTZ   NOT NULL,
  symbol        VARCHAR(10)   NOT NULL,

  -- Price-based features
  returns_1     DECIMAL(10,6),
  returns_5     DECIMAL(10,6),
  returns_15    DECIMAL(10,6),
  returns_60    DECIMAL(10,6),

  -- Technical indicators
  rsi_14        DECIMAL(8,4),
  rsi_28        DECIMAL(8,4),
  ema_20        DECIMAL(12,4),
  ema_50        DECIMAL(12,4),
  ema_200       DECIMAL(12,4),
  macd          DECIMAL(10,6),
  macd_signal   DECIMAL(10,6),
  macd_hist     DECIMAL(10,6),
  bb_upper      DECIMAL(12,4),
  bb_lower      DECIMAL(12,4),
  bb_pct        DECIMAL(8,4),
  atr_14        DECIMAL(12,4),
  stoch_k       DECIMAL(8,4),
  stoch_d       DECIMAL(8,4),

  -- Volume features
  volume_ratio  DECIMAL(8,4),
  vwap_dev      DECIMAL(8,4),

  -- Market regime
  adx_14        DECIMAL(8,4),
  regime        VARCHAR(10),

  PRIMARY KEY (time, symbol)
);

SELECT create_hypertable(
  'features', 'time',
  if_not_exists => TRUE,
  chunk_time_interval => INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_features_symbol_time
  ON features (symbol, time DESC);

-- ── 3. SIGNALS ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
  id            SERIAL,
  time          TIMESTAMPTZ   NOT NULL,
  symbol        VARCHAR(10)   NOT NULL,
  direction     VARCHAR(5)    NOT NULL,
  score         DECIMAL(6,4)  NOT NULL,
  confidence    DECIMAL(6,4)  NOT NULL,
  model_id      VARCHAR(50),
  regime        VARCHAR(10),

  tft_score     DECIMAL(6,4),
  xgb_score     DECIMAL(6,4),
  lstm_score    DECIMAL(6,4),

  tft_weight    DECIMAL(5,3),
  xgb_weight    DECIMAL(5,3),
  lstm_weight   DECIMAL(5,3),

  outcome       VARCHAR(10),
  outcome_pnl   DECIMAL(10,2),
  outcome_at    TIMESTAMPTZ,

  PRIMARY KEY (time, symbol)
);

SELECT create_hypertable(
  'signals', 'time',
  if_not_exists => TRUE,
  chunk_time_interval => INTERVAL '30 days'
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol
  ON signals (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_signals_direction
  ON signals (direction, time DESC);

-- ── 4. ORDERS ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
  id              SERIAL        PRIMARY KEY,
  time            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  alpaca_order_id VARCHAR(100)  UNIQUE,
  symbol          VARCHAR(10)   NOT NULL,
  side            VARCHAR(5)    NOT NULL,
  qty             DECIMAL(12,4) NOT NULL,
  order_type      VARCHAR(10)   NOT NULL,
  limit_price     DECIMAL(12,4),
  filled_price    DECIMAL(12,4),
  filled_qty      DECIMAL(12,4),
  status          VARCHAR(20)   NOT NULL,
  source          VARCHAR(10),
  signal_id       INTEGER,
  model_id        VARCHAR(50),
  created_at      TIMESTAMPTZ   DEFAULT NOW(),
  filled_at       TIMESTAMPTZ,
  cancelled_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_time
  ON orders (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_orders_status
  ON orders (status, time DESC);

CREATE INDEX IF NOT EXISTS idx_orders_alpaca_id
  ON orders (alpaca_order_id);

-- ── 5. POSITIONS ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS positions (
  id              SERIAL        PRIMARY KEY,
  symbol          VARCHAR(10)   NOT NULL UNIQUE,
  side            VARCHAR(5)    NOT NULL,
  qty             DECIMAL(12,4) NOT NULL,
  entry_price     DECIMAL(12,4) NOT NULL,
  current_price   DECIMAL(12,4),
  market_value    DECIMAL(12,2),
  unrealized_pnl  DECIMAL(12,2),
  realized_pnl    DECIMAL(12,2) DEFAULT 0,
  opened_at       TIMESTAMPTZ   DEFAULT NOW(),
  updated_at      TIMESTAMPTZ   DEFAULT NOW(),
  closed_at       TIMESTAMPTZ,
  is_open         BOOLEAN       DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_positions_open
  ON positions (is_open, symbol);

-- ── 6. PORTFOLIO SNAPSHOTS ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  time            TIMESTAMPTZ   NOT NULL,
  portfolio_value DECIMAL(14,2) NOT NULL,
  cash_balance    DECIMAL(14,2) NOT NULL,
  buying_power    DECIMAL(14,2),
  total_pnl       DECIMAL(12,2),
  daily_pnl       DECIMAL(12,2),
  open_positions  INTEGER,
  PRIMARY KEY (time)
);

SELECT create_hypertable(
  'portfolio_snapshots', 'time',
  if_not_exists => TRUE,
  chunk_time_interval => INTERVAL '30 days'
);

-- ── 7. MODEL PERFORMANCE ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS model_performance (
  id              SERIAL        PRIMARY KEY,
  time            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  model_id        VARCHAR(50)   NOT NULL,
  model_type      VARCHAR(20),

  val_sharpe      DECIMAL(8,4),
  val_hit_rate    DECIMAL(6,4),
  val_loss        DECIMAL(10,6),
  val_mae         DECIMAL(10,6),

  live_sharpe     DECIMAL(8,4),
  live_hit_rate   DECIMAL(6,4),
  live_trades     INTEGER,
  live_pnl        DECIMAL(12,2),

  status          VARCHAR(20),
  promoted_at     TIMESTAMPTZ,
  demoted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_model_perf_model
  ON model_performance (model_id, time DESC);

-- ── 8. PRESERVE LEGACY TABLES (backwards-compatibility) ──────────────────
-- These pre-date the v2 schema; kept so existing services don't crash.

CREATE TABLE IF NOT EXISTS market_raw_minute (
  ts      TIMESTAMPTZ NOT NULL,
  symbol  TEXT        NOT NULL,
  open    DOUBLE PRECISION NOT NULL,
  high    DOUBLE PRECISION NOT NULL,
  low     DOUBLE PRECISION NOT NULL,
  close   DOUBLE PRECISION NOT NULL,
  volume  BIGINT      NOT NULL,
  source  TEXT        DEFAULT 'polygon'
);

SELECT create_hypertable('market_raw_minute', 'ts',
  chunk_time_interval => INTERVAL '1 day',
  if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_raw_symbol
  ON market_raw_minute (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS signals_scored (
  ts          TIMESTAMPTZ NOT NULL,
  symbol      TEXT        NOT NULL,
  score       DOUBLE PRECISION NOT NULL,
  direction   SMALLINT    NOT NULL,
  confidence  DOUBLE PRECISION,
  source      TEXT
);

SELECT create_hypertable('signals_scored', 'ts',
  chunk_time_interval => INTERVAL '1 day',
  if_not_exists => TRUE
);

-- ── 9. VIEWS ──────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW latest_prices AS
SELECT DISTINCT ON (symbol)
  symbol,
  time,
  close  AS price,
  volume,
  (close - LAG(close) OVER (
    PARTITION BY symbol ORDER BY time
  )) / NULLIF(LAG(close) OVER (
    PARTITION BY symbol ORDER BY time
  ), 0) * 100 AS change_pct
FROM ohlcv_bars
ORDER BY symbol, time DESC;

CREATE OR REPLACE VIEW open_positions_live AS
SELECT
  p.symbol,
  p.side,
  p.qty,
  p.entry_price,
  lp.price        AS current_price,
  p.qty * lp.price AS market_value,
  (lp.price - p.entry_price) * p.qty
    * CASE WHEN p.side = 'LONG' THEN 1 ELSE -1 END
    AS unrealized_pnl,
  (lp.price - p.entry_price) / p.entry_price * 100
    * CASE WHEN p.side = 'LONG' THEN 1 ELSE -1 END
    AS pnl_pct,
  p.opened_at
FROM positions p
LEFT JOIN latest_prices lp USING (symbol)
WHERE p.is_open = TRUE;

CREATE OR REPLACE VIEW daily_pnl AS
SELECT
  DATE_TRUNC('day', time) AS date,
  SUM(daily_pnl)          AS pnl,
  MAX(portfolio_value)    AS peak_value,
  MIN(portfolio_value)    AS trough_value
FROM portfolio_snapshots
GROUP BY DATE_TRUNC('day', time)
ORDER BY date DESC;

CREATE OR REPLACE VIEW signal_performance AS
SELECT
  model_id,
  direction,
  COUNT(*)                                  AS total,
  COUNT(*) FILTER (WHERE outcome = 'WIN')   AS wins,
  COUNT(*) FILTER (WHERE outcome = 'LOSS')  AS losses,
  ROUND(
    COUNT(*) FILTER (WHERE outcome = 'WIN')::DECIMAL
    / NULLIF(COUNT(*) FILTER (
        WHERE outcome IN ('WIN','LOSS')
      ), 0) * 100,
    2
  )                                         AS hit_rate_pct,
  ROUND(AVG(outcome_pnl), 2)               AS avg_pnl
FROM signals
WHERE outcome IS NOT NULL
GROUP BY model_id, direction
ORDER BY model_id, direction;

-- ── 10. CONTINUOUS AGGREGATES ─────────────────────────────────────────────

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 hour', time) AS bucket,
  symbol,
  FIRST(open,  time) AS open,
  MAX(high)          AS high,
  MIN(low)           AS low,
  LAST(close,  time) AS close,
  SUM(volume)        AS volume
FROM ohlcv_bars
GROUP BY bucket, symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'ohlcv_1h',
  start_offset      => INTERVAL '3 hours',
  end_offset        => INTERVAL '1 minute',
  schedule_interval => INTERVAL '30 minutes',
  if_not_exists     => TRUE
);

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1d
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 day', time) AS bucket,
  symbol,
  FIRST(open,  time) AS open,
  MAX(high)          AS high,
  MIN(low)           AS low,
  LAST(close,  time) AS close,
  SUM(volume)        AS volume
FROM ohlcv_bars
GROUP BY bucket, symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'ohlcv_1d',
  start_offset      => INTERVAL '3 days',
  end_offset        => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour',
  if_not_exists     => TRUE
);

-- ── 11. DATA RETENTION ────────────────────────────────────────────────────

SELECT add_retention_policy(
  'ohlcv_bars',
  INTERVAL '2 years',
  if_not_exists => TRUE
);

SELECT add_retention_policy(
  'features',
  INTERVAL '1 year',
  if_not_exists => TRUE
);

SELECT add_retention_policy(
  'market_raw_minute',
  INTERVAL '90 days',
  if_not_exists => TRUE
);

DO $$ BEGIN
  RAISE NOTICE 'APEX database schema initialized ✓';
END $$;
