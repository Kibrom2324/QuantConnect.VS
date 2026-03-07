#!/usr/bin/env python3
"""
APEX Paper Trading Daily Monitor
scripts/paper_trading_monitor.py

Run daily (e.g. via cron at 17:00 ET) to produce a structured report of
everything that happened in the paper portfolio during the last 24 h.

Outputs
───────
  logs/paper_trading/YYYY-MM-DD.json    — machine-readable daily report
  stdout                                 — human-readable summary

Exit codes
──────────
  0   — healthy (no limits breached)
  1   — daily loss limit breached
  2   — data unavailable / Alpaca connection error

Usage
─────
  python scripts/paper_trading_monitor.py
  python scripts/paper_trading_monitor.py --date 2026-02-27
  python scripts/paper_trading_monitor.py --alert-only
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
import yaml

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(levelname)s  %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("paper_monitor")

# ─── Config & env ─────────────────────────────────────────────────────────────

WORKSPACE = Path(__file__).resolve().parent.parent
CONFIG_PATH = WORKSPACE / "configs" / "paper_trading.yaml"
REPORT_DIR  = WORKSPACE / "logs" / "paper_trading"

ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_KEY  = os.getenv("ALPACA_API_KEY",  "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Baseline ensemble weights from paper_trading.yaml (fallback if env missing)
BASELINE_WEIGHTS: dict[str, float] = {"tft": 0.40, "xgboost": 0.35, "factor": 0.25}
WEIGHT_DRIFT_ALERT = 0.15   # 15 %


# ─── Alpaca helpers ───────────────────────────────────────────────────────────

def _alpaca_headers() -> dict[str, str]:
    if not ALPACA_KEY or not ALPACA_SECRET:
        log.warning("ALPACA_API_KEY / ALPACA_SECRET_KEY not set — Alpaca calls will fail")
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


def get_account() -> dict[str, Any]:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{ALPACA_BASE}/v2/account", headers=_alpaca_headers())
        r.raise_for_status()
        return r.json()


def get_activities(date_str: str) -> list[dict]:
    """Return all FILL activities for the given date (YYYY-MM-DD)."""
    after  = f"{date_str}T00:00:00Z"
    before = f"{date_str}T23:59:59Z"
    url = (
        f"{ALPACA_BASE}/v2/account/activities/FILL"
        f"?after={after}&until={before}&page_size=500"
    )
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers=_alpaca_headers())
        r.raise_for_status()
        return r.json()


def get_orders(date_str: str) -> list[dict]:
    """Return all filled orders for the given date."""
    after  = f"{date_str}T00:00:00Z"
    before = f"{date_str}T23:59:59Z"
    url = (
        f"{ALPACA_BASE}/v2/orders"
        f"?status=filled&after={after}&until={before}&limit=500&direction=asc"
    )
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers=_alpaca_headers())
        r.raise_for_status()
        return r.json()


def get_portfolio_history(date_str: str) -> dict:
    """Return intraday equity curve for the given date."""
    url = (
        f"{ALPACA_BASE}/v2/account/portfolio/history"
        f"?period=1D&timeframe=5Min&date_end={date_str}"
    )
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers=_alpaca_headers())
        r.raise_for_status()
        return r.json()


# ─── Redis helpers ────────────────────────────────────────────────────────────

def get_kill_switch_status() -> str:
    try:
        rc = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=3, decode_responses=True)
        val = rc.get("apex:kill_switch")
        return "ACTIVE" if val == "1" else "INACTIVE"
    except Exception as exc:
        return f"UNKNOWN ({exc})"


def get_ensemble_weights_from_redis() -> dict[str, float] | None:
    """Read live ensemble weights stored by the signal engine (if available)."""
    try:
        rc = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=3, decode_responses=True)
        raw = rc.get("apex:paper:ensemble_weights")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


# ─── Trade analysis ───────────────────────────────────────────────────────────

def pair_trades(orders: list[dict]) -> list[dict]:
    """
    Naively pair buy→sell orders per symbol in chronological order.
    Returns a list of round-trip dicts with realized P&L and holding time.
    """
    open_stack: dict[str, list[dict]] = {}
    round_trips: list[dict] = []

    for o in sorted(orders, key=lambda x: x.get("filled_at") or x.get("created_at") or ""):
        sym  = o.get("symbol", "?")
        side = o.get("side", "")
        qty  = float(o.get("filled_qty") or 0)
        price = float(o.get("filled_avg_price") or 0)
        ts    = o.get("filled_at") or o.get("created_at") or ""

        if side == "buy":
            open_stack.setdefault(sym, []).append(
                {"qty": qty, "price": price, "ts": ts}
            )
        elif side == "sell":
            stack = open_stack.get(sym, [])
            remaining_sell = qty
            while remaining_sell > 0 and stack:
                entry = stack[0]
                matched = min(remaining_sell, entry["qty"])
                pnl = (price - entry["price"]) * matched
                hold_s = 0
                try:
                    t_in  = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
                    t_out = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hold_s = (t_out - t_in).total_seconds()
                except Exception:
                    pass
                round_trips.append({
                    "symbol":          sym,
                    "qty":             matched,
                    "entry_price":     entry["price"],
                    "exit_price":      price,
                    "realized_pnl":    round(pnl, 4),
                    "holding_seconds": int(hold_s),
                    "win":             pnl > 0,
                    "entry_ts":        entry["ts"],
                    "exit_ts":         ts,
                })
                entry["qty"] -= matched
                remaining_sell -= matched
                if entry["qty"] <= 0:
                    stack.pop(0)

    return round_trips


def max_intraday_drawdown(history: dict) -> tuple[float, float]:
    """
    Return (max_drawdown_pct, peak_equity) from portfolio history.
    """
    equities = [float(e) for e in (history.get("equity") or []) if e is not None]
    if not equities:
        return 0.0, 0.0
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)
    return round(max_dd, 6), round(peak, 2)


def check_weight_drift(live: dict[str, float] | None) -> dict:
    if live is None:
        return {"status": "unavailable", "drifts": {}}
    drifts: dict[str, float] = {}
    alert = False
    for model, baseline in BASELINE_WEIGHTS.items():
        live_w = live.get(model, baseline)
        drift  = abs(live_w - baseline) / max(baseline, 1e-9)
        drifts[model] = round(drift, 4)
        if drift > WEIGHT_DRIFT_ALERT:
            alert = True
    return {"status": "ALERT" if alert else "OK", "drifts": drifts, "live_weights": live}


# ─── Report builder ───────────────────────────────────────────────────────────

def build_report(target_date: str, alert_only: bool) -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Alpaca data ──────────────────────────────────────────────────────────
    try:
        account = get_account()
        orders  = get_orders(target_date)
        history = get_portfolio_history(target_date)
    except httpx.ConnectError as exc:
        log.error("Cannot connect to Alpaca: %s", exc)
        sys.exit(2)
    except httpx.HTTPStatusError as exc:
        log.error("Alpaca HTTP error: %s", exc)
        sys.exit(2)

    equity      = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity ", account.get("last_equity", equity)))
    pnl_today   = round(equity - last_equity, 4)
    pnl_pct     = round(pnl_today / last_equity, 6) if last_equity else 0.0

    # ── Daily loss limit check ────────────────────────────────────────────────
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    limit_pct = float(
        (cfg.get("risk") or {}).get("daily_loss", {}).get("limit_pct", 0.03)
    )
    alert_pct = float(
        (cfg.get("risk") or {}).get("daily_loss", {}).get("alert_pct", 0.02)
    )

    loss_breach  = pnl_pct < -limit_pct
    loss_warning = pnl_pct < -alert_pct

    # ── Trade stats ───────────────────────────────────────────────────────────
    trips    = pair_trades(orders)
    n_trades = len(trips)
    n_wins   = sum(1 for t in trips if t["win"])
    win_rate = round(n_wins / n_trades, 4) if n_trades else None
    total_pnl   = round(sum(t["realized_pnl"] for t in trips), 4)
    avg_hold_s  = (
        round(sum(t["holding_seconds"] for t in trips) / n_trades, 1)
        if n_trades else None
    )
    avg_hold_m  = round(avg_hold_s / 60, 1) if avg_hold_s is not None else None

    # ── Drawdown ──────────────────────────────────────────────────────────────
    max_dd_pct, peak_equity = max_intraday_drawdown(history)

    # ── Kill switch ───────────────────────────────────────────────────────────
    ks_status = get_kill_switch_status()

    # ── Ensemble weight drift ─────────────────────────────────────────────────
    live_weights = get_ensemble_weights_from_redis()
    weight_drift = check_weight_drift(live_weights)

    # ── Assemble report ───────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "date":            target_date,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "environment":     "paper",
        "account": {
            "equity":          equity,
            "pnl_today":       pnl_today,
            "pnl_today_pct":   pnl_pct,
        },
        "limits": {
            "daily_loss_limit_pct": limit_pct,
            "daily_loss_alert_pct": alert_pct,
            "breached":             loss_breach,
            "warning":              loss_warning,
        },
        "trades": {
            "total_orders_filled": len(orders),
            "round_trips":         n_trades,
            "wins":                n_wins,
            "win_rate":            win_rate,
            "realized_pnl":        total_pnl,
            "avg_holding_minutes": avg_hold_m,
            "details":             trips,
        },
        "drawdown": {
            "max_intraday_pct":    max_dd_pct,
            "peak_equity":         peak_equity,
        },
        "kill_switch":     ks_status,
        "ensemble_weights": weight_drift,
        "alerts":          [],
    }

    # ── Populate alerts list ───────────────────────────────────────────────────
    if loss_breach:
        report["alerts"].append({
            "severity": "CRITICAL",
            "code":     "DAILY_LOSS_LIMIT_BREACHED",
            "message":  f"P&L {pnl_pct:.2%} exceeded daily loss limit of {limit_pct:.2%}",
        })
    elif loss_warning:
        report["alerts"].append({
            "severity": "WARNING",
            "code":     "DAILY_LOSS_ALERT",
            "message":  f"P&L {pnl_pct:.2%} exceeded warning threshold {alert_pct:.2%}",
        })
    if ks_status == "ACTIVE":
        report["alerts"].append({
            "severity": "WARNING",
            "code":     "KILL_SWITCH_ACTIVE",
            "message":  "Kill switch is ACTIVE — no new trades are being placed",
        })
    if weight_drift["status"] == "ALERT":
        drifts_str = ", ".join(
            f"{k}={v:.0%}" for k, v in weight_drift["drifts"].items() if v > WEIGHT_DRIFT_ALERT
        )
        report["alerts"].append({
            "severity": "WARNING",
            "code":     "ENSEMBLE_WEIGHT_DRIFT",
            "message":  f"Ensemble weight drift exceeds 15% threshold: {drifts_str}",
        })
    if max_dd_pct > cfg.get("risk", {}).get("drawdown", {}).get("max_drawdown_pct", 0.06):
        report["alerts"].append({
            "severity": "CRITICAL",
            "code":     "INTRADAY_DRAWDOWN_BREACH",
            "message":  f"Intraday drawdown {max_dd_pct:.2%} exceeded limit",
        })

    # ── Save JSON report ──────────────────────────────────────────────────────
    out_path = REPORT_DIR / f"{target_date}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Report saved → %s", out_path)

    return report


# ─── Human-readable summary ───────────────────────────────────────────────────

GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[0;33m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def print_summary(report: dict) -> None:
    date_str = report["date"]
    acc   = report["account"]
    tr    = report["trades"]
    dd    = report["drawdown"]
    alerts = report["alerts"]
    ks    = report["kill_switch"]

    def _col(ok: bool, text: str) -> str:
        return f"{GREEN}{text}{RESET}" if ok else f"{RED}{text}{RESET}"

    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}  APEX Paper Trading Daily Report — {date_str}{RESET}")
    print(f"{'─'*60}")

    # P&L
    pnl     = acc["pnl_today"]
    pnl_pct = acc["pnl_today_pct"]
    pnl_ok  = pnl >= 0
    print(f"  P&L Today      {_col(pnl_ok, f'${pnl:+,.2f}  ({pnl_pct:+.2%})')}")
    print(f"  Portfolio NAV  ${acc['equity']:,.2f}")

    # Trades
    win_r = tr["win_rate"]
    win_str = f"{win_r:.0%}" if win_r is not None else "n/a"
    print(f"\n  Round Trips    {tr['round_trips']}")
    print(f"  Win Rate       {_col(win_r is not None and win_r >= 0.5, win_str)}")
    print(f"  Realized P&L   ${tr['realized_pnl']:+,.2f}")
    hold = tr["avg_holding_minutes"]
    print(f"  Avg Hold Time  {f'{hold:.1f} min' if hold is not None else 'n/a'}")

    # Drawdown
    dd_pct = dd["max_intraday_pct"]
    print(f"\n  Max Drawdown   {_col(dd_pct < 0.03, f'{dd_pct:.2%}')}")

    # Kill switch
    ks_ok = ks == "INACTIVE"
    print(f"  Kill Switch    {_col(ks_ok, ks)}")

    # Weight drift
    wd = report["ensemble_weights"]
    if wd["status"] != "unavailable":
        wd_ok = wd["status"] == "OK"
        print(f"  Weight Drift   {_col(wd_ok, wd['status'])}", end="")
        if not wd_ok:
            print(f"  ({', '.join(f'{k}:{v:.0%}' for k,v in wd['drifts'].items() if v > 0.05)})", end="")
        print()

    # Alerts
    if alerts:
        print(f"\n  {'─'*56}")
        for a in alerts:
            col = RED if a["severity"] == "CRITICAL" else YELLOW
            print(f"  {col}[{a['severity']}]{RESET} {a['message']}")
    else:
        print(f"\n  {GREEN}No alerts — system healthy.{RESET}")

    print(f"\n{'─'*60}\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="APEX Paper Trading Monitor")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Date to analyse (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--alert-only",
        action="store_true",
        help="Suppress healthy output; only print alerts.",
    )
    args = parser.parse_args()

    report = build_report(args.date, args.alert_only)

    if not args.alert_only or report["alerts"]:
        print_summary(report)

    # Exit 1 if daily loss limit breached
    for alert in report["alerts"]:
        if alert["code"] in ("DAILY_LOSS_LIMIT_BREACHED", "INTRADAY_DRAWDOWN_BREACH"):
            sys.exit(1)


if __name__ == "__main__":
    main()
