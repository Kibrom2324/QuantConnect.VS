-- APEX Feedback Migration — infra/db/feedback_migration.sql
-- Phase 5: Tables for feedback loop, trade labeling, and counterfactuals
--
-- Run AFTER lineage_migration.sql
--
-- Rollback: DROP TABLE veto_counterfactuals, trade_feedback,
--           model_regime_accuracy, calibration_snapshots CASCADE;

-- ── trade_feedback ──────────────────────────────────────────────────────────
-- Labeled closed positions with realized P&L and cost accuracy data.

CREATE TABLE IF NOT EXISTS trade_feedback (
    trade_id               TEXT PRIMARY KEY,
    decision_id            TEXT REFERENCES decision_records(decision_id),
    symbol                 TEXT NOT NULL,
    direction              INT,
    entry_time             TIMESTAMPTZ,
    exit_time              TIMESTAMPTZ,
    entry_price            FLOAT,
    exit_price             FLOAT,
    realized_pnl_bps       FLOAT,
    calibrated_prob_at_entry FLOAT,
    actual_outcome         INT,           -- 1=win, 0=loss
    estimated_cost_bps     FLOAT,
    realized_cost_bps      FLOAT,
    regime_at_entry        INT,
    model_weights_at_entry JSONB
);

CREATE INDEX IF NOT EXISTS idx_trade_feedback_symbol_ts
    ON trade_feedback (symbol, exit_time DESC);

-- ── veto_counterfactuals ────────────────────────────────────────────────────
-- What would have happened if vetoed trades had been executed.

CREATE TABLE IF NOT EXISTS veto_counterfactuals (
    decision_id              TEXT REFERENCES decision_records(decision_id),
    symbol                   TEXT,
    direction                INT,
    veto_reason              TEXT,
    price_at_veto            FLOAT,
    counterfactual_exit_price FLOAT,
    counterfactual_pnl_bps   FLOAT,
    would_have_won           BOOLEAN,
    timestamp                TIMESTAMPTZ,
    PRIMARY KEY (decision_id)
);

CREATE INDEX IF NOT EXISTS idx_veto_cf_ts
    ON veto_counterfactuals (timestamp DESC);

-- ── model_regime_accuracy ───────────────────────────────────────────────────
-- Per-model per-regime rolling accuracy windows for adaptive combiner.

CREATE TABLE IF NOT EXISTS model_regime_accuracy (
    model_name    TEXT,
    regime        INT,
    window_start  TIMESTAMPTZ,
    window_end    TIMESTAMPTZ,
    accuracy      FLOAT,
    sample_count  INT,
    PRIMARY KEY (model_name, regime, window_start)
);

-- Done.
