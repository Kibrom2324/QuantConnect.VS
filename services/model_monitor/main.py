#!/usr/bin/env python3
"""
APEX Model Monitor Service
services/model_monitor/main.py

Runs as a long-lived service (HTTP server on port 8020).  Every
POLL_INTERVAL_SECONDS (default: 3600 = 1 hour) it:

  1. Fetches the last 14 calendar days of Alpaca portfolio history.
  2. Computes the rolling 14-day live Sharpe from daily equity returns.
  3. Queries MLflow for the last production run's OOS Sharpe (backtest baseline).
  4. Computes relative drift: (backtest_sharpe - live_sharpe) / |backtest_sharpe|
  5. Exposes four Prometheus metrics on /metrics (port 8020):
       apex_model_live_sharpe_14d          — rolling 14d live Sharpe
       apex_model_backtest_sharpe          — last MLflow production OOS Sharpe
       apex_model_sharpe_drift_ratio       — drift fraction (0.30 = 30% degraded)
       apex_model_last_retrain_timestamp   — Unix epoch of last production run
  6. Writes apex:model:live_sharpe_14d to Redis for retrain_scheduler.py.
  7. If drift ≥ DRIFT_ALERT_THRESHOLD (0.30), logs a CRITICAL alert.

Prometheus alert rules in infra/prometheus/model_alerts.yml scrape these
metrics and fire ModelSharpeDrift / ModelStale alerts accordingly.

Exit codes
──────────
  0  — clean shutdown
  1  — fatal startup error

Usage
─────
  python services/model_monitor/main.py

  # Prometheus endpoint:
  curl http://localhost:8020/metrics

  # Health check:
  curl http://localhost:8020/health

Environment variables
─────────────────────
  ALPACA_BASE_URL          Alpaca endpoint
  ALPACA_API_KEY           Alpaca API key
  ALPACA_SECRET_KEY        Alpaca secret key
  MLFLOW_TRACKING_URI      MLflow server URL      (default: http://localhost:5000)
  MLFLOW_EXPERIMENT_NAME   Walk-forward experiment (default: apex-walk-forward)
  REDIS_HOST               Redis host             (default: localhost)
  REDIS_PORT               Redis port             (default: 6379)
  REDIS_PASSWORD           Redis password
  POLL_INTERVAL_SECONDS    How often to re-poll   (default: 3600)
  DRIFT_ALERT_THRESHOLD    Drift ratio for CRITICAL log (default: 0.30)
  METRICS_PORT             Prometheus HTTP port   (default: 8020)
  LOOKBACK_DAYS            Rolling window for live Sharpe (default: 14)
  ANN_FACTOR               Annualisation factor   (default: 252 for daily)
"""

from __future__ import annotations

import logging
import math
import os
import signal
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import httpx
import redis

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("model_monitor")

# ─── Configuration ────────────────────────────────────────────────────────────

ALPACA_BASE_URL         = os.getenv("ALPACA_BASE_URL",      "https://paper-api.alpaca.markets")
ALPACA_API_KEY          = os.getenv("ALPACA_API_KEY",       "")
ALPACA_SECRET_KEY       = os.getenv("ALPACA_SECRET_KEY",    "")

MLFLOW_TRACKING_URI     = os.getenv("MLFLOW_TRACKING_URI",  "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME  = os.getenv("MLFLOW_EXPERIMENT_NAME", "apex-walk-forward")

REDIS_HOST              = os.getenv("REDIS_HOST",     "localhost")
REDIS_PORT              = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD          = os.getenv("REDIS_PASSWORD", "") or None

POLL_INTERVAL_SECONDS   = int(os.getenv("POLL_INTERVAL_SECONDS",   "3600"))
DRIFT_ALERT_THRESHOLD   = float(os.getenv("DRIFT_ALERT_THRESHOLD", "0.30"))
METRICS_PORT            = int(os.getenv("METRICS_PORT",             "8020"))
LOOKBACK_DAYS           = int(os.getenv("LOOKBACK_DAYS",            "14"))
ANN_FACTOR              = float(os.getenv("ANN_FACTOR",             "252"))

# Redis key written for retrain_scheduler.py
KEY_LIVE_SHARPE = "apex:model:live_sharpe_14d"


# ─── Shared metrics state (written by poll loop, read by HTTP server) ─────────

class Metrics:
    """Thread-safe container for the four exposed Prometheus gauges."""

    def __init__(self) -> None:
        self._lock              = threading.Lock()
        self.live_sharpe        = float("nan")
        self.backtest_sharpe    = float("nan")
        self.drift_ratio        = float("nan")
        self.last_retrain_ts    = 0.0          # Unix epoch
        self.last_poll_ts       = 0.0
        self.poll_count         = 0
        self.brier_score        = float("nan")
        self.last_error: Optional[str] = None

    def update(
        self,
        live_sharpe:     float,
        backtest_sharpe: float,
        last_retrain_ts: float,
    ) -> None:
        with self._lock:
            self.live_sharpe     = live_sharpe
            self.backtest_sharpe = backtest_sharpe
            self.drift_ratio     = (
                (backtest_sharpe - live_sharpe) / abs(backtest_sharpe)
                if backtest_sharpe != 0.0 and not math.isnan(backtest_sharpe)
                else float("nan")
            )
            self.last_retrain_ts = last_retrain_ts
            self.last_poll_ts    = time.time()
            self.poll_count     += 1
            self.last_error      = None

    def record_error(self, msg: str) -> None:
        with self._lock:
            self.last_error   = msg
            self.last_poll_ts = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "live_sharpe":     self.live_sharpe,
                "backtest_sharpe": self.backtest_sharpe,
                "drift_ratio":     self.drift_ratio,
                "last_retrain_ts": self.last_retrain_ts,
                "last_poll_ts":    self.last_poll_ts,
                "poll_count":      self.poll_count,
                "brier_score":     self.brier_score,
                "last_error":      self.last_error,
            }


