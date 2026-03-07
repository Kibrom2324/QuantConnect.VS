#!/usr/bin/env python3
"""
APEX Daily Intelligence Brief — scripts/daily_intelligence_brief.py

Phase 7: Automated post-close report generator.

Sections:
  1. Today's Calls — predictions, outcomes, PnL
  2. Score Card — trailing 20-day win rate, Sharpe, PnL
  3. Calibration Check — "When we say 70%, we're right X% of the time"
  4. Veto Report — refused trades, what would have happened
  5. Model Weights — which model is strongest now
  6. Regime State — current regime per symbol
  7. Tomorrow's Watchlist — top 5 opportunities with calibrated probabilities

Output: Markdown report suitable for Substack/Beehiiv delivery.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="APEX Daily Intelligence Brief")
    parser.add_argument("--dsn", default="postgresql://apex:apex@localhost:5432/apex")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--output", default=None, help="Output file path (stdout if not set)")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--date", default=None, help="Report date (YYYY-MM-DD, default: today)")
    return parser.parse_args()


def fetch_todays_calls(conn, report_date: date) -> list[dict]:
    """Section 1: Today's predictions and outcomes."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, direction, calibrated_prob, net_edge_bps,
               action, veto_reason, order_id, realized_pnl_bps,
               regime, model_weights
        FROM decision_records
        WHERE DATE(ts) = %s
        ORDER BY ts DESC
    """, (report_date,))

    calls = []
    for row in cur.fetchall():
        calls.append({
            "symbol": row[0],
            "direction": "LONG" if row[1] == 1 else "SHORT",
            "probability": f"{row[2]:.1%}" if row[2] else "N/A",
            "net_edge_bps": f"{row[3]:.1f}" if row[3] else "N/A",
            "action": row[4],
            "veto_reason": row[5] or "",
            "order_id": row[6] or "",
            "pnl_bps": f"{row[7]:.1f}" if row[7] else "pending",
            "regime": row[8],
        })
    return calls


def fetch_scorecard(conn, lookback_days: int) -> dict:
    """Section 2: Trailing performance metrics."""
    cur = conn.cursor()

    # Win rate
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE realized_pnl_bps > 0) AS wins,
            COUNT(*) AS total
        FROM trade_feedback
        WHERE exit_time >= NOW() - INTERVAL '%s days'
    """, (lookback_days,))
    row = cur.fetchone()
    total = row[1] if row else 0
    wins = row[0] if row else 0
    win_rate = wins / total if total > 0 else 0.0

    # Total PnL
    cur.execute("""
        SELECT SUM(realized_pnl_bps), AVG(realized_pnl_bps)
        FROM trade_feedback
        WHERE exit_time >= NOW() - INTERVAL '%s days'
    """, (lookback_days,))
    row = cur.fetchone()
    total_pnl = float(row[0]) if row and row[0] else 0.0
    avg_pnl = float(row[1]) if row and row[1] else 0.0

    # Trade count
    cur.execute("""
        SELECT COUNT(*) FROM decision_records
        WHERE action = 'TRADED'
          AND ts >= NOW() - INTERVAL '%s days'
    """, (lookback_days,))
    trade_count = cur.fetchone()[0]

    return {
        "win_rate": win_rate,
        "wins": wins,
        "total_trades": total,
        "total_pnl_bps": total_pnl,
        "avg_pnl_bps": avg_pnl,
        "trade_count": trade_count,
        "lookback_days": lookback_days,
    }


def fetch_calibration_check(conn) -> list[dict]:
    """Section 3: Calibration reliability bins."""
    cur = conn.cursor()
    cur.execute("""
        SELECT bin_lower, bin_upper, predicted_prob_avg, actual_freq, sample_count
        FROM calibration_snapshots
        WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM calibration_snapshots)
        ORDER BY bin_lower
    """)
    return [
        {
            "bin": f"{row[0]:.0%}-{row[1]:.0%}",
            "predicted": f"{row[2]:.1%}",
            "actual": f"{row[3]:.1%}",
            "samples": row[4],
            "gap": f"{abs(row[2] - row[3]):.1%}",
        }
        for row in cur.fetchall()
    ]


