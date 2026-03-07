#!/usr/bin/env python3
"""
APEX Portfolio Drawdown Circuit Breaker
scripts/circuit_breaker.py

Runs as a background daemon.  Every CHECK_INTERVAL_SECONDS it fetches the
current portfolio equity from Alpaca and computes intraday drawdown from the
session high.  If drawdown exceeds DRAWDOWN_THRESHOLD_PCT it:

  1. Sets the Redis kill switch to "true" (PERMANENT — never auto-resets)
  2. Logs a structured CRITICAL alert
  3. Optionally POSTs to a webhook (ALERT_WEBHOOK_URL env var)

The kill switch is NEVER reset automatically.  Recovery requires explicit
manual intervention (see GO_LIVE_RUNBOOK.md, §5 — Kill Switch Protocol).

Exit codes
──────────
  0   — stopped cleanly (SIGTERM / SIGINT)
  1   — fatal startup error (Redis unavailable, missing env vars)

Usage
─────
  # Foreground (useful for testing):
  python scripts/circuit_breaker.py

  # Background daemon:
  nohup python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &
  echo $! > /tmp/circuit_breaker.pid

  # Manual kill-switch reset (after investigation):
  redis-cli SET apex:kill_switch false

Environment variables
─────────────────────
  ALPACA_BASE_URL          Alpaca endpoint (paper or live)
  ALPACA_API_KEY           Alpaca API key
  ALPACA_SECRET_KEY        Alpaca secret key
  REDIS_HOST               Redis host              (default: localhost)
  REDIS_PORT               Redis port              (default: 6379)
  REDIS_PASSWORD           Redis password          (default: none)
  DRAWDOWN_THRESHOLD_PCT   Max drawdown 0–1        (default: 0.05 = 5%)
  CHECK_INTERVAL_SECONDS   Poll interval           (default: 60)
  ALERT_WEBHOOK_URL        Webhook URL for alerts  (optional)
  KILL_SWITCH_REDIS_KEY    Redis key name          (default: apex:kill_switch)

Design rules
────────────
- All credentials come from os.getenv() — nothing is hardcoded.
- The kill switch is write-only from this process — it never reads back to
  reset.  "Latching" behaviour: once set, stays set.
- If Redis is unreachable, the process logs CRITICAL and exits 1 immediately.
  It does NOT retry silently — a dead Redis means the kill switch is unguarded.
- If Alpaca is unreachable for more than ALPACA_RETRY_LIMIT consecutive polls,
  the kill switch is engaged as a conservative fail-closed action.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
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
log = logging.getLogger("circuit_breaker")

# ─── Configuration from environment ──────────────────────────────────────────

ALPACA_BASE_URL         = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_API_KEY          = os.getenv("ALPACA_API_KEY",  "")
ALPACA_SECRET_KEY       = os.getenv("ALPACA_SECRET_KEY", "")

REDIS_HOST              = os.getenv("REDIS_HOST",     "localhost")
REDIS_PORT              = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD          = os.getenv("REDIS_PASSWORD", "") or None

DRAWDOWN_THRESHOLD_PCT  = float(os.getenv("DRAWDOWN_THRESHOLD_PCT", "0.05"))
CHECK_INTERVAL_SECONDS  = int(os.getenv("CHECK_INTERVAL_SECONDS",   "60"))
ALERT_WEBHOOK_URL       = os.getenv("ALERT_WEBHOOK_URL", "")
KILL_SWITCH_REDIS_KEY   = os.getenv("KILL_SWITCH_REDIS_KEY", "apex:kill_switch")

# Number of consecutive Alpaca failures before fail-closed engagement
ALPACA_RETRY_LIMIT      = int(os.getenv("ALPACA_RETRY_LIMIT", "5"))


# ─── Alpaca client ────────────────────────────────────────────────────────────

def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def fetch_equity() -> float:
    """Return current portfolio equity from Alpaca account endpoint.

    Raises httpx.HTTPError or httpx.ConnectError on failure.
    """
    url = f"{ALPACA_BASE_URL.rstrip('/')}/v2/account"
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        resp = client.get(url, headers=_alpaca_headers())
        resp.raise_for_status()
        data = resp.json()
        return float(data["equity"])


def fetch_positions() -> list[dict]:
    """Return current open positions for alert enrichment."""
    url = f"{ALPACA_BASE_URL.rstrip('/')}/v2/positions"
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.get(url, headers=_alpaca_headers())
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return []


# ─── Redis kill-switch ────────────────────────────────────────────────────────

def connect_redis() -> redis.Redis:
    """Return a connected Redis client.  Raises ConnectionError on failure."""
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        decode_responses=True,
    )
    r.ping()   # raises ConnectionError if unreachable
    return r


def engage_kill_switch(r: redis.Redis, reason: str, metadata: dict) -> None:
    """
    Latch the kill switch ON in Redis.

    Uses a pipeline with MULTI/EXEC to write both the kill switch flag and a
    metadata record atomically.  This is a one-way latch — it never resets.
    """
    meta_key = f"{KILL_SWITCH_REDIS_KEY}:metadata"
    meta_value = json.dumps({
        "engaged_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        **metadata,
    })

    pipe = r.pipeline(transaction=True)
    pipe.set(KILL_SWITCH_REDIS_KEY, "true")
    pipe.set(meta_key, meta_value)
    pipe.execute()

    log.critical(
        "KILL SWITCH ENGAGED — all trading halted. "
        "Manual reset required: redis-cli SET %s false",
        KILL_SWITCH_REDIS_KEY,
    )
    log.critical("Reason: %s | Metadata: %s", reason, json.dumps(metadata))


def is_kill_switch_active(r: redis.Redis) -> bool:
    """Return True if the kill switch is already latched."""
    raw = r.get(KILL_SWITCH_REDIS_KEY)
    if raw is None:
        return False
    return raw.strip().lower() in ("true", "1", "on", "yes")


# ─── Webhook alert ────────────────────────────────────────────────────────────

def send_webhook_alert(reason: str, metadata: dict) -> None:
    """POST a JSON alert to ALERT_WEBHOOK_URL if configured."""
    if not ALERT_WEBHOOK_URL:
        return

    payload = {
        "text": f":rotating_light: *APEX KILL SWITCH ENGAGED* :rotating_light:\n{reason}",
        "attachments": [
            {
                "color": "#FF0000",
                "fields": [
                    {"title": k, "value": str(v), "short": True}
                    for k, v in metadata.items()
                ],
                "footer": "APEX Circuit Breaker",
                "ts": int(time.time()),
            }
        ],
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(ALERT_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        log.info("Webhook alert sent to %s", ALERT_WEBHOOK_URL)
    except Exception as exc:
        log.error("Webhook alert failed: %s", exc)


# ─── Circuit breaker state ────────────────────────────────────────────────────

class DrawdownMonitor:
    """
    Tracks intraday equity high-water mark and computes current drawdown.

    The session high is reset on each new calendar day (UTC).  Drawdown is
    computed as:

        drawdown = (session_high - current_equity) / session_high

    The kill switch fires when drawdown ≥ DRAWDOWN_THRESHOLD_PCT.
    """

    def __init__(self) -> None:
        self.session_high:        float = 0.0
        self.session_date:        str   = ""
        self.consecutive_failures: int  = 0
        self.poll_count:          int   = 0
        self.kill_switch_engaged: bool  = False

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_reset_session(self, equity: float) -> None:
        today = self._today_utc()
        if today != self.session_date:
            log.info(
                "New trading day %s — resetting session high-water mark "
                "(previous: $%.2f)", today, self.session_high,
            )
            self.session_high  = equity
            self.session_date  = today

    def update(self, equity: float) -> Optional[float]:
        """
        Update state with new equity reading.
        Returns current drawdown fraction, or None if first reading.
        """
        self._maybe_reset_session(equity)

        if equity > self.session_high:
            self.session_high = equity

        if self.session_high <= 0:
            return None

        drawdown = (self.session_high - equity) / self.session_high
        return drawdown

    def record_alpaca_failure(self) -> int:
        self.consecutive_failures += 1
        return self.consecutive_failures

    def record_alpaca_success(self) -> None:
        self.consecutive_failures = 0


# ─── Main loop ────────────────────────────────────────────────────────────────

def validate_startup() -> None:
    """Fail fast if required env vars are missing."""
    missing = []
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"):
        if not os.getenv(var, "").strip():
            missing.append(var)
    if missing:
        log.critical("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)

    log.info(
        "Circuit breaker starting — "
        "threshold=%.1f%%, interval=%ds, redis=%s:%d, key=%s",
        DRAWDOWN_THRESHOLD_PCT * 100,
        CHECK_INTERVAL_SECONDS,
        REDIS_HOST, REDIS_PORT,
        KILL_SWITCH_REDIS_KEY,
    )


def run() -> int:
    validate_startup()

    # Connect to Redis — if this fails, we cannot guard the kill switch.
    try:
        r = connect_redis()
        log.info("Redis connected at %s:%d", REDIS_HOST, REDIS_PORT)
    except redis.ConnectionError as exc:
        log.critical("Cannot connect to Redis: %s — circuit breaker cannot start", exc)
        return 1

    # Check if kill switch is already active
    if is_kill_switch_active(r):
        log.warning(
            "Kill switch is ALREADY active in Redis (key=%s). "
            "Monitoring will continue but no new orders will be placed.",
            KILL_SWITCH_REDIS_KEY,
        )

    monitor  = DrawdownMonitor()
    running  = True

    def _handle_signal(signum, frame):
        nonlocal running
        log.info("Signal %s received — shutting down cleanly", signum)
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("Drawdown monitor active — polling every %ds", CHECK_INTERVAL_SECONDS)

    while running:
        loop_start = time.monotonic()
        monitor.poll_count += 1

        # ── Fetch equity ─────────────────────────────────────────────────────
        try:
            equity = fetch_equity()
            monitor.record_alpaca_success()

        except httpx.HTTPStatusError as exc:
            failures = monitor.record_alpaca_failure()
            log.error(
                "Alpaca HTTP error (consecutive_failures=%d/%d): %s",
                failures, ALPACA_RETRY_LIMIT, exc,
            )
            if failures >= ALPACA_RETRY_LIMIT and not monitor.kill_switch_engaged:
                reason = (
                    f"Alpaca unreachable for {failures} consecutive polls "
                    "— fail-closed: engaging kill switch"
                )
                engage_kill_switch(r, reason, {
                    "consecutive_failures": failures,
                    "alpaca_url": ALPACA_BASE_URL,
                })
                send_webhook_alert(reason, {"consecutive_failures": failures})
                monitor.kill_switch_engaged = True
            _sleep_remaining(loop_start, CHECK_INTERVAL_SECONDS)
            continue

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            failures = monitor.record_alpaca_failure()
            log.error(
                "Alpaca connection error (consecutive_failures=%d/%d): %s",
                failures, ALPACA_RETRY_LIMIT, exc,
            )
            if failures >= ALPACA_RETRY_LIMIT and not monitor.kill_switch_engaged:
                reason = (
                    f"Alpaca unreachable for {failures} consecutive polls "
                    "(connection error) — fail-closed"
                )
                engage_kill_switch(r, reason, {
                    "consecutive_failures": failures,
                    "error": str(exc),
                })
                send_webhook_alert(reason, {"consecutive_failures": failures})
                monitor.kill_switch_engaged = True
            _sleep_remaining(loop_start, CHECK_INTERVAL_SECONDS)
            continue

        # ── Compute drawdown ──────────────────────────────────────────────────
        drawdown = monitor.update(equity)

        if drawdown is None:
            log.info(
                "poll=%d  equity=$%.2f  (initialising session high-water mark)",
                monitor.poll_count, equity,
            )
            _sleep_remaining(loop_start, CHECK_INTERVAL_SECONDS)
            continue

        drawdown_pct = drawdown * 100
        log.info(
            "poll=%d  equity=$%.2f  high=$%.2f  drawdown=%.2f%%  threshold=%.1f%%",
            monitor.poll_count,
            equity,
            monitor.session_high,
            drawdown_pct,
            DRAWDOWN_THRESHOLD_PCT * 100,
        )

        # ── Warn at 80 % of threshold ─────────────────────────────────────────
        warn_threshold = DRAWDOWN_THRESHOLD_PCT * 0.80
        if drawdown >= warn_threshold and not monitor.kill_switch_engaged:
            log.warning(
                "DRAWDOWN WARNING: %.2f%% — approaching %.1f%% threshold "
                "(80%% alert level breached)",
                drawdown_pct, DRAWDOWN_THRESHOLD_PCT * 100,
            )

        # ── Engage kill switch ────────────────────────────────────────────────
        if drawdown >= DRAWDOWN_THRESHOLD_PCT and not monitor.kill_switch_engaged:
            positions = fetch_positions()
            pos_summary = [
                {"symbol": p["symbol"], "unrealized_pl": p.get("unrealized_pl")}
                for p in positions
            ]
            metadata = {
                "equity":          round(equity, 2),
                "session_high":    round(monitor.session_high, 2),
                "drawdown_pct":    round(drawdown_pct, 4),
                "threshold_pct":   DRAWDOWN_THRESHOLD_PCT * 100,
                "poll_count":      monitor.poll_count,
                "open_positions":  len(pos_summary),
                "positions":       pos_summary,
                "session_date":    monitor.session_date,
            }

            reason = (
                f"Portfolio drawdown {drawdown_pct:.2f}% exceeded "
                f"{DRAWDOWN_THRESHOLD_PCT * 100:.1f}% threshold "
                f"(equity=${equity:.2f}, session_high=${monitor.session_high:.2f})"
            )

            # Latch the kill switch — never resets automatically
            engage_kill_switch(r, reason, metadata)
            send_webhook_alert(reason, metadata)
            monitor.kill_switch_engaged = True

            # Continue monitoring (log ongoing state) but don't re-trigger
            log.critical(
                "HALT. Open positions: %d. "
                "Manual intervention required — run: "
                "redis-cli SET %s false",
                len(pos_summary), KILL_SWITCH_REDIS_KEY,
            )

        # Re-verify Redis connectivity periodically (every 10 polls)
        if monitor.poll_count % 10 == 0:
            try:
                r.ping()
            except redis.ConnectionError as exc:
                log.critical(
                    "Redis connectivity lost: %s — "
                    "kill switch cannot be written if drawdown occurs!",
                    exc,
                )
                # Attempt to reconnect
                try:
                    r = connect_redis()
                    log.info("Redis reconnected")
                except redis.ConnectionError:
                    log.critical(
                        "Redis reconnect failed — "
                        "circuit breaker is UNGUARDED until Redis recovers"
                    )

        _sleep_remaining(loop_start, CHECK_INTERVAL_SECONDS)

    log.info("Circuit breaker stopped cleanly")
    return 0


def _sleep_remaining(loop_start: float, interval: float) -> None:
    """Sleep for the remainder of the poll interval."""
    elapsed = time.monotonic() - loop_start
    remaining = interval - elapsed
    if remaining > 0:
        time.sleep(remaining)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(run())
