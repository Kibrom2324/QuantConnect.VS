-- APEX Phase 0: Lineage + Calibration Migration
-- Run: psql -h localhost -p 5432 -U apex -d apexdb -f infra/db/lineage_migration.sql
-- Rollback: see bottom of file

-- ─── decision_records ─────────────────────────────────────────────────────────
-- Stores every trade decision AND every veto with full lineage.
-- NOT a hypertable — event records, not high-frequency time-series.

CREATE TABLE IF NOT EXISTS decision_records (
    decision_id TEXT PRIMARY KEY,
    signal_id TEXT,
    prediction_ids TEXT[],
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    direction INT,
    calibrated_prob FLOAT,
    raw_edge_bps FLOAT,
    net_edge_bps FLOAT,
    ood_score FLOAT,
    disagreement_score FLOAT,
    regime INT,
    model_weights JSONB,
    recommended_size_pct FLOAT,
    action TEXT,
    veto_reason TEXT,
    feature_version TEXT,
    signal_process_version TEXT,
    order_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_decision_records_symbol_ts
    ON decision_records (symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_decision_records_action
    ON decision_records (action, timestamp DESC);

-- ─── calibration_snapshots ────────────────────────────────────────────────────
-- Stores periodic calibration curve snapshots for monitoring & comparison.

CREATE TABLE IF NOT EXISTS calibration_snapshots (
    snapshot_time TIMESTAMPTZ NOT NULL,
    bin_lower FLOAT,
    bin_upper FLOAT,
    predicted_prob_avg FLOAT,
    actual_freq FLOAT,
    sample_count INT,
    brier_score FLOAT
);

CREATE INDEX IF NOT EXISTS idx_calibration_snapshots_time
    ON calibration_snapshots (snapshot_time DESC);


-- ─── ROLLBACK ─────────────────────────────────────────────────────────────────
-- To undo this migration, run:
--   DROP INDEX IF EXISTS idx_calibration_snapshots_time;
--   DROP TABLE IF EXISTS calibration_snapshots;
--   DROP INDEX IF EXISTS idx_decision_records_action;
--   DROP INDEX IF EXISTS idx_decision_records_symbol_ts;
--   DROP TABLE IF EXISTS decision_records;
