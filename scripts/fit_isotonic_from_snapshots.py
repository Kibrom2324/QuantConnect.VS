#!/usr/bin/env python3
"""
scripts/fit_isotonic_from_snapshots.py

Fits an IsotonicCalibrator from calibration_snapshots histogram data
in TimescaleDB and pushes the pickled model to Redis.

The calibration_snapshots table has binned data:
  (bin_lower, bin_upper, predicted_prob_avg, actual_freq, sample_count)

This script expands bins into synthetic (predicted_prob, actual_outcome) pairs,
fits sklearn.isotonic.IsotonicRegression, and saves via
IsotonicCalibrator.save_to_redis() → Redis key "apex:calibration:curve".

Usage:
    python scripts/fit_isotonic_from_snapshots.py \
        --dsn "postgresql://apex_user:apex_pass@localhost:15432/apex" \
        --redis-host localhost --redis-port 6379
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import redis as _redis

from shared.core.calibrator import IsotonicCalibrator


def fetch_bins(dsn: str) -> list[dict]:
    """Fetch calibration histogram bins from TimescaleDB."""
    import psycopg2

    query = """
        SELECT bin_lower, bin_upper, predicted_prob_avg, actual_freq, sample_count
        FROM calibration_snapshots
        WHERE sample_count > 0
        ORDER BY bin_lower
    """
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def expand_bins(bins: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """
    Expand histogram bins into synthetic (predicted_prob, actual_outcome) pairs.

    For each bin with n=sample_count, predicted_prob_avg=p, actual_freq=f:
      - Generate n predicted probs uniformly in [bin_lower, bin_upper]
        centered on predicted_prob_avg
      - Generate round(n*f) positive outcomes and n-round(n*f) negatives
    """
    all_probs = []
    all_outcomes = []

    for b in bins:
        n = int(b["sample_count"])
        p_avg = float(b["predicted_prob_avg"])
        freq = float(b["actual_freq"])
        lo = float(b["bin_lower"])
        hi = float(b["bin_upper"])

        if n <= 0:
            continue

        # Generate predicted probabilities spread around p_avg within the bin
        rng = np.random.default_rng(42)
        probs = np.clip(rng.normal(loc=p_avg, scale=(hi - lo) / 4, size=n), lo, hi)

        # Generate binary outcomes matching the empirical frequency
        n_pos = int(round(n * freq))
        outcomes = np.array([1] * n_pos + [0] * (n - n_pos))
        rng.shuffle(outcomes)

        all_probs.append(probs)
        all_outcomes.append(outcomes)

    return np.concatenate(all_probs), np.concatenate(all_outcomes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit IsotonicCalibrator from calibration_snapshots and push to Redis"
    )
    parser.add_argument("--dsn", default=os.environ.get(
        "TIMESCALEDB_DSN",
        "postgresql://apex_user:apex_pass@localhost:15432/apex",
    ))
    parser.add_argument("--redis-host", default=os.environ.get("REDIS_HOST", "localhost"))
    parser.add_argument("--redis-port", type=int,
                        default=int(os.environ.get("REDIS_PORT", "6379")))
    parser.add_argument("--redis-key", default="apex:calibration:curve")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fit and report but don't push to Redis")
    args = parser.parse_args()

    print(f"Fetching bins from {args.dsn}...")
    bins = fetch_bins(args.dsn)
    if not bins:
        print("ERROR: No rows in calibration_snapshots — cannot fit calibrator")
        sys.exit(1)

    total_samples = sum(b["sample_count"] for b in bins)
    print(f"Found {len(bins)} bins, {total_samples} total samples")
    for b in bins:
        print(f"  [{b['bin_lower']:.2f}, {b['bin_upper']:.2f})"
              f"  pred_avg={b['predicted_prob_avg']:.3f}"
              f"  actual_freq={b['actual_freq']:.3f}"
              f"  n={b['sample_count']}")

    probs, outcomes = expand_bins(bins)
    print(f"\nExpanded to {len(probs)} synthetic training points")

    # Fit
    cal = IsotonicCalibrator()
    cal.fit(probs, outcomes)
    print("IsotonicCalibrator fitted")

    # Brier scores
    pre_brier = IsotonicCalibrator.brier_score(probs, outcomes)
    calibrated = cal.calibrate_batch(probs)
    post_brier = IsotonicCalibrator.brier_score(calibrated, outcomes)
    print(f"Pre-calibration  Brier: {pre_brier:.6f}")
    print(f"Post-calibration Brier: {post_brier:.6f}")

    # Spot-check calibration mapping
    test_probs = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    print("\nCalibration mapping:")
    for p in test_probs:
        print(f"  raw={p:.2f} → calibrated={cal.calibrate(p):.4f}")

    if args.dry_run:
        print("\n--dry-run: not pushing to Redis")
        return

    # Push to Redis
    r = _redis.Redis(host=args.redis_host, port=args.redis_port, decode_responses=False)
    r.ping()
    cal.save_to_redis(r, key=args.redis_key)

    # Update Brier score
    r_str = _redis.Redis(host=args.redis_host, port=args.redis_port, decode_responses=True)
    r_str.set("apex:feedback:brier_score", str(round(post_brier, 6)))

    # Verify
    raw = r.get(args.redis_key)
    if raw is not None:
        print(f"\n✓ Redis key '{args.redis_key}' exists ({len(raw)} bytes)")
    else:
        print(f"\n✗ Redis key '{args.redis_key}' NOT found — something went wrong")
        sys.exit(1)

    print("Done. Restart signal-engine to load the calibrator.")


if __name__ == "__main__":
    main()