def fetch_veto_report(conn, report_date: date) -> list[dict]:
    """Section 4: Vetoed trades and counterfactuals."""
    cur = conn.cursor()
    cur.execute("""
        SELECT vc.symbol, vc.direction, vc.veto_reason,
               vc.price_at_veto, vc.counterfactual_exit_price,
               vc.counterfactual_pnl_bps, vc.would_have_won
        FROM veto_counterfactuals vc
        WHERE DATE(vc.timestamp) = %s
        ORDER BY ABS(vc.counterfactual_pnl_bps) DESC NULLS LAST
    """, (report_date,))
    return [
        {
            "symbol": row[0],
            "direction": "LONG" if row[1] == 1 else "SHORT",
            "reason": row[2],
            "entry_price": f"${row[3]:.2f}" if row[3] else "N/A",
            "exit_price": f"${row[4]:.2f}" if row[4] else "pending",
            "counterfactual_pnl": f"{row[5]:.1f} bps" if row[5] else "pending",
            "would_have_won": "YES" if row[6] else ("NO" if row[6] is not None else "?"),
        }
        for row in cur.fetchall()
    ]


def fetch_model_weights(redis_client) -> dict[str, float]:
    """Section 5: Current model weights."""
    weights = {}
    for model in ["xgboost", "lstm", "timesfm", "indicator_composite"]:
        try:
            val = redis_client.get(f"apex:model_weight:{model}")
            weights[model] = float(val) if val else 0.25
        except Exception:
            weights[model] = 0.25
    return weights


def fetch_regime_state(redis_client, symbols: list[str]) -> dict[str, str]:
    """Section 6: Current regime per symbol."""
    regime_names = {0: "Unknown", 1: "Trending Up", 2: "Trending Down", 3: "Range", 4: "Volatile"}
    result = {}
    for sym in symbols:
        try:
            val = redis_client.get(f"apex:regime:{sym}")
            regime = int(val) if val else 0
            result[sym] = regime_names.get(regime, "Unknown")
        except Exception:
            result[sym] = "Unknown"
    return result


def fetch_watchlist(conn, redis_client) -> list[dict]:
    """Section 7: Top 5 opportunities for tomorrow."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, calibrated_prob, net_edge_bps, regime
        FROM decision_records
        WHERE ts >= NOW() - INTERVAL '1 day'
          AND action != 'TRADED'
          AND calibrated_prob > 0.52
          AND (net_edge_bps IS NULL OR net_edge_bps > 0)
        ORDER BY calibrated_prob DESC
        LIMIT 5
    """)
    return [
        {
            "symbol": row[0],
            "probability": f"{row[1]:.1%}",
            "net_edge_bps": f"{row[2]:.1f}" if row[2] else "N/A",
            "regime": row[3],
        }
        for row in cur.fetchall()
    ]


