#!/usr/bin/env python3
"""
APEX Indicator Composite Comparison — scripts/indicator_composite_comparison.py

Phase 2: Replay comparison between old indicator voting and new
LightGBM indicator composite.

Reads historical feature vectors, runs both old voting logic and
new composite model, compares:
  - Directional accuracy
  - Brier score
  - Ensemble contribution (Shapley-style, optional)

Usage:
    python scripts/indicator_composite_comparison.py \
        --dsn postgresql://... --model models/indicator_composite.pkl
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indicator Composite vs Voting Comparison")
    parser.add_argument("--dsn", default="postgresql://apex:apex@localhost:5432/apex")
    parser.add_argument("--model", default="models/indicator_composite.pkl",
                        help="Path to trained composite model")
    parser.add_argument("--lookback-days", type=int, default=63)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def old_voting_predict(features: dict) -> float:
    """
    Old voting logic: each indicator casts +1 or -1,
    final score = (votes + n) / (2n) to get [0, 1].
    """
    votes = 0
    n = 0

    # RSI > 50 → bullish
    rsi = features.get("rsi_14", 50.0)
    votes += 1 if rsi > 50 else -1
    n += 1

    # EMA cross (12 > 26 → bullish)
    ema12 = features.get("ema_12", 0.0)
    ema26 = features.get("ema_26", 0.0)
    votes += 1 if ema12 > ema26 else -1
    n += 1

    # MACD histogram > 0 → bullish
    macd_hist = features.get("macd_histogram", 0.0)
    votes += 1 if macd_hist > 0 else -1
    n += 1

    # Stochastic K > 50 → bullish
    stoch_k = features.get("stoch_k", 50.0)
    votes += 1 if stoch_k > 50 else -1
    n += 1

    # SMA cross (50 > 200 → bullish)
    sma50 = features.get("sma_50", 0.0)
    sma200 = features.get("sma_200", 0.0)
    votes += 1 if sma50 > sma200 else -1
    n += 1

    # Volume z-score > 0 → bullish
    vol_z = features.get("volume_zscore_20d", 0.0)
    votes += 1 if vol_z > 0 else -1
    n += 1

    return (votes + n) / (2 * n)


def fetch_comparison_data(conn, lookback_days: int) -> list[dict]:
    """Fetch historical feature vectors with actual outcomes."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            symbol, timestamp,
            rsi_14, ema_12, ema_26, macd_line, macd_signal, macd_histogram,
            stoch_k, stoch_d, sma_50, sma_200,
            bb_upper, bb_lower, bb_width,
            realized_vol_20d, volume_zscore_20d,
            return_1d
        FROM features
        WHERE timestamp >= NOW() - INTERVAL '%s days'
        ORDER BY timestamp
    """, (lookback_days,))

    rows = []
    for row in cur.fetchall():
        features = {
            "rsi_14": row[2], "ema_12": row[3], "ema_26": row[4],
            "macd_line": row[5], "macd_signal": row[6], "macd_histogram": row[7],
            "stoch_k": row[8], "stoch_d": row[9], "sma_50": row[10], "sma_200": row[11],
            "bb_upper": row[12], "bb_lower": row[13], "bb_width": row[14],
            "realized_vol_20d": row[15], "volume_zscore_20d": row[16],
        }
        actual = 1 if (row[17] or 0) > 0 else 0
        rows.append({
            "symbol": row[0],
            "timestamp": row[1],
            "features": features,
            "actual": actual,
        })
    return rows


def compute_metrics(predictions: list[float], actuals: list[int]) -> dict:
    """Compute directional accuracy and Brier score."""
    if not predictions:
        return {"accuracy": 0.0, "brier": 1.0, "count": 0}

    n = len(predictions)
    correct = sum(
        1 for p, a in zip(predictions, actuals)
        if (p > 0.5 and a == 1) or (p <= 0.5 and a == 0)
    )
    accuracy = correct / n

    brier = sum((p - a) ** 2 for p, a in zip(predictions, actuals)) / n

    return {"accuracy": accuracy, "brier": brier, "count": n}


def generate_report(voting_metrics: dict, composite_metrics: dict, lookback: int) -> str:
    lines = []
    lines.append(f"# Indicator Composite vs Voting — Replay Comparison ({lookback} days)")
    lines.append("")

    lines.append("| Metric | Old Voting | New Composite | Delta |")
    lines.append("|--------|-----------|--------------|-------|")

    for metric in ["accuracy", "brier"]:
        v = voting_metrics[metric]
        c = composite_metrics[metric]
        delta = c - v
        sign = "+" if delta > 0 else ""
        better = "better" if (metric == "accuracy" and delta > 0) or (metric == "brier" and delta < 0) else "worse"
        lines.append(f"| {metric.title()} | {v:.4f} | {c:.4f} | {sign}{delta:.4f} ({better}) |")

    lines.append(f"| Sample Count | {voting_metrics['count']} | {composite_metrics['count']} | — |")
    lines.append("")

    acc_diff = composite_metrics["accuracy"] - voting_metrics["accuracy"]
    lines.append("## Verdict")
    lines.append("")
    if acc_diff >= 0.02:
        lines.append(f"**PROMOTE**: Composite beats voting by {acc_diff:.1%} accuracy (>= 2% threshold).")
    elif acc_diff > 0:
        lines.append(f"**HOLD**: Composite is better by {acc_diff:.1%}, but below 2% promotion threshold.")
    else:
        lines.append(f"**REJECT**: Composite is worse by {abs(acc_diff):.1%}. Keep voting.")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed")
        sys.exit(1)

    from models.indicator_composite import IndicatorComposite

    conn = psycopg2.connect(args.dsn)
    data = fetch_comparison_data(conn, args.lookback_days)
    conn.close()

    if not data:
        logger.warning("No data found for comparison")
        sys.exit(0)

    composite = IndicatorComposite(model_path=args.model)
    if not composite.is_fitted:
        logger.error("Composite model not found at %s", args.model)
        sys.exit(1)

    voting_preds = []
    composite_preds = []
    actuals = []

    for row in data:
        vp = old_voting_predict(row["features"])
        cp = composite.predict(row["features"])
        if cp is None:
            continue
        voting_preds.append(vp)
        composite_preds.append(cp)
        actuals.append(row["actual"])

    voting_metrics = compute_metrics(voting_preds, actuals)
    composite_metrics = compute_metrics(composite_preds, actuals)

    report = generate_report(voting_metrics, composite_metrics, args.lookback_days)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(report)
        logger.info("Report written to %s", args.output)
    else:
        print(report)


if __name__ == "__main__":
    main()