_metrics = Metrics()


# ─── Alpaca helpers ───────────────────────────────────────────────────────────

def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def fetch_portfolio_history(lookback_days: int) -> list[float]:
    """
    Fetch daily equity values from Alpaca portfolio history for the last
    `lookback_days` calendar days.

    Returns a list of daily closing equity values (floats), oldest first.
    Uses the 1D timeframe so each point is one trading day's close.
    """
    end_date   = date.today().isoformat()
    period     = f"{lookback_days}D"
    url        = (
        f"{ALPACA_BASE_URL.rstrip('/')}/v2/account/portfolio/history"
        f"?period={period}&timeframe=1D&date_end={end_date}&extended_hours=false"
    )
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        resp = client.get(url, headers=_alpaca_headers())
        resp.raise_for_status()

    data       = resp.json()
    equity_arr = data.get("equity", [])
    # Filter out None / 0 values (non-trading days)
    return [float(e) for e in equity_arr if e is not None and float(e) > 0]


def compute_daily_sharpe(equity_series: list[float]) -> float:
    """
    Compute annualised Sharpe ratio from a series of daily equity values.

    daily_return_i = (equity_i - equity_{i-1}) / equity_{i-1}
    Sharpe = mean(returns) / std(returns) * sqrt(ANN_FACTOR)

    Returns NaN if the series is too short (< 2 points) or std == 0.
    """
    if len(equity_series) < 2:
        return float("nan")

    returns = []
    for i in range(1, len(equity_series)):
        prev = equity_series[i - 1]
        curr = equity_series[i]
        if prev > 0:
            returns.append((curr - prev) / prev)

    if len(returns) < 2:
        return float("nan")

    n    = len(returns)
    mean = sum(returns) / n
    var  = sum((r - mean) ** 2 for r in returns) / (n - 1)   # sample variance
    std  = math.sqrt(var)

    if std == 0.0:
        return float("nan")

    return (mean / std) * math.sqrt(ANN_FACTOR)


# ─── MLflow helpers ───────────────────────────────────────────────────────────

def fetch_mlflow_production_sharpe() -> tuple[float, float]:
    """
    Query MLflow for the most recent production-tagged run.

    Returns (oos_sharpe, start_time_unix_epoch).
    Returns (nan, 0.0) if MLflow is unreachable or no production run exists.
    """
    base = MLFLOW_TRACKING_URI.rstrip("/")
    try:
        # Get experiment ID
        resp = httpx.get(
            f"{base}/api/2.0/mlflow/experiments/get-by-name",
            params={"experiment_name": MLFLOW_EXPERIMENT_NAME},
            timeout=10.0,
        )
        if resp.status_code == 404:
            log.warning("MLflow experiment '%s' not found", MLFLOW_EXPERIMENT_NAME)
            return float("nan"), 0.0
        resp.raise_for_status()
        exp_id = resp.json()["experiment"]["experiment_id"]

        # Search production-tagged runs
        payload = {
            "experiment_ids": [exp_id],
            "filter":         "tags.production = 'true'",
            "order_by":       ["start_time DESC"],
            "max_results":    1,
        }
        resp = httpx.post(
            f"{base}/api/2.0/mlflow/runs/search",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        runs = resp.json().get("runs", [])
        if not runs:
            log.warning("No production-tagged run found in MLflow experiment '%s'", MLFLOW_EXPERIMENT_NAME)
            return float("nan"), 0.0

        run     = runs[0]
        metrics = {m["key"]: m["value"] for m in run.get("data", {}).get("metrics", [])}
        sharpe  = float(metrics.get("oos_sharpe", float("nan")))
        ts_ms   = float(run["info"]["start_time"])
        return sharpe, ts_ms / 1000.0

    except httpx.ConnectError as exc:
        log.warning("Cannot reach MLflow at %s: %s", base, exc)
        return float("nan"), 0.0
    except Exception as exc:
        log.warning("MLflow query failed: %s", exc)
        return float("nan"), 0.0


# ─── Redis write ──────────────────────────────────────────────────────────────

def _write_redis(key: str, value: str) -> None:
    try:
        r = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
            socket_timeout=5.0, decode_responses=True,
        )
        r.set(key, value)
    except redis.RedisError as exc:
        log.warning("Redis write failed (key=%s): %s", key, exc)