def generate_report(
    report_date: date,
    calls: list[dict],
    scorecard: dict,
    calibration: list[dict],
    vetoes: list[dict],
    weights: dict[str, float],
    regimes: dict[str, str],
    watchlist: list[dict],
) -> str:
    """Generate the markdown report."""
    lines = []
    lines.append(f"# APEX Daily Intelligence Brief — {report_date.isoformat()}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1: Today's Calls
    lines.append("## 1. Today's Calls")
    lines.append("")
    if calls:
        lines.append("| Symbol | Direction | Probability | Net Edge | Action | PnL |")
        lines.append("|--------|-----------|-------------|----------|--------|-----|")
        for c in calls:
            lines.append(
                f"| {c['symbol']} | {c['direction']} | {c['probability']} "
                f"| {c['net_edge_bps']} bps | {c['action']} | {c['pnl_bps']} |"
            )
    else:
        lines.append("*No signals generated today.*")
    lines.append("")

    # Section 2: Score Card
    lines.append("## 2. Score Card")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Win Rate ({scorecard['lookback_days']}d) | {scorecard['win_rate']:.1%} ({scorecard['wins']}/{scorecard['total_trades']}) |")
    lines.append(f"| Total PnL | {scorecard['total_pnl_bps']:.0f} bps |")
    lines.append(f"| Avg PnL/Trade | {scorecard['avg_pnl_bps']:.1f} bps |")
    lines.append(f"| Trades Executed | {scorecard['trade_count']} |")
    lines.append("")

    # Section 3: Calibration Check
    lines.append("## 3. Calibration Check")
    lines.append("")
    lines.append("*\"When we say X%, we're right Y% of the time\"*")
    lines.append("")
    if calibration:
        lines.append("| Predicted Range | Predicted | Actual | Gap | Samples |")
        lines.append("|-----------------|-----------|--------|-----|---------|")
        for c in calibration:
            lines.append(f"| {c['bin']} | {c['predicted']} | {c['actual']} | {c['gap']} | {c['samples']} |")
    else:
        lines.append("*Insufficient data for calibration check.*")
    lines.append("")

    # Section 4: Veto Report
    lines.append("## 4. Veto Report")
    lines.append("")
    if vetoes:
        lines.append("| Symbol | Direction | Reason | Entry | Exit | CF PnL | Would Win? |")
        lines.append("|--------|-----------|--------|-------|------|--------|------------|")
        for v in vetoes:
            lines.append(
                f"| {v['symbol']} | {v['direction']} | {v['reason']} "
                f"| {v['entry_price']} | {v['exit_price']} | {v['counterfactual_pnl']} "
                f"| {v['would_have_won']} |"
            )
    else:
        lines.append("*No vetoed trades today.*")
    lines.append("")

    # Section 5: Model Weights
    lines.append("## 5. Model Weights")
    lines.append("")
    lines.append("| Model | Weight |")
    lines.append("|-------|--------|")
    for model, weight in sorted(weights.items(), key=lambda x: -x[1]):
        bar = "█" * int(weight * 20) + "░" * (20 - int(weight * 20))
        lines.append(f"| {model} | {weight:.1%} {bar} |")
    lines.append("")

    # Section 6: Regime State
    lines.append("## 6. Regime State")
    lines.append("")
    lines.append("| Symbol | Regime |")
    lines.append("|--------|--------|")
    for sym, regime in sorted(regimes.items()):
        lines.append(f"| {sym} | {regime} |")
    lines.append("")

    # Section 7: Watchlist
    lines.append("## 7. Tomorrow's Watchlist")
    lines.append("")
    if watchlist:
        lines.append("| # | Symbol | Probability | Net Edge |")
        lines.append("|---|--------|-------------|----------|")
        for i, w in enumerate(watchlist, 1):
            lines.append(f"| {i} | {w['symbol']} | {w['probability']} | {w['net_edge_bps']} bps |")
    else:
        lines.append("*No high-conviction opportunities identified.*")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by APEX Trading System. Past performance is not indicative of future results.*")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info("Generating brief for %s", report_date)

    try:
        import psycopg2
        import redis
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        sys.exit(1)

    conn = psycopg2.connect(args.dsn)
    r = redis.Redis(host=args.redis_host, port=args.redis_port, decode_responses=True)

    # QQQ universe symbols
    qqq_symbols = [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL",
        "META", "TSLA", "AVGO", "COST", "NFLX",
        "AMD", "ADBE", "QCOM", "PEP", "CSCO",
        "INTC", "CMCSA", "TMUS", "AMGN", "INTU",
    ]

    # Fetch all sections
    calls = fetch_todays_calls(conn, report_date)
    scorecard = fetch_scorecard(conn, args.lookback_days)
    calibration = fetch_calibration_check(conn)
    vetoes = fetch_veto_report(conn, report_date)
    weights = fetch_model_weights(r)
    regimes = fetch_regime_state(r, qqq_symbols)
    watchlist = fetch_watchlist(conn, r)

    conn.close()

    # Generate report
    report = generate_report(
        report_date, calls, scorecard, calibration,
        vetoes, weights, regimes, watchlist,
    )

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
