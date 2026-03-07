#!/usr/bin/env python3
"""
scripts/replay_harness.py — Phase 0 deterministic signal replay.

Replays historical signal payloads through old (PlattScaler) vs new
(IsotonicCalibrator) calibration paths and produces a comparison report.

Usage:
    # Replay from JSON file of historical payloads
    python scripts/replay_harness.py --input signals.json --output report.json

    # Replay from TimescaleDB
    python scripts/replay_harness.py --from-db --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_signals_from_file(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def load_signals_from_db(dsn: str, limit: int) -> list[dict]:
    import psycopg2  # noqa: PLC0415

    query = """
        SELECT symbol, raw_score, probability, ts
        FROM signals
        WHERE raw_score IS NOT NULL
        ORDER BY ts DESC
        LIMIT %s
    """
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query, (limit,))
            return [
                {
                    "symbol": row[0],
                    "raw_score": float(row[1]),
                    "original_prob": float(row[2]),
                    "ts": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                }
                for row in cur.fetchall()
            ]
    finally:
        conn.close()


def replay(
    signals: list[dict],
    platt_path: str | None,
    redis_host: str,
    redis_port: int,
) -> dict:
    """Replay signals through both calibration paths."""
    from services.signal_engine.main import PlattScaler  # noqa: PLC0415
    from shared.core.calibrator import IsotonicCalibrator  # noqa: PLC0415

    # Load Platt scaler
    platt = PlattScaler()
    if platt_path and os.path.exists(platt_path):
        platt.load(platt_path)
    else:
        print("WARNING: No Platt scaler found — Platt probabilities will be unavailable")
        platt = None

    # Load isotonic calibrator from Redis
    import redis as _redis  # noqa: PLC0415
    r = _redis.Redis(host=redis_host, port=redis_port, decode_responses=False)
    try:
        isotonic = IsotonicCalibrator.load_from_redis(r)
    except Exception as exc:
        print(f"WARNING: Could not load isotonic calibrator: {exc}")
        isotonic = IsotonicCalibrator()  # unfitted passthrough

    results = []
    for sig in signals:
        raw_score = sig["raw_score"]
        symbol = sig.get("symbol", "UNKNOWN")

        platt_prob = None
        if platt is not None:
            try:
                platt_prob = float(platt.predict_proba(np.array([raw_score]))[0])
            except RuntimeError:
                pass

        iso_prob = isotonic.calibrate(raw_score)

        result = {
            "symbol": symbol,
            "raw_score": raw_score,
            "platt_prob": platt_prob,
            "isotonic_prob": iso_prob,
            "delta": abs(platt_prob - iso_prob) if platt_prob is not None else None,
            "ts": sig.get("ts"),
        }
        results.append(result)

    # Summary statistics
    deltas = [r["delta"] for r in results if r["delta"] is not None]
    summary = {
        "total_signals": len(results),
        "signals_with_comparison": len(deltas),
        "mean_delta": float(np.mean(deltas)) if deltas else None,
        "max_delta": float(np.max(deltas)) if deltas else None,
        "median_delta": float(np.median(deltas)) if deltas else None,
        "p95_delta": float(np.percentile(deltas, 95)) if deltas else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return {"summary": summary, "signals": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay signals through old vs new calibration")
    parser.add_argument("--input", "-i", help="JSON file with historical signal payloads")
    parser.add_argument("--from-db", action="store_true", help="Load signals from TimescaleDB")
    parser.add_argument("--limit", type=int, default=1000, help="Max signals to replay from DB")
    parser.add_argument("--output", "-o", default="replay_report.json", help="Output report path")
    parser.add_argument("--dsn", default=os.environ.get(
        "TIMESCALEDB_DSN", "postgresql://apex:apex@localhost:5432/apex"
    ))
    parser.add_argument("--platt-path", default=os.environ.get(
        "PLATT_SCALER_PATH", "configs/models/platt_scaler.json"
    ))
    parser.add_argument("--redis-host", default=os.environ.get("REDIS_HOST", "localhost"))
    parser.add_argument("--redis-port", type=int, default=int(os.environ.get("REDIS_PORT", "16379")))
    args = parser.parse_args()

    if args.from_db:
        print(f"Loading up to {args.limit} signals from DB...")
        signals = load_signals_from_db(args.dsn, args.limit)
    elif args.input:
        print(f"Loading signals from {args.input}...")
        signals = load_signals_from_file(args.input)
    else:
        print("ERROR: Provide --input or --from-db")
        sys.exit(1)

    if not signals:
        print("No signals found to replay")
        sys.exit(1)

    print(f"Replaying {len(signals)} signals...")
    report = replay(signals, args.platt_path, args.redis_host, args.redis_port)

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)

    s = report["summary"]
    print(f"\nReplay complete — {s['total_signals']} signals processed")
    if s["mean_delta"] is not None:
        print(f"  Mean |Platt - Isotonic|:   {s['mean_delta']:.6f}")
        print(f"  Max |Platt - Isotonic|:    {s['max_delta']:.6f}")
        print(f"  P95 |Platt - Isotonic|:    {s['p95_delta']:.6f}")
    print(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
