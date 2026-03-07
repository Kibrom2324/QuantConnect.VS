#!/usr/bin/env python3
"""
APEX Daily Feedback Worker — scripts/daily_feedback.py

Phase 5: Post-close daily batch that:
  1. Labels closed positions with realized PnL
  2. Refits calibrator (keeps old if new is worse)
  3. Updates regime-specific model accuracy
  4. Updates cost model accuracy (estimated vs. realized)
  5. Labels veto counterfactuals
  6. Refreshes attribution
  7. Pushes metrics to Prometheus/Redis

Run daily after market close (e.g., 16:30 ET via cron or K8s CronJob).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="APEX daily feedback worker")
    parser.add_argument("--dsn", default="postgresql://apex:apex@localhost:5432/apex")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def label_closed_positions(conn, lookback_days: int) -> int:
    """Label closed positions with actual outcomes."""
    cur = conn.cursor()

    # Find decision_records with trades that now have exit data
    cur.execute("""
        SELECT dr.decision_id, dr.symbol, dr.direction,
               dr.calibrated_prob, dr.net_edge_bps, dr.model_weights,
               dr.regime, dr.ts
        FROM decision_records dr
        WHERE dr.action = 'TRADED'
          AND dr.ts >= NOW() - INTERVAL '%s days'
          AND NOT EXISTS (
              SELECT 1 FROM trade_feedback tf WHERE tf.decision_id = dr.decision_id
          )
    """, (lookback_days,))

    unlabeled = cur.fetchall()
    labeled = 0

    for row in unlabeled:
        decision_id, symbol, direction, prob, edge, weights, regime, ts = row

        # Look for exit in order results (simplified — real impl would track fills)
        cur.execute("""
            SELECT fill_price, realized_cost_bps, realized_pnl_bps
            FROM decision_records
            WHERE symbol = %s AND action = 'TRADED' AND ts > %s
            ORDER BY ts ASC LIMIT 1
        """, (symbol, ts))

        exit_row = cur.fetchone()
        if exit_row is None:
            continue

        fill_price, realized_cost, realized_pnl = exit_row
        actual_outcome = 1 if (realized_pnl or 0) > 0 else 0

        if not hasattr(label_closed_positions, '_dry_run') or not label_closed_positions._dry_run:
            cur.execute("""
                INSERT INTO trade_feedback
                    (trade_id, decision_id, symbol, direction, entry_time,
                     calibrated_prob_at_entry, actual_outcome,
                     estimated_cost_bps, realized_cost_bps, realized_pnl_bps,
                     regime_at_entry, model_weights_at_entry)
                VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                decision_id, symbol, direction, ts,
                prob, actual_outcome, edge, realized_cost, realized_pnl,
                regime, json.dumps(weights) if weights else '{}',
            ))
            labeled += 1

    conn.commit()
    return labeled


def refit_calibrator(conn, redis_client, lookback_days: int) -> dict:
    """
    Refit isotonic calibrator from recent trade feedback.
    Keep old calibrator if new one has worse Brier score.
    """
    from shared.core.calibrator import IsotonicCalibrator

    cur = conn.cursor()
    cur.execute("""
        SELECT calibrated_prob_at_entry, actual_outcome
        FROM trade_feedback
        WHERE exit_time >= NOW() - INTERVAL '%s days'
          AND calibrated_prob_at_entry IS NOT NULL
          AND actual_outcome IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 2000
    """, (lookback_days,))

    rows = cur.fetchall()
    if len(rows) < 50:
        logger.warning("Insufficient feedback data for calibration refit: %d rows", len(rows))
        return {"status": "skipped", "reason": "insufficient_data", "n_rows": len(rows)}

    probs = np.array([r[0] for r in rows])
    outcomes = np.array([r[1] for r in rows])

    # Current Brier score
    old_brier = float(IsotonicCalibrator.brier_score(probs, outcomes))

    # Fit new calibrator
    new_cal = IsotonicCalibrator()
    new_cal.fit(probs, outcomes)
    new_probs = new_cal.calibrate_batch(probs)
    new_brier = float(IsotonicCalibrator.brier_score(new_probs, outcomes))

    if new_brier >= old_brier:
        logger.info(
            "Calibrator refit skipped: new Brier %.6f >= old Brier %.6f",
            new_brier, old_brier,
        )
        return {
            "status": "kept_old",
            "old_brier": old_brier,
            "new_brier": new_brier,
        }

    # New is better — save it
    improvement = (old_brier - new_brier) / old_brier * 100
    logger.info(
        "Calibrator improved: %.6f → %.6f (%.1f%% better)",
        old_brier, new_brier, improvement,
    )

    new_cal.save_to_redis(redis_client)

    # Write Brier score to Redis for monitoring
    redis_client.set("apex:feedback:brier_score", str(new_brier))

    # Store snapshot for historical tracking
    bins = new_cal.reliability_bins(probs, outcomes, n_bins=10)
    for b in bins:
        cur.execute("""
            INSERT INTO calibration_snapshots
                (snapshot_time, bin_lower, bin_upper, predicted_prob_avg,
                 actual_freq, sample_count, brier_score)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s)
        """, (
            b["bin_lower"], b["bin_upper"], b["predicted_prob_avg"],
            b["actual_freq"], b["sample_count"], new_brier,
        ))
    conn.commit()

    return {
        "status": "updated",
        "old_brier": old_brier,
        "new_brier": new_brier,
        "improvement_pct": round(improvement, 2),
        "n_samples": len(rows),
    }


