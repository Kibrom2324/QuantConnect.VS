#!/usr/bin/env python3
"""
APEX Indicator Data Preparation — scripts/prepare_indicator_data.py

Phase 2: Prepares training data for the LightGBM indicator composite.
Uses walk-forward splits: 63-day train, 21-day val, 10-day embargo, 21-day test.

Reads from TimescaleDB signals table, extracts indicator values,
computes interaction terms, and labels with next-day return direction.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare indicator composite training data")
    parser.add_argument("--dsn", default="postgresql://apex:apex@localhost:5432/apex")
    parser.add_argument("--output", default="data/indicator_composite_data.npz")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--train-days", type=int, default=63)
    parser.add_argument("--val-days", type=int, default=21)
    parser.add_argument("--embargo-days", type=int, default=10)
    parser.add_argument("--test-days", type=int, default=21)
    return parser.parse_args()


def create_walk_forward_splits(
    n_samples: int,
    train_days: int = 63,
    val_days: int = 21,
    embargo_days: int = 10,
    test_days: int = 21,
) -> list[dict]:
    """
    Generate walk-forward split indices.

    Returns list of dicts with train_start, train_end, val_start, val_end,
    test_start, test_end indices.
    """
    window = train_days + val_days + embargo_days + test_days
    splits = []
    start = 0

    while start + window <= n_samples:
        split = {
            "train_start": start,
            "train_end": start + train_days,
            "val_start": start + train_days,
            "val_end": start + train_days + val_days,
            "test_start": start + train_days + val_days + embargo_days,
            "test_end": start + train_days + val_days + embargo_days + test_days,
        }
        splits.append(split)
        start += test_days  # slide by test window size

    return splits


def main() -> None:
    args = parse_args()

    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    logger.info("Connecting to %s", args.dsn)
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()

    # Fetch indicator data from features table
    query = """
        SELECT symbol, timestamp,
               rsi_14, ema_12, ema_26, macd_line, macd_signal, macd_histogram,
               stoch_k, stoch_d, sma_50, sma_200, bb_upper, bb_lower, bb_width,
               realized_vol_20d, volume_zscore_20d, return_1d
        FROM features
        WHERE timestamp >= NOW() - INTERVAL '%s days'
        ORDER BY symbol, timestamp
    """
    cur.execute(query, (args.lookback_days,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        logger.error("No data found")
        sys.exit(1)

    logger.info("Fetched %d rows", len(rows))

    # Build feature matrix with interaction terms
    features = []
    labels = []
    for row in rows:
        rsi, ema12, ema26, macd_l, macd_s, macd_h = row[2:8]
        stoch_k, stoch_d, sma50, sma200 = row[8:12]
        bb_u, bb_l, bb_w, vol, vol_z, ret_1d = row[12:18]

        # Interaction terms
        rsi_x_macd = rsi * macd_h
        stoch_x_vol = stoch_k * vol_z
        sma_cross = 1.0 if sma50 > sma200 else -1.0
        sma_cross_x_vol = sma_cross * vol

        features.append([
            rsi, ema12, ema26, macd_l, macd_s, macd_h,
            stoch_k, stoch_d, sma50, sma200, bb_u, bb_l, bb_w,
            vol, vol_z,
            rsi_x_macd, stoch_x_vol, sma_cross_x_vol,
        ])
        # Label: 1 if next-day return positive, 0 otherwise
        labels.append(1 if ret_1d > 0 else 0)

    X = np.array(features, dtype=np.float64)
    y = np.array(labels, dtype=np.int32)

    # Generate walk-forward splits
    splits = create_walk_forward_splits(
        len(X), args.train_days, args.val_days, args.embargo_days, args.test_days,
    )
    logger.info("Generated %d walk-forward splits", len(splits))

    # Save
    from pathlib import Path
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, X=X, y=y, splits=json.dumps(splits))
    logger.info("Saved to %s (X=%s, y=%s)", args.output, X.shape, y.shape)


if __name__ == "__main__":
    main()
