#!/usr/bin/env python3
"""
APEX Signal Attribution Report
scripts/signal_attribution_report.py

Queries the `signal_attribution` TimescaleDB table populated by
services/attribution/tracker.py and prints a structured report of
per-signal performance metrics.

For every signal (tft, rsi, ema, macd, stoch, sentiment, xgb, factor):
  - Trade count (number of trades where signal was active)
  - Win rate   (% of trades where signal direction agreed with P&L direction)
  - Avg P&L    (mean realised P&L of trades attributed to this signal)
  - Total P&L  (sum of trade_pnl weighted by contributed_weight)
  - Sharpe contribution (signal's marginal contribution to strategy Sharpe)
  - Avg contributed weight (how much ensemble weight the signal held on average)
  - Alignment rate (% of time signal direction matched final trade direction)

Flags any signal with:
  - Negative average contributed_weight (signal is consistently contra-trend)
  - Win rate below 45% (worse than random)
  - Negative Sharpe contribution

Exit codes
──────────
  0  — report generated; no negative contributors
  1  — report generated; one or more signals flagged negative
  2  — database / query error

Usage
─────
  python scripts/signal_attribution_report.py
  python scripts/signal_attribution_report.py --days 30
  python scripts/signal_attribution_report.py --days 7 --symbol AAPL
  python scripts/signal_attribution_report.py --signal tft rsi
  python scripts/signal_attribution_report.py --json > report.json
  python scripts/signal_attribution_report.py --csv  > report.csv

Environment variables
─────────────────────
  DATABASE_URL             Full PostgreSQL DSN
  TIMESCALEDB_PASSWORD     Used if DATABASE_URL is not set
  POSTGRES_USER            (default: apex)
  POSTGRES_DB              (default: apexdb)
  TIMESCALEDB_HOST         (default: localhost)
  TIMESCALEDB_PORT         (default: 5432)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Optional

# ─── Configuration ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    _user = os.getenv("POSTGRES_USER",          "apex")
    _pw   = os.getenv("TIMESCALEDB_PASSWORD") or os.getenv("POSTGRES_PASSWORD", "")
    _host = os.getenv("TIMESCALEDB_HOST",        "localhost")
    _port = os.getenv("TIMESCALEDB_PORT",        "5432")
    _db   = os.getenv("POSTGRES_DB",             "apexdb")
    DATABASE_URL = f"postgresql://{_user}:{_pw}@{_host}:{_port}/{_db}"

SIGNAL_NAMES: list[str] = ["tft", "rsi", "ema", "macd", "stoch", "sentiment", "xgb", "factor"]

# Thresholds for flagging
MIN_WIN_RATE            = 0.45    # flag if below this
MIN_AVG_CONTRIB_WEIGHT  = 0.0    # flag if negative (consistently contra-trend)
MIN_SHARPE_CONTRIB      = 0.0    # flag if negative


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SignalStats:
    signal_name:           str
    trade_count:           int
    win_count:             int
    win_rate:              float          # 0–1
    avg_pnl:               float          # mean realised P&L per trade
    total_pnl:             float          # sum of pnl across all attributed trades
    weighted_pnl:          float          # sum(contributed_weight * trade_pnl)
    sharpe_contribution:   float          # marginal Sharpe contribution
    avg_contributed_weight: float         # mean ensemble weight
    avg_signal_value:      float          # mean abs(signal_value) — signal strength
    alignment_rate:        float          # fraction of trades where signal aligned
    negative:              bool           # True if this signal is flagged bad

    @property
    def flag(self) -> str:
        if not self.negative:
            return ""
        issues = []
        if self.win_rate < MIN_WIN_RATE:
            issues.append(f"win_rate={self.win_rate:.1%}<{MIN_WIN_RATE:.0%}")
        if self.avg_contributed_weight < MIN_AVG_CONTRIB_WEIGHT:
            issues.append("contra-trend")
        if self.sharpe_contribution < MIN_SHARPE_CONTRIB:
            issues.append("neg-sharpe-contrib")
        return " | ".join(issues) if issues else "flagged"


# ─── Database query ───────────────────────────────────────────────────────────

_STATS_QUERY = """
WITH base AS (
    SELECT
        signal_name,
        trade_pnl,
        contributed_weight,
        signal_value,
        aligned,
        ts
    FROM signal_attribution
    WHERE ts >= %(since)s
      AND (%(symbol)s IS NULL OR symbol = %(symbol)s)
      AND (%(signal_filter)s IS NULL OR signal_name = ANY(%(signal_filter)s))
      -- exclude very stale snapshots (> 20 min) to keep attribution quality high
      AND snapshot_age_seconds <= 1200
),
per_signal AS (
    SELECT
        signal_name,
        COUNT(*)                            AS trade_count,
        SUM(CASE WHEN aligned THEN 1 ELSE 0 END) AS win_count,
        AVG(trade_pnl)                      AS avg_pnl,
        SUM(trade_pnl)                      AS total_pnl,
        SUM(contributed_weight * trade_pnl) AS weighted_pnl,
        AVG(contributed_weight)             AS avg_contributed_weight,
        AVG(ABS(signal_value))              AS avg_signal_strength,
        SUM(CASE WHEN aligned THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) AS alignment_rate,
        -- Sharpe contribution: mean and stddev of per-trade weighted P&L
        AVG(contributed_weight * trade_pnl)             AS wt_pnl_mean,
        STDDEV_SAMP(contributed_weight * trade_pnl)     AS wt_pnl_std
    FROM base
    GROUP BY signal_name
)
SELECT
    signal_name,
    trade_count,
    win_count,
    COALESCE(win_count::float / NULLIF(trade_count, 0), 0) AS win_rate,
    COALESCE(avg_pnl, 0)                  AS avg_pnl,
    COALESCE(total_pnl, 0)               AS total_pnl,
    COALESCE(weighted_pnl, 0)            AS weighted_pnl,
    COALESCE(avg_contributed_weight, 0)  AS avg_contributed_weight,
    COALESCE(avg_signal_strength, 0)     AS avg_signal_strength,
    COALESCE(alignment_rate, 0)          AS alignment_rate,
    -- Sharpe contribution: annualised (sqrt(252) for daily; trades may be intraday)
    CASE
        WHEN COALESCE(wt_pnl_std, 0) > 0
        THEN (wt_pnl_mean / wt_pnl_std) * SQRT(252)
        ELSE 0
    END AS sharpe_contribution
