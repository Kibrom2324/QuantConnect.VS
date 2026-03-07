-- ─────────────────────────────────────────────────────────────────────────────
-- APEX Signal Attribution — TimescaleDB Migration
-- infra/db/signal_attribution_migration.sql
--
-- Phase: Signal Attribution Tracking
--
-- Creates the signal_attribution hypertable that records which signals
-- were active on each closed trade, enabling per-signal win rate,
-- avg P&L, and Sharpe contribution analysis.
--
-- Run once to migrate an existing database:
--   docker compose exec timescaledb psql -U apex -d apexdb \
--     -f /docker-entrypoint-initdb.d/signal_attribution_migration.sql
--
-- Or via DATABASE_URL:
--   psql $DATABASE_URL -f infra/db/signal_attribution_migration.sql
--
-- Idempotent: all statements use IF NOT EXISTS guards.
-- ─────────────────────────────────────────────────────────────────────────────

-- Ensure TimescaleDB extension is loaded
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ─── Core attribution table ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_attribution (
    -- Time of trade close (partition key for the hypertable)
    ts                   TIMESTAMPTZ      NOT NULL,

    -- Trade identifiers
    symbol               TEXT             NOT NULL,
    order_id             TEXT             NOT NULL,       -- Alpaca order ID

    -- Signal being attributed
    signal_name          TEXT             NOT NULL,       -- tft | rsi | ema | macd | stoch | sentiment | xgb | factor

    -- Signal state at the moment of trade entry
    signal_value         DOUBLE PRECISION NOT NULL,       -- normalised score (−1 to +1)
    contributed_weight   DOUBLE PRECISION NOT NULL,       -- effective ensemble weight × sign alignment
    signal_direction     SMALLINT         NOT NULL,       -- sign(signal_value): +1 / −1 / 0

    -- Trade outcome
    trade_pnl            DOUBLE PRECISION NOT NULL,       -- realised P&L (USD) for this round-trip
    trade_direction      SMALLINT         NOT NULL,       -- sign(trade_pnl): +1 win / −1 loss / 0

    -- Attribution quality flags
    aligned              BOOLEAN          NOT NULL,       -- signal_direction == trade_direction AND both non-zero
    entry_ts             TIMESTAMPTZ,                     -- position open time (NULL if unknown)
    snapshot_age_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,  -- age of signal snapshot at attribution time

    -- Constraint: one row per (trade close, signal) — prevents duplicates from at-least-once Kafka
    CONSTRAINT signal_attribution_pk PRIMARY KEY (ts, symbol, signal_name, order_id)
);

-- ─── Convert to TimescaleDB hypertable ────────────────────────────────────────

SELECT create_hypertable(
    'signal_attribution',
    'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

-- ─── Indexes ──────────────────────────────────────────────────────────────────

-- Fast lookups by symbol for per-symbol reports
CREATE INDEX IF NOT EXISTS idx_sig_attr_symbol
    ON signal_attribution (symbol, ts DESC);

-- Fast lookups by signal for per-signal reports
CREATE INDEX IF NOT EXISTS idx_sig_attr_signal
    ON signal_attribution (signal_name, ts DESC);

-- Fast lookups by order for trade-level reconciliation
CREATE INDEX IF NOT EXISTS idx_sig_attr_order
    ON signal_attribution (order_id);

-- Compound index for the most common report query pattern
CREATE INDEX IF NOT EXISTS idx_sig_attr_signal_aligned
    ON signal_attribution (signal_name, aligned, ts DESC);

-- ─── Retention policy ─────────────────────────────────────────────────────────
-- Keep 1 year of attribution data — enough for full annual performance review.
-- Adjust via: SELECT alter_job(<job_id>, config => jsonb_set(config, '{drop_after}', '"180 days"'));

SELECT add_retention_policy(
    'signal_attribution',
    INTERVAL '365 days',
    if_not_exists => TRUE
);

-- ─── Validation view ──────────────────────────────────────────────────────────
-- Quick summary view used by signal_attribution_report.py and Grafana.

CREATE OR REPLACE VIEW signal_attribution_summary AS
SELECT
    signal_name,
    COUNT(*)                                                     AS total_records,
    COUNT(DISTINCT order_id)                                     AS distinct_trades,
    COUNT(DISTINCT symbol)                                       AS distinct_symbols,
    ROUND(AVG(trade_pnl)::numeric, 6)                           AS avg_trade_pnl,
    ROUND((SUM(CASE WHEN aligned THEN 1 ELSE 0 END)::float
           / NULLIF(COUNT(*), 0) * 100)::numeric, 2)            AS win_rate_pct,
    ROUND(AVG(contributed_weight)::numeric, 4)                   AS avg_contributed_weight,
    ROUND(AVG(ABS(signal_value))::numeric, 4)                    AS avg_signal_strength,
    MIN(ts)                                                      AS first_record,
    MAX(ts)                                                      AS last_record
FROM signal_attribution
GROUP BY signal_name
ORDER BY avg_contributed_weight DESC;

COMMENT ON TABLE signal_attribution IS
    'Per-signal attribution rows written by services/attribution/tracker.py. '
    'One row per (closed trade, signal). '
    'Query with scripts/signal_attribution_report.py.';

COMMENT ON COLUMN signal_attribution.contributed_weight IS
    'effective_ensemble_weight × sign(signal_value) × sign(trade_pnl). '
    'Positive = signal agreed with outcome. Negative = signal disagreed.';

COMMENT ON COLUMN signal_attribution.snapshot_age_seconds IS
    'Age of the signal snapshot used for attribution at the time of trade close. '
    'Rows where this > 1200 (20 min) are excluded from reports by default.';
