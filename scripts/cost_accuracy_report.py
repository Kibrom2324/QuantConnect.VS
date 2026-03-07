#!/usr/bin/env python3
"""
APEX Cost Accuracy Report — scripts/cost_accuracy_report.py

Phase 1: Compares estimated execution costs against realized costs
from filled orders. Reports rolling |estimated - realized| accuracy.

Usage:
    python scripts/cost_accuracy_report.py --dsn postgresql://... --lookback 30
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="APEX Cost Accuracy Report")
    parser.add_argument("--dsn", default="postgresql://apex:apex@localhost:5432/apex")
    parser.add_argument("--lookback", type=int, default=30, help="Days to look back")
    parser.add_argument("--output", default=None, help="Output file (stdout if not set)")
    return parser.parse_args()


def fetch_cost_data(conn, lookback_days: int) -> list[dict]:
    """Fetch trades with both estimated and realized costs."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            tf.symbol,
            tf.entry_time,
            tf.estimated_cost_bps,
            tf.realized_cost_bps,
            ABS(tf.estimated_cost_bps - tf.realized_cost_bps) AS error_bps,
            tf.regime_at_entry
        FROM trade_feedback tf
        WHERE tf.estimated_cost_bps IS NOT NULL
          AND tf.realized_cost_bps IS NOT NULL
          AND tf.exit_time >= NOW() - INTERVAL '%s days'
        ORDER BY tf.entry_time DESC
    """, (lookback_days,))

    return [
        {
            "symbol": row[0],
            "entry_time": row[1],
            "estimated": row[2],
            "realized": row[3],
            "error": row[4],
            "regime": row[5],
        }
        for row in cur.fetchall()
    ]


def generate_report(data: list[dict], lookback_days: int) -> str:
    lines = []
    lines.append(f"# APEX Cost Accuracy Report — Last {lookback_days} Days")
    lines.append("")

    if not data:
        lines.append("*No trades with both estimated and realized costs found.*")
        return "\n".join(lines)

    errors = [d["error"] for d in data]
    mean_error = sum(errors) / len(errors)
    max_error = max(errors)
    under = sum(1 for d in data if d["estimated"] < d["realized"])
    over = sum(1 for d in data if d["estimated"] > d["realized"])

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Trades with cost data | {len(data)} |")
    lines.append(f"| Mean |estimated - realized| | {mean_error:.1f} bps |")
    lines.append(f"| Max error | {max_error:.1f} bps |")
    lines.append(f"| Under-estimated cost | {under} ({under/len(data):.0%}) |")
    lines.append(f"| Over-estimated cost | {over} ({over/len(data):.0%}) |")
    lines.append(f"| Target (< 50% avg error) | {'PASS' if mean_error < 50 else 'FAIL'} |")
    lines.append("")

    # Per-regime breakdown
    by_regime: dict[int, list[float]] = {}
    for d in data:
        by_regime.setdefault(d["regime"], []).append(d["error"])

    regime_names = {0: "Unknown", 1: "Trending Up", 2: "Trending Down", 3: "Range", 4: "Volatile"}
    lines.append("## By Regime")
    lines.append("")
    lines.append("| Regime | Trades | Mean Error (bps) |")
    lines.append("|--------|--------|------------------|")
    for regime in sorted(by_regime.keys()):
        errs = by_regime[regime]
        name = regime_names.get(regime, f"Regime {regime}")
        lines.append(f"| {name} | {len(errs)} | {sum(errs)/len(errs):.1f} |")
    lines.append("")

    # Per-symbol breakdown (top 10 by error)
    by_symbol: dict[str, list[float]] = {}
    for d in data:
        by_symbol.setdefault(d["symbol"], []).append(d["error"])

    lines.append("## By Symbol (Top 10 by Error)")
    lines.append("")
    lines.append("| Symbol | Trades | Mean Error (bps) |")
    lines.append("|--------|--------|------------------|")
    ranked = sorted(by_symbol.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)
    for sym, errs in ranked[:10]:
        lines.append(f"| {sym} | {len(errs)} | {sum(errs)/len(errs):.1f} |")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed")
        sys.exit(1)

    conn = psycopg2.connect(args.dsn)
    data = fetch_cost_data(conn, args.lookback)
    conn.close()

    report = generate_report(data, args.lookback)

    if args.output:
        from pathlib import Path
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(report)
        logger.info("Report written to %s", args.output)
    else:
        print(report)


if __name__ == "__main__":
    main()