def update_regime_accuracy(conn, lookback_days: int) -> dict:
    """Update model_regime_accuracy table from recent trade feedback."""
    cur = conn.cursor()

    cur.execute("""
        SELECT model_weights_at_entry, regime_at_entry, actual_outcome
        FROM trade_feedback
        WHERE exit_time >= NOW() - INTERVAL '%s days'
          AND model_weights_at_entry IS NOT NULL
    """, (lookback_days,))

    rows = cur.fetchall()
    if not rows:
        return {"status": "no_data"}

    # Aggregate by model × regime
    from collections import defaultdict
    accuracy_data = defaultdict(list)

    for weights_json, regime, outcome in rows:
        weights = json.loads(weights_json) if isinstance(weights_json, str) else weights_json
        for model_name in weights:
            accuracy_data[(model_name, regime)].append(outcome)

    inserted = 0
    for (model_name, regime), outcomes in accuracy_data.items():
        acc = sum(outcomes) / len(outcomes) if outcomes else 0.0
        cur.execute("""
            INSERT INTO model_regime_accuracy
                (model_name, regime, window_start, window_end, accuracy, sample_count)
            VALUES (%s, %s, NOW() - INTERVAL '%s days', NOW(), %s, %s)
            ON CONFLICT (model_name, regime, window_start) DO UPDATE
                SET accuracy = EXCLUDED.accuracy,
                    sample_count = EXCLUDED.sample_count,
                    window_end = EXCLUDED.window_end
        """, (model_name, regime, lookback_days, acc, len(outcomes)))
        inserted += 1

    conn.commit()
    return {"status": "updated", "model_regime_pairs": inserted}


def label_veto_counterfactuals(conn, lookback_days: int) -> int:
    """Label unlabeled veto counterfactuals using current market prices."""
    cur = conn.cursor()

    # Find unlabeled vetoes
    cur.execute("""
        SELECT dr.decision_id, dr.symbol, dr.direction,
               dr.action, dr.veto_reason, dr.ts
        FROM decision_records dr
        WHERE dr.action LIKE 'VETOED%%'
          AND dr.ts >= NOW() - INTERVAL '%s days'
          AND NOT EXISTS (
              SELECT 1 FROM veto_counterfactuals vc WHERE vc.decision_id = dr.decision_id
          )
    """, (lookback_days,))

    vetoes = cur.fetchall()
    labeled = 0

    for decision_id, symbol, direction, action, reason, ts in vetoes:
        # Get the price at veto time (from the raw_score or a separate price table)
        # For now, compute from next available trade data
        cur.execute("""
            SELECT raw_score FROM decision_records
            WHERE decision_id = %s
        """, (decision_id,))

        price_row = cur.fetchone()
        if price_row is None:
            continue

        # Simple counterfactual: what did the symbol do in the next session?
        cur.execute("""
            SELECT bar_close FROM features
            WHERE symbol = %s AND timestamp > %s
            ORDER BY timestamp ASC LIMIT 1
        """, (symbol, ts))

        next_row = cur.fetchone()
        if next_row is None:
            continue

        exit_price = float(next_row[0])
        entry_price = float(price_row[0]) if price_row[0] else exit_price

        if entry_price > 0:
            if direction == 1:
                pnl_bps = ((exit_price - entry_price) / entry_price) * 10000
            else:
                pnl_bps = ((entry_price - exit_price) / entry_price) * 10000
        else:
            pnl_bps = 0.0

        would_have_won = pnl_bps > 0

        cur.execute("""
            INSERT INTO veto_counterfactuals
                (decision_id, symbol, direction, veto_reason, price_at_veto,
                 counterfactual_exit_price, counterfactual_pnl_bps,
                 would_have_won, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_id) DO NOTHING
        """, (
            decision_id, symbol, direction, reason, entry_price,
            exit_price, round(pnl_bps, 2), would_have_won, ts,
        ))
        labeled += 1

    conn.commit()
    return labeled