FROM per_signal
ORDER BY sharpe_contribution DESC;
"""

_SUMMARY_QUERY = """
SELECT
    COUNT(DISTINCT order_id)                AS total_trades,
    COUNT(DISTINCT symbol)                  AS total_symbols,
    MIN(ts)                                 AS earliest,
    MAX(ts)                                 AS latest,
    SUM(trade_pnl) / NULLIF(COUNT(DISTINCT order_id), 0) AS avg_trade_pnl,
    SUM(CASE WHEN trade_pnl > 0 THEN 1 ELSE 0 END)::float
        / NULLIF(COUNT(DISTINCT order_id), 0)            AS portfolio_win_rate
FROM signal_attribution
WHERE ts >= %(since)s
  AND (%(symbol)s IS NULL OR symbol = %(symbol)s)
  AND snapshot_age_seconds <= 1200;
"""

_TOP_SYMBOLS_QUERY = """
SELECT
    symbol,
    COUNT(DISTINCT order_id)                       AS trade_count,
    SUM(trade_pnl) / COUNT(DISTINCT order_id)      AS avg_pnl,
    SUM(CASE WHEN trade_pnl > 0 THEN 1 ELSE 0 END)::float
        / NULLIF(COUNT(DISTINCT order_id), 0)      AS win_rate
FROM signal_attribution
WHERE ts >= %(since)s
  AND snapshot_age_seconds <= 1200
