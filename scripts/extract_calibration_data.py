#!/usr/bin/env python3
"""
scripts/extract_calibration_data.py — Phase 0 calibration data extraction.

Extracts historical (predicted_probability, actual_outcome) pairs from the
signals database and optionally fits + pushes an IsotonicCalibrator to Redis.

Usage:
    # Extract CSV only
    python scripts/extract_calibration_data.py --output calibration_data.csv

    # Extract + fit + push to Redis
    python scripts/extract_calibration_data.py --output calibration_data.csv --fit --push
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extract_from_db(dsn: str, lookback_days: int) -> list[tuple[float, int]]:
    """
    Query TimescaleDB for (probability, actual_outcome) pairs.
    actual_outcome = 1 if the subsequent bar's return was positive, else 0.
    """
    import psycopg2  # noqa: PLC0415

    query = """
        SELECT s.probability, 
               CASE WHEN o.pnl > 0 THEN 1 ELSE 0 END AS actual_outcome
        FROM signals s
        JOIN order_results o ON s.symbol = o.symbol 
            AND o.ts > s.ts 
            AND o.ts < s.ts + INTERVAL '1 day'
        WHERE s.ts > NOW() - INTERVAL '%s days'
          AND s.probability IS NOT NULL
          AND o.pnl IS NOT NULL
        ORDER BY s.ts
    """

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query, (lookback_days,))
            return [(float(row[0]), int(row[1])) for row in cur.fetchall()]
    finally:
        conn.close()


def write_csv(pairs: list[tuple[float, int]], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["predicted_prob", "actual_outcome"])
        writer.writerows(pairs)
    print(f"Wrote {len(pairs)} rows to {path}")


def fit_and_push(pairs: list[tuple[float, int]], redis_host: str, redis_port: int) -> None:
    import numpy as np  # noqa: PLC0415
    import redis as _redis  # noqa: PLC0415
    from shared.core.calibrator import IsotonicCalibrator  # noqa: PLC0415

    probs = np.array([p for p, _ in pairs])
    outcomes = np.array([o for _, o in pairs])

    cal = IsotonicCalibrator()
    cal.fit(probs, outcomes)

    brier = IsotonicCalibrator.brier_score(probs, outcomes)
    print(f"Pre-calibration Brier score: {brier:.6f}")

    calibrated = cal.calibrate_batch(probs)
    post_brier = IsotonicCalibrator.brier_score(calibrated, outcomes)
    print(f"Post-calibration Brier score: {post_brier:.6f}")

    r = _redis.Redis(host=redis_host, port=redis_port, decode_responses=False)
    cal.save_to_redis(r)
    r.set("apex:feedback:brier_score", str(post_brier))
    print("Calibrator pushed to Redis and Brier score updated")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract calibration data from signals DB")
    parser.add_argument("--output", "-o", default="calibration_data.csv", help="Output CSV path")
    parser.add_argument("--lookback-days", type=int, default=30, help="Days of history to query")
    parser.add_argument("--dsn", default=os.environ.get(
        "TIMESCALEDB_DSN", "postgresql://apex:apex@localhost:5432/apex"
    ), help="TimescaleDB connection string")
    parser.add_argument("--fit", action="store_true", help="Fit IsotonicCalibrator on extracted data")
    parser.add_argument("--push", action="store_true", help="Push fitted calibrator to Redis")
    parser.add_argument("--redis-host", default=os.environ.get("REDIS_HOST", "localhost"))
    parser.add_argument("--redis-port", type=int, default=int(os.environ.get("REDIS_PORT", "16379")))
    args = parser.parse_args()

    print(f"Extracting calibration data (lookback={args.lookback_days}d)...")
    pairs = extract_from_db(args.dsn, args.lookback_days)

    if not pairs:
        print("No calibration data found — check that signals and order_results tables have data")
        sys.exit(1)

    write_csv(pairs, args.output)

    if args.fit or args.push:
        if len(pairs) < 50:
            print(f"WARNING: only {len(pairs)} data points — calibrator may be unreliable")
        fit_and_push(pairs, args.redis_host, args.redis_port)


if __name__ == "__main__":
    main()
