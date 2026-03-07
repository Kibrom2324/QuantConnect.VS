#!/usr/bin/env python3
"""
APEX Model Retrain Scheduler
scripts/retrain_scheduler.py

Runs as a background daemon (or one-shot via --check-now).  Every
SCHEDULER_INTERVAL_SECONDS it evaluates two independent retrain triggers:

  Trigger A — Time-based
    If more than RETRAIN_INTERVAL_DAYS (default: 30) have elapsed since the
    last MLflow production run, schedule a retrain.

  Trigger B — Performance-based (Sharpe drift)
    If the rolling 14-day live Sharpe published by services/model_monitor
    has dropped ≥ SHARPE_DRIFT_THRESHOLD (default: 30 %) below the last
    backtest Sharpe, schedule a retrain immediately.

When a retrain is triggered:
  1. Spawns services/model_training/walk_forward.py as a subprocess.
  2. Logs a retrain_event to MLflow (experiment: apex-retrain-events).
  3. Posts a JSON alert to ALERT_WEBHOOK_URL if configured.
  4. Writes the retrain outcome (success/failure, new run_id) to Redis
     so model_monitor can track drift against the updated baseline.

Exit codes
──────────
  0  — clean exit (SIGTERM / --check-now completed)
  1  — fatal startup error

Usage
─────
  # Daemon mode (runs until SIGTERM):
  python scripts/retrain_scheduler.py

  # One-shot mode (check triggers once, retrain if needed, then exit):
  python scripts/retrain_scheduler.py --check-now

  # Dry-run (evaluate triggers but do not retrain):
  python scripts/retrain_scheduler.py --check-now --dry-run

Environment variables
─────────────────────
  MLFLOW_TRACKING_URI          MLflow server URL         (default: http://localhost:5000)
  MLFLOW_EXPERIMENT_NAME       Walk-forward experiment   (default: apex-walk-forward)
  RETRAIN_EXPERIMENT_NAME      Retrain events experiment (default: apex-retrain-events)
  RETRAIN_SCRIPT_PATH          Path to walk_forward entry point (default: see below)
  RETRAIN_INTERVAL_DAYS        Days between forced retrains (default: 30)
  SHARPE_DRIFT_THRESHOLD       Fractional drift that triggers retrain (default: 0.30)
  SCHEDULER_INTERVAL_SECONDS   Daemon poll interval, seconds (default: 3600)
  REDIS_HOST                   Redis host (default: localhost)
  REDIS_PORT                   Redis port (default: 6379)
  REDIS_PASSWORD               Redis password (default: none)
  ALERT_WEBHOOK_URL            Webhook for retrain notifications (optional)

Redis keys written
──────────────────
  apex:model:last_retrain_ts         ISO timestamp of last successful retrain
  apex:model:last_retrain_run_id     MLflow run_id of last production fold
  apex:model:last_backtest_sharpe    OOS Sharpe from last production fold
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
log = logging.getLogger("retrain_scheduler")

# ─── Configuration ────────────────────────────────────────────────────────────

MLFLOW_TRACKING_URI       = os.getenv("MLFLOW_TRACKING_URI",       "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME    = os.getenv("MLFLOW_EXPERIMENT_NAME",    "apex-walk-forward")
RETRAIN_EXPERIMENT_NAME   = os.getenv("RETRAIN_EXPERIMENT_NAME",   "apex-retrain-events")

_WS = Path(__file__).resolve().parent.parent
RETRAIN_SCRIPT_PATH = Path(
    os.getenv("RETRAIN_SCRIPT_PATH",
              str(_WS / "services" / "model_training" / "walk_forward_runner.py"))
)

RETRAIN_INTERVAL_DAYS     = int(float(os.getenv("RETRAIN_INTERVAL_DAYS",    "30")))
SHARPE_DRIFT_THRESHOLD    = float(os.getenv("SHARPE_DRIFT_THRESHOLD",       "0.30"))
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS",   "3600"))
ALERT_WEBHOOK_URL          = os.getenv("ALERT_WEBHOOK_URL", "")

REDIS_HOST     = os.getenv("REDIS_HOST",     "localhost")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None

# Redis keys
KEY_LAST_RETRAIN_TS      = "apex:model:last_retrain_ts"
KEY_LAST_RETRAIN_RUN_ID  = "apex:model:last_retrain_run_id"
KEY_LAST_BACKTEST_SHARPE = "apex:model:last_backtest_sharpe"
KEY_LIVE_SHARPE          = "apex:model:live_sharpe_14d"    # written by model_monitor


# ─── Optional MLflow ──────────────────────────────────────────────────────────

try:
    import mlflow
    _MLFLOW_OK = True
except ImportError:
    mlflow = None  # type: ignore[assignment]
    _MLFLOW_OK = False
    log.warning("mlflow not installed — retrain events will not be logged to MLflow")


# ─── Redis helpers ────────────────────────────────────────────────────────────

def _redis_client() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
        socket_timeout=5.0, decode_responses=True,
    )


def _redis_get(key: str) -> Optional[str]:
    try:
        r = _redis_client()
        return r.get(key)
    except redis.RedisError as exc:
        log.warning("Redis read failed for key=%s: %s", key, exc)
        return None


def _redis_set(key: str, value: str) -> None:
    try:
        r = _redis_client()
        r.set(key, value)
    except redis.RedisError as exc:
        log.warning("Redis write failed for key=%s: %s", key, exc)


# ─── MLflow helpers ───────────────────────────────────────────────────────────

def _mlflow_get_last_production_run() -> Optional[dict]:
    """
    Query MLflow REST API for the most recent run tagged production=true
    in the walk-forward experiment.

    Returns a dict with keys: run_id, start_time_ms, oos_sharpe.
    Returns None if MLflow is unreachable or no production run exists.
    """
    base = MLFLOW_TRACKING_URI.rstrip("/")
    try:
        # 1. Get experiment ID
        resp = httpx.get(
            f"{base}/api/2.0/mlflow/experiments/get-by-name",
            params={"experiment_name": MLFLOW_EXPERIMENT_NAME},
            timeout=10.0,
        )
        if resp.status_code == 404:
            log.warning("MLflow experiment '%s' not found", MLFLOW_EXPERIMENT_NAME)
            return None
        resp.raise_for_status()
        exp_id = resp.json()["experiment"]["experiment_id"]

        # 2. Search for production-tagged runs
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
            log.warning("No production-tagged runs found in experiment '%s'", MLFLOW_EXPERIMENT_NAME)
            return None

        run = runs[0]
        run_info    = run["info"]
        run_data    = run.get("data", {})
        metrics_raw = {m["key"]: m["value"] for m in run_data.get("metrics", [])}
        oos_sharpe  = float(metrics_raw.get("oos_sharpe", 0.0))

        return {
            "run_id":        run_info["run_id"],
            "start_time_ms": run_info["start_time"],
            "oos_sharpe":    oos_sharpe,
        }

    except httpx.ConnectError as exc:
        log.warning("Cannot reach MLflow at %s: %s", base, exc)
        return None
    except Exception as exc:
        log.warning("MLflow query failed: %s", exc)
        return None


def _mlflow_log_retrain_event(
    trigger: str,
    retrain_run_id: Optional[str],
    live_sharpe:     float,
    backtest_sharpe: float,
    success:         bool,
    detail:          str = "",
) -> None:
    """Log a retrain_event run to the apex-retrain-events experiment."""
    if not _MLFLOW_OK:
        return
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(RETRAIN_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"retrain_{trigger}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"):
            mlflow.log_params({
                "trigger":            trigger,
                "retrain_script":     str(RETRAIN_SCRIPT_PATH),
                "sharpe_drift_threshold": SHARPE_DRIFT_THRESHOLD,
                "retrain_interval_days":  RETRAIN_INTERVAL_DAYS,
            })
            mlflow.log_metrics({
                "live_sharpe":      live_sharpe,
                "backtest_sharpe":  backtest_sharpe,
                "sharpe_drift_pct": (
                    (backtest_sharpe - live_sharpe) / abs(backtest_sharpe)
                    if backtest_sharpe != 0 else 0.0
                ),
            })
            mlflow.set_tags({
                "trigger":             trigger,
                "success":             str(success),
                "new_production_run":  retrain_run_id or "unknown",
                "detail":              detail,
            })
        log.info("Retrain event logged to MLflow experiment '%s'", RETRAIN_EXPERIMENT_NAME)
    except Exception as exc:
        log.warning("Could not log retrain event to MLflow: %s", exc)


# ─── Webhook alert ────────────────────────────────────────────────────────────

def _send_alert(subject: str, body: dict) -> None:
    if not ALERT_WEBHOOK_URL:
        return
    payload = {
        "text": f":robot_face: *APEX Model Retrain* — {subject}",
        "attachments": [{
            "color":  "#36a64f" if body.get("success") else "#ff0000",
            "fields": [
                {"title": k, "value": str(v), "short": True}
                for k, v in body.items()
            ],
            "footer": "APEX Retrain Scheduler",
            "ts":     int(time.time()),
        }],
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(ALERT_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        log.info("Alert webhook sent: %s", subject)
    except Exception as exc:
        log.warning("Webhook alert failed: %s", exc)


# ─── Trigger evaluation ───────────────────────────────────────────────────────

class RetrainTrigger:
    """Evaluates whether a retrain is needed and why."""

    def __init__(self) -> None:
        self.triggered:       bool  = False
        self.reason:          str   = ""
        self.live_sharpe:     float = 0.0
        self.backtest_sharpe: float = 0.0
        self.drift_pct:       float = 0.0
        self.days_since_last: Optional[float] = None

    def evaluate(self) -> "RetrainTrigger":
        """Check both triggers; populate self with result."""
        prod_run = _mlflow_get_last_production_run()

        # ── Resolve current metrics ───────────────────────────────────────────
        # Backtest Sharpe: from MLflow first, then Redis cache
        if prod_run:
            self.backtest_sharpe = prod_run["oos_sharpe"]
            _redis_set(KEY_LAST_BACKTEST_SHARPE, str(self.backtest_sharpe))
            _redis_set(KEY_LAST_RETRAIN_RUN_ID,  prod_run["run_id"])
        else:
            cached = _redis_get(KEY_LAST_BACKTEST_SHARPE)
            self.backtest_sharpe = float(cached) if cached else 0.0

        # Live Sharpe: from Redis (written by model_monitor/main.py)
        live_raw = _redis_get(KEY_LIVE_SHARPE)
        self.live_sharpe = float(live_raw) if live_raw is not None else 0.0

        # Drift: (backtest - live) / |backtest|  — positive means degraded
        if self.backtest_sharpe != 0.0:
            self.drift_pct = (self.backtest_sharpe - self.live_sharpe) / abs(self.backtest_sharpe)
        else:
            self.drift_pct = 0.0

        # ── Trigger A: time-based ─────────────────────────────────────────────
        last_ts_raw = _redis_get(KEY_LAST_RETRAIN_TS)
        if prod_run:
            # Use MLflow start_time as the authoritative last-retrain timestamp
            last_retrain_dt = datetime.fromtimestamp(
                prod_run["start_time_ms"] / 1000, tz=timezone.utc
            )
        elif last_ts_raw:
            last_retrain_dt = datetime.fromisoformat(last_ts_raw)
        else:
            last_retrain_dt = None

        if last_retrain_dt:
            self.days_since_last = (
                datetime.now(timezone.utc) - last_retrain_dt
            ).total_seconds() / 86400
        else:
            self.days_since_last = None

        if self.days_since_last is None or self.days_since_last >= RETRAIN_INTERVAL_DAYS:
            days_str = f"{self.days_since_last:.1f}" if self.days_since_last is not None else "unknown"
            self.triggered = True
            self.reason    = (
                f"time_based: {days_str} days since last retrain "
                f"(threshold: {RETRAIN_INTERVAL_DAYS}d)"
            )
            log.info(
                "Trigger A (time-based): %s days since last retrain (threshold %dd)",
                days_str, RETRAIN_INTERVAL_DAYS,
            )

        # ── Trigger B: Sharpe drift ───────────────────────────────────────────
        if self.backtest_sharpe > 0 and self.drift_pct >= SHARPE_DRIFT_THRESHOLD:
            self.triggered = True
            if "time_based" in self.reason:
                self.reason += " + "
            self.reason += (
                f"sharpe_drift: live={self.live_sharpe:.3f} vs "
                f"backtest={self.backtest_sharpe:.3f} "
                f"drift={self.drift_pct*100:.1f}% >= {SHARPE_DRIFT_THRESHOLD*100:.0f}%"
            )
            log.warning(
                "Trigger B (Sharpe drift): live=%.3f backtest=%.3f drift=%.1f%% threshold=%.0f%%",
                self.live_sharpe, self.backtest_sharpe,
                self.drift_pct * 100, SHARPE_DRIFT_THRESHOLD * 100,
            )

        if not self.triggered:
            log.info(
                "No retrain needed — "
                "days_since_last=%.1f (threshold=%dd), "
                "drift=%.1f%% (threshold=%.0f%%)",
                self.days_since_last or 0,
                RETRAIN_INTERVAL_DAYS,
                self.drift_pct * 100,
                SHARPE_DRIFT_THRESHOLD * 100,
            )

        return self


# ─── Retrain execution ────────────────────────────────────────────────────────

def run_retrain(trigger: RetrainTrigger, dry_run: bool = False) -> bool:
    """
    Invoke the walk-forward retrain script as a subprocess.

    Returns True if the retrain completed successfully.
    """
    log.info(
        "Retrain triggered — reason: %s | dry_run=%s",
        trigger.reason, dry_run,
    )

    if dry_run:
        log.info("DRY RUN — skipping actual retrain subprocess")
        _send_alert("Dry-run retrain triggered", {
            "reason":     trigger.reason,
            "live_sharpe":     trigger.live_sharpe,
            "backtest_sharpe": trigger.backtest_sharpe,
            "drift_pct":       f"{trigger.drift_pct*100:.1f}%",
            "success":         "dry-run",
        })
        return True

    if not RETRAIN_SCRIPT_PATH.exists():
        # Fallback: try running the WalkForwardTrainer directly from the module
        log.warning(
            "Retrain script not found at %s — attempting module-based invocation",
            RETRAIN_SCRIPT_PATH,
        )
        script_cmd = [
            sys.executable, "-m",
            "services.model_training.walk_forward",
        ]
    else:
        script_cmd = [sys.executable, str(RETRAIN_SCRIPT_PATH)]

    env = os.environ.copy()
    env["MLFLOW_TRACKING_URI"]    = MLFLOW_TRACKING_URI
    env["MLFLOW_EXPERIMENT_NAME"] = MLFLOW_EXPERIMENT_NAME

    log.info("Spawning retrain: %s", " ".join(script_cmd))
    started_at = datetime.now(timezone.utc)

    try:
        result = subprocess.run(
            script_cmd,
            env=env,
            cwd=str(_WS),
            capture_output=False,   # let stdout/stderr flow to our log
            timeout=7200,           # 2-hour hard cap on retraining
        )
        success  = result.returncode == 0
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        log.info(
            "Retrain subprocess finished — rc=%d success=%s duration=%.0fs",
            result.returncode, success, duration,
        )

    except subprocess.TimeoutExpired:
        log.error("Retrain subprocess timed out after 2 hours")
        success  = False
        duration = 7200.0

    except Exception as exc:
        log.error("Retrain subprocess failed with exception: %s", exc)
        success  = False
        duration = 0.0

    # ── Post-retrain bookkeeping ───────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    if success:
        _redis_set(KEY_LAST_RETRAIN_TS, now_iso)
        # Fetch the new production run's Sharpe and update Redis
        new_prod = _mlflow_get_last_production_run()
        new_run_id  = new_prod["run_id"]    if new_prod else "unknown"
        new_sharpe  = new_prod["oos_sharpe"] if new_prod else 0.0
        _redis_set(KEY_LAST_RETRAIN_RUN_ID,  new_run_id)
        _redis_set(KEY_LAST_BACKTEST_SHARPE, str(new_sharpe))
        log.info(
            "Retrain complete — new production run_id=%s oos_sharpe=%.3f",
            new_run_id, new_sharpe,
        )
    else:
        new_run_id = "failed"
        new_sharpe = trigger.backtest_sharpe

    # Log to MLflow retrain-events experiment
    _mlflow_log_retrain_event(
        trigger          = trigger.reason[:80],
        retrain_run_id   = new_run_id,
        live_sharpe      = trigger.live_sharpe,
        backtest_sharpe  = trigger.backtest_sharpe,
        success          = success,
        detail           = f"duration={duration:.0f}s",
    )

    # Send webhook notification
    _send_alert(
        "Retrain completed" if success else "Retrain FAILED",
        {
            "reason":            trigger.reason,
            "success":           success,
            "duration_seconds":  int(duration),
            "new_run_id":        new_run_id,
            "new_oos_sharpe":    round(new_sharpe, 4),
            "prev_live_sharpe":  round(trigger.live_sharpe, 4),
            "completed_at":      now_iso,
        },
    )

    return success


# ─── Main loop ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="APEX Model Retrain Scheduler",
    )
    parser.add_argument(
        "--check-now",
        action="store_true",
        help="Evaluate triggers once and exit (one-shot mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate triggers and log alert, but do not run retrain",
    )
    args = parser.parse_args()

    log.info(
        "Retrain scheduler starting — "
        "interval=%ds, retrain_every=%dd, drift_threshold=%.0f%%",
        SCHEDULER_INTERVAL_SECONDS,
        RETRAIN_INTERVAL_DAYS,
        SHARPE_DRIFT_THRESHOLD * 100,
    )

    running = True

    def _stop(sig, _frame):
        nonlocal running
        log.info("Signal %s received — stopping", sig)
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    while running:
        loop_start = time.monotonic()

        trigger = RetrainTrigger().evaluate()

        if trigger.triggered:
            run_retrain(trigger, dry_run=args.dry_run)

        if args.check_now:
            log.info("--check-now: exiting after single evaluation")
            return 0

        elapsed   = time.monotonic() - loop_start
        remaining = SCHEDULER_INTERVAL_SECONDS - elapsed
        if remaining > 0 and running:
            log.info("Next check in %.0f minutes", remaining / 60)
            time.sleep(remaining)

    log.info("Retrain scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