GROUP BY symbol
ORDER BY avg_pnl DESC
LIMIT 10;
"""


def _query_db(
    since: datetime,
    symbol: Optional[str],
    signal_filter: Optional[list[str]],
) -> tuple[list[SignalStats], dict, list[dict]]:
    """
    Run all three queries against TimescaleDB.
    Returns (signal_stats, summary_dict, top_symbols).
    """
    import psycopg2
    import psycopg2.extras

    params = {
        "since":         since,
        "symbol":        symbol,
        "signal_filter": signal_filter,   # list or None
    }

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── Per-signal stats ─────────────────────────────────────────
            cur.execute(_STATS_QUERY, params)
            rows = cur.fetchall()

            stats: list[SignalStats] = []
            for r in rows:
                sharpe  = float(r["sharpe_contribution"] or 0.0)
                win_r   = float(r["win_rate"] or 0.0)
                avg_cw  = float(r["avg_contributed_weight"] or 0.0)
                flagged = (
                    win_r   < MIN_WIN_RATE           or
                    avg_cw  < MIN_AVG_CONTRIB_WEIGHT or
                    sharpe  < MIN_SHARPE_CONTRIB
                )
                stats.append(SignalStats(
                    signal_name            = r["signal_name"],
                    trade_count            = int(r["trade_count"] or 0),
                    win_count              = int(r["win_count"]   or 0),
                    win_rate               = win_r,
                    avg_pnl                = float(r["avg_pnl"]   or 0.0),
                    total_pnl              = float(r["total_pnl"] or 0.0),
                    weighted_pnl           = float(r["weighted_pnl"] or 0.0),
                    sharpe_contribution    = sharpe,
                    avg_contributed_weight = avg_cw,
                    avg_signal_value       = float(r["avg_signal_strength"] or 0.0),
                    alignment_rate         = float(r["alignment_rate"] or 0.0),
                    negative               = flagged,
                ))

            # ── Portfolio summary ────────────────────────────────────────
            cur.execute(_SUMMARY_QUERY, params)
            summary_row = cur.fetchone()
            summary = dict(summary_row) if summary_row else {}

            # ── Top symbols ──────────────────────────────────────────────
            cur.execute(_TOP_SYMBOLS_QUERY, params)
            top_symbols = [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()

    return stats, summary, top_symbols


# ─── Formatters ───────────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{x*100:+.1f}%"

def _pct_plain(x: float) -> str:
    return f"{x*100:.1f}%"

def _money(x: float) -> str:
    return f"${x:+.4f}"

def _f3(x: float) -> str:
    return f"{x:+.3f}"

def _flag_icon(neg: bool) -> str:
    return "  ⚠  FLAGGED" if neg else ""


def print_text_report(
    stats:       list[SignalStats],
    summary:     dict,
    top_symbols: list[dict],
    args:        argparse.Namespace,
) -> None:
    W = 100

    print()
    print("═" * W)
    print("  APEX SIGNAL ATTRIBUTION REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  Window:    last {args.days} days"
          + (f"  │  Symbol: {args.symbol}" if args.symbol else "")
          + (f"  │  Signals: {', '.join(args.signal)}" if args.signal else ""))
    print("═" * W)

    # ── Portfolio summary ────────────────────────────────────────────────────
    if summary:
        total_t  = summary.get("total_trades",      0)
        total_s  = summary.get("total_symbols",     0)
        port_wr  = float(summary.get("portfolio_win_rate", 0) or 0)
        avg_tpnl = float(summary.get("avg_trade_pnl",      0) or 0)
        print()
        print("  PORTFOLIO SUMMARY")
        print(f"  Total attributed trades : {total_t}")
        print(f"  Symbols traded          : {total_s}")
        print(f"  Portfolio win rate      : {_pct_plain(port_wr)}")
        print(f"  Avg trade P&L           : {_money(avg_tpnl)}")

    # ── Per-signal table ─────────────────────────────────────────────────────
    print()
    print("  PER-SIGNAL ATTRIBUTION")
    print()

    hdr = (
        f"  {'Signal':<12}"
        f"{'Trades':>8}"
        f"{'Win%':>8}"
        f"{'Avg PnL':>12}"
        f"{'Total PnL':>12}"
        f"{'Align%':>8}"
        f"{'Avg Wt':>8}"
        f"{'Sharpe Contrib':>16}"
        f"  Flag"
    )
    print(hdr)
    print("  " + "─" * (W - 2))

    flagged_signals: list[SignalStats] = []

    for s in stats:
        flag_str = "  ⚠ FLAGGED" if s.negative else ""
        if s.negative:
            flagged_signals.append(s)
        row = (
            f"  {s.signal_name:<12}"
            f"{s.trade_count:>8d}"
            f"{_pct_plain(s.win_rate):>8}"
            f"{_money(s.avg_pnl):>12}"
            f"{_money(s.total_pnl):>12}"
            f"{_pct_plain(s.alignment_rate):>8}"
            f"{s.avg_contributed_weight:>8.3f}"
            f"{s.sharpe_contribution:>16.4f}"
            f"{flag_str}"
        )
        print(row)

    print("  " + "─" * (W - 2))

    # ── Negative contributors ────────────────────────────────────────────────
    if flagged_signals:
        print()
        print("  ⚠  FLAGGED SIGNALS — require investigation or weight reduction")
        print()
        for s in flagged_signals:
            print(f"  {s.signal_name.upper():>12}  {s.flag}")
            if s.win_rate < MIN_WIN_RATE:
                print(f"               Win rate {_pct_plain(s.win_rate)} < {_pct_plain(MIN_WIN_RATE)} threshold")
            if s.avg_contributed_weight < MIN_AVG_CONTRIB_WEIGHT:
                print(f"               Avg contributed weight {s.avg_contributed_weight:.4f} is negative")
                print(f"               → Signal is consistently contra-trend; consider zeroing its weight")
            if s.sharpe_contribution < MIN_SHARPE_CONTRIB:
                print(f"               Sharpe contribution {s.sharpe_contribution:.4f} is negative")
                print(f"               → This signal reduces risk-adjusted returns; reduce or remove")
            print()
        print("  Remediation options:")
        print("  1. Reduce weight in configs/live_trading.yaml model.ensemble_weights")
        print("  2. Add a confidence gate in services/signal_engine/filters.py")
        print("  3. Disable the signal by setting its weight env var to 0")
        print(f"     e.g. ENSEMBLE_WEIGHT_{flagged_signals[0].signal_name.upper()}=0")
    else:
        print()
        print("  ✓  All signals are contributing positively to strategy performance.")

    # ── Top symbols ──────────────────────────────────────────────────────────
    if top_symbols:
        print()
        print("  TOP SYMBOLS BY AVG P&L (last {} days)".format(args.days))
        print()
        print(f"  {'Symbol':<10}  {'Trades':>8}  {'Avg PnL':>12}  {'Win%':>8}")
        print("  " + "─" * 45)
        for row in top_symbols[:5]:
            print(
                f"  {str(row['symbol']):<10}"
                f"  {int(row.get('trade_count', 0)):>8d}"
                f"  {_money(float(row.get('avg_pnl', 0) or 0)):>12}"
                f"  {_pct_plain(float(row.get('win_rate', 0) or 0)):>8}"
            )

    print()
    print("═" * W)

    n_flagged = len(flagged_signals)
    if n_flagged:
        print(f"  VERDICT: {n_flagged} signal(s) flagged — review and reweight before next live session.")
    else:
        print("  VERDICT: All signals healthy.")
    print("═" * W)
    print()


def print_json_report(
    stats:       list[SignalStats],
    summary:     dict,
    top_symbols: list[dict],
    args:        argparse.Namespace,
) -> None:
    out = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "window_days":   args.days,
        "symbol_filter": args.symbol,
        "summary": {
            k: (str(v) if hasattr(v, "isoformat") else v)
            for k, v in summary.items()
        },
        "signals": [
            {
                "signal_name":             s.signal_name,
                "trade_count":             s.trade_count,
                "win_rate":                round(s.win_rate, 4),
                "avg_pnl":                 round(s.avg_pnl, 6),
                "total_pnl":               round(s.total_pnl, 6),
                "weighted_pnl":            round(s.weighted_pnl, 6),
                "sharpe_contribution":     round(s.sharpe_contribution, 4),
                "avg_contributed_weight":  round(s.avg_contributed_weight, 4),
                "avg_signal_value":        round(s.avg_signal_value, 4),
                "alignment_rate":          round(s.alignment_rate, 4),
                "flagged":                 s.negative,
                "flag_reason":             s.flag,
            }
            for s in stats
        ],
        "top_symbols": [
            {
                k: (str(v) if hasattr(v, "isoformat") else v)
                for k, v in row.items()
            }
            for row in top_symbols
        ],
        "flagged_count": sum(1 for s in stats if s.negative),
    }
    print(json.dumps(out, indent=2))


def print_csv_report(
    stats:       list[SignalStats],
    summary:     dict,
    top_symbols: list[dict],
    args:        argparse.Namespace,
) -> None:
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "signal_name", "trade_count", "win_rate", "avg_pnl", "total_pnl",
        "weighted_pnl", "sharpe_contribution", "avg_contributed_weight",
        "avg_signal_value", "alignment_rate", "flagged", "flag_reason",
    ])
    writer.writeheader()
    for s in stats:
        writer.writerow({
            "signal_name":             s.signal_name,
            "trade_count":             s.trade_count,
            "win_rate":                round(s.win_rate, 4),
            "avg_pnl":                 round(s.avg_pnl, 6),
            "total_pnl":               round(s.total_pnl, 6),
            "weighted_pnl":            round(s.weighted_pnl, 6),
            "sharpe_contribution":     round(s.sharpe_contribution, 4),
            "avg_contributed_weight":  round(s.avg_contributed_weight, 4),
            "avg_signal_value":        round(s.avg_signal_value, 4),
            "alignment_rate":          round(s.alignment_rate, 4),
            "flagged":                 s.negative,
            "flag_reason":             s.flag,
        })
    print(buf.getvalue(), end="")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="APEX Signal Attribution Report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--days", type=int, default=14,
        help="Look-back window in days (default: 14)",
    )
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Filter to a single symbol, e.g. --symbol AAPL",
    )
    parser.add_argument(
        "--signal", nargs="+", default=None,
        choices=SIGNAL_NAMES,
        help="Filter to specific signals, e.g. --signal tft rsi",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Output CSV (signals table only)",
    )
    parser.add_argument(
        "--min-trades", type=int, default=5,
        help="Minimum number of trades to report a signal (default: 5)",
    )
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    # Validate DATABASE_URL before connecting
    if not DATABASE_URL or ":@" in DATABASE_URL.replace("://", "___"):
        # "://user:@host" means empty password — warn but proceed for local dev
        pass
    if "change-me" in DATABASE_URL or (
        not os.getenv("DATABASE_URL") and not (
            os.getenv("TIMESCALEDB_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
        )
    ):
        print(
            "ERROR: Database credentials not set. "
            "Set TIMESCALEDB_PASSWORD (or DATABASE_URL) in your .env file.",
            file=sys.stderr,
        )
        return 2

    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        return 2

    try:
        stats, summary, top_symbols = _query_db(
            since         = since,
            symbol        = args.symbol,
            signal_filter = args.signal,
        )
    except Exception as exc:
        print(f"ERROR: Database query failed: {exc}", file=sys.stderr)
        return 2

    # Filter by minimum trade count
    if args.min_trades > 0:
        stats = [s for s in stats if s.trade_count >= args.min_trades]

    if not stats:
        print(
            f"No attribution data found for the last {args.days} days "
            f"(min_trades={args.min_trades}). "
            "Ensure services/attribution/tracker.py is running.",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print_json_report(stats, summary, top_symbols, args)
    elif getattr(args, "csv"):
        print_csv_report(stats, summary, top_symbols, args)
    else:
        print_text_report(stats, summary, top_symbols, args)

    has_negatives = any(s.negative for s in stats)
    return 1 if has_negatives else 0


if __name__ == "__main__":
    sys.exit(main())