# ─── Poll loop ────────────────────────────────────────────────────────────────

def poll_once() -> None:
    """
    Execute one full monitoring cycle:
      fetch equity → compute live Sharpe → fetch MLflow Sharpe →
      compute drift → update metrics → write Redis → log alerts.
    """
    log.info("Model monitor poll starting (lookback=%dd)", LOOKBACK_DAYS)

    # ── 1. Fetch portfolio history from Alpaca ─────────────────────────────
    try:
        equity_series = fetch_portfolio_history(LOOKBACK_DAYS)
        log.info("Fetched %d equity data points from Alpaca", len(equity_series))
    except httpx.HTTPStatusError as exc:
        msg = f"Alpaca portfolio history HTTP {exc.response.status_code}: {exc}"
        log.error(msg)
        _metrics.record_error(msg)
        return
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        msg = f"Alpaca connection error: {exc}"
        log.error(msg)
        _metrics.record_error(msg)
        return
    except Exception as exc:
        msg = f"Unexpected error fetching portfolio history: {exc}"
        log.error(msg)
        _metrics.record_error(msg)
        return

    # ── 2. Compute rolling live Sharpe ─────────────────────────────────────
    live_sharpe = compute_daily_sharpe(equity_series)
    if math.isnan(live_sharpe):
        log.warning(
            "Could not compute live Sharpe — insufficient data points (%d)",
            len(equity_series),
        )
    else:
        log.info("Live Sharpe (rolling %dd): %.4f", LOOKBACK_DAYS, live_sharpe)
        _write_redis(KEY_LIVE_SHARPE, str(live_sharpe))

    # ── 3. Fetch backtest Sharpe from MLflow ───────────────────────────────
    backtest_sharpe, last_retrain_ts = fetch_mlflow_production_sharpe()
    if math.isnan(backtest_sharpe):
        log.warning("Backtest Sharpe unavailable from MLflow — using cached value if available")
    else:
        log.info("Backtest Sharpe (MLflow production run): %.4f", backtest_sharpe)

    # ── 4. Compute drift ───────────────────────────────────────────────────
    if not math.isnan(live_sharpe) and not math.isnan(backtest_sharpe) and backtest_sharpe != 0:
        drift_ratio = (backtest_sharpe - live_sharpe) / abs(backtest_sharpe)
        log.info(
            "Sharpe drift: %.1f%% (live=%.4f backtest=%.4f threshold=%.0f%%)",
            drift_ratio * 100, live_sharpe, backtest_sharpe,
            DRIFT_ALERT_THRESHOLD * 100,
        )

        if drift_ratio >= DRIFT_ALERT_THRESHOLD:
            log.critical(
                "MODEL DRIFT ALERT: Sharpe degraded %.1f%% "
                "(live=%.4f, backtest=%.4f) — retrain_scheduler should trigger",
                drift_ratio * 100, live_sharpe, backtest_sharpe,
            )
    else:
        drift_ratio = float("nan")

    # ── 5. Update shared metrics for /metrics endpoint ─────────────────────
    _metrics.update(
        live_sharpe     = live_sharpe     if not math.isnan(live_sharpe)     else 0.0,
        backtest_sharpe = backtest_sharpe if not math.isnan(backtest_sharpe) else 0.0,
        last_retrain_ts = last_retrain_ts,
    )

    snap = _metrics.snapshot()
    log.info(
        "Poll complete — live_sharpe=%.4f backtest_sharpe=%.4f "
        "drift=%.1f%% last_retrain=%s poll_count=%d",
        snap["live_sharpe"],
        snap["backtest_sharpe"],
        snap["drift_ratio"] * 100 if not math.isnan(snap["drift_ratio"]) else float("nan"),
        datetime.fromtimestamp(snap["last_retrain_ts"], tz=timezone.utc).isoformat()
        if snap["last_retrain_ts"] > 0 else "unknown",
        snap["poll_count"],
    )

    # ── Phase 0: read Brier score from Redis feedback key ──────────────────
    try:
        from shared.core.metrics import CALIBRATION_BRIER  # noqa: PLC0415
        r = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
            socket_timeout=5.0, decode_responses=True,
        )
        brier_raw = r.get("apex:feedback:brier_score")
        if brier_raw is not None:
            brier = float(brier_raw)
            CALIBRATION_BRIER.set(brier)
            with _metrics._lock:
                _metrics.brier_score = brier
            log.info("Calibration Brier score: %.6f", brier)
    except Exception as exc:
        log.debug("Brier score read failed (non-critical): %s", exc)