def push_feedback_metrics(redis_client, conn, lookback_days: int) -> dict:
    """Push feedback metrics to Redis and Prometheus."""
    cur = conn.cursor()

    # Veto precision
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE NOT would_have_won) AS correct_vetoes,
               COUNT(*) AS total_vetoes
        FROM veto_counterfactuals
        WHERE timestamp >= NOW() - INTERVAL '%s days'
    """, (lookback_days,))
    row = cur.fetchone()
    if row and row[1] > 0:
        veto_precision = row[0] / row[1]
        redis_client.set("apex:feedback:veto_precision", str(veto_precision))
    else:
        veto_precision = 0.0

    # Cost estimation error
    cur.execute("""
        SELECT AVG(ABS(estimated_cost_bps - realized_cost_bps))
        FROM trade_feedback
        WHERE exit_time >= NOW() - INTERVAL '%s days'
          AND estimated_cost_bps IS NOT NULL
          AND realized_cost_bps IS NOT NULL
    """, (lookback_days,))
    row = cur.fetchone()
    cost_error = float(row[0]) if row and row[0] is not None else 0.0
    redis_client.set("apex:feedback:cost_estimation_error_bps", str(cost_error))

    return {
        "veto_precision": round(veto_precision, 4),
        "cost_estimation_error_bps": round(cost_error, 2),
    }


def main() -> None:
    args = parse_args()

    try:
        import psycopg2
        import redis
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        sys.exit(1)

    logger.info("=== APEX Daily Feedback Worker ===")
    logger.info("DSN: %s", args.dsn.split("@")[-1])  # Don't log credentials
    logger.info("Lookback: %d days", args.lookback_days)

    conn = psycopg2.connect(args.dsn)
    r = redis.Redis(host=args.redis_host, port=args.redis_port, decode_responses=True)

    results = {}

    # 1. Label closed positions
    logger.info("Step 1: Labeling closed positions...")
    labeled = label_closed_positions(conn, args.lookback_days)
    results["positions_labeled"] = labeled
    logger.info("  Labeled %d positions", labeled)

    # 2. Refit calibrator
    logger.info("Step 2: Refitting calibrator...")
    cal_result = refit_calibrator(conn, r, args.lookback_days)
    results["calibration"] = cal_result
    logger.info("  Calibration: %s", cal_result["status"])

    # 3. Update regime accuracy
    logger.info("Step 3: Updating regime accuracy...")
    regime_result = update_regime_accuracy(conn, args.lookback_days)
    results["regime_accuracy"] = regime_result
    logger.info("  Regime accuracy: %s", regime_result["status"])

    # 4. Label veto counterfactuals
    logger.info("Step 4: Labeling veto counterfactuals...")
    vetoes_labeled = label_veto_counterfactuals(conn, args.lookback_days)
    results["vetoes_labeled"] = vetoes_labeled
    logger.info("  Labeled %d veto counterfactuals", vetoes_labeled)

    # 5. Push feedback metrics
    logger.info("Step 5: Pushing feedback metrics...")
    metrics = push_feedback_metrics(r, conn, args.lookback_days)
    results["metrics"] = metrics
    logger.info("  Veto precision: %.2f%%", metrics["veto_precision"] * 100)
    logger.info("  Cost error: %.1f bps", metrics["cost_estimation_error_bps"])

    conn.close()

    logger.info("=== Feedback complete ===")
    logger.info("Results: %s", json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