def poll_loop(stop_event: threading.Event) -> None:
    """Background thread: poll on startup, then every POLL_INTERVAL_SECONDS."""
    # First poll immediately
    try:
        poll_once()
    except Exception as exc:
        log.error("Unexpected error in first poll: %s", exc)

    while not stop_event.wait(timeout=POLL_INTERVAL_SECONDS):
        try:
            poll_once()
        except Exception as exc:
            log.error("Unexpected error in poll loop: %s", exc)

    log.info("Poll loop stopped")


# ─── Prometheus /metrics HTTP handler ─────────────────────────────────────────

def _fmt_gauge(name: str, value: float, help_text: str, labels: str = "") -> str:
    """Format a single Prometheus gauge metric in text exposition format."""
    label_str = f"{{{labels}}}" if labels else ""
    val_str   = str(value) if not math.isnan(value) and not math.isinf(value) else "NaN"
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} gauge\n"
        f"{name}{label_str} {val_str}\n"
    )


class MetricsHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — serves /metrics and /health."""

    def do_GET(self):
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path in ("/health", "/healthz"):
            self._serve_health()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_metrics(self) -> None:
        snap    = _metrics.snapshot()
        drift   = snap["drift_ratio"]
        payload = (
            _fmt_gauge(
                "apex_model_live_sharpe_14d",
                snap["live_sharpe"],
                f"Rolling {LOOKBACK_DAYS}-day live Sharpe ratio (annualised)",
            )
            + _fmt_gauge(
                "apex_model_backtest_sharpe",
                snap["backtest_sharpe"],
                "OOS Sharpe from last MLflow production walk-forward run",
            )
            + _fmt_gauge(
                "apex_model_sharpe_drift_ratio",
                drift if not math.isnan(drift) else 0.0,
                "Relative Sharpe degradation: (backtest - live) / |backtest|. "
                "0.30 means 30% below backtest baseline.",
            )
            + _fmt_gauge(
                "apex_model_last_retrain_timestamp",
                snap["last_retrain_ts"],
                "Unix timestamp of last MLflow production walk-forward run",
            )
            + _fmt_gauge(
                "apex_model_monitor_poll_count",
                float(snap["poll_count"]),
                "Number of completed model monitor poll cycles",
            )
            + _fmt_gauge(
                "apex_model_monitor_last_poll_timestamp",
                snap["last_poll_ts"],
                "Unix timestamp of last completed poll cycle",
            )
            + _fmt_gauge(
                "apex_calibration_brier_score",
                snap["brier_score"],
                "Brier score of the isotonic calibrator (lower is better)",
            )
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_health(self) -> None:
        snap   = _metrics.snapshot()
        status = "ok" if snap["last_error"] is None else "degraded"
        body   = (
            f'{{"status":"{status}",'
            f'"poll_count":{snap["poll_count"]},'
            f'"last_error":{repr(snap["last_error"])}}}'
        ).encode()
        # Always 200 — "degraded" means running with transient errors, not down.
        # Only external infrastructure failures should make the container unhealthy.
        code   = 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress default access log (too noisy for Prometheus scrapes)
        pass


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.critical(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set — exiting"
        )
        return 1

    log.info(
        "Model monitor starting — port=%d poll_interval=%ds lookback=%dd drift_threshold=%.0f%%",
        METRICS_PORT, POLL_INTERVAL_SECONDS, LOOKBACK_DAYS,
        DRIFT_ALERT_THRESHOLD * 100,
    )

    stop_event = threading.Event()

    # Start poll loop in a background thread
    poll_thread = threading.Thread(
        target=poll_loop, args=(stop_event,), name="poll-loop", daemon=True
    )
    poll_thread.start()

    # Start metrics HTTP server in a background thread
    server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    server_thread = threading.Thread(
        target=server.serve_forever, name="http-server", daemon=True
    )
    server_thread.start()
    log.info("Prometheus metrics available at http://0.0.0.0:%d/metrics", METRICS_PORT)

    # Handle shutdown signals
    def _stop(sig, _frame):
        log.info("Signal %s received — shutting down", sig)
        stop_event.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    # Block until stopped
    stop_event.wait()
    poll_thread.join(timeout=10)
    log.info("Model monitor stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
