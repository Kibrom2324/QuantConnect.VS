#!/usr/bin/env python3
"""
APEX Live Trading Go/No-Go Validator
scripts/go_live_validator.py

Run this script BEFORE switching to live capital.  It performs a structured
pre-flight check and prints a GO / NO-GO decision.  All 10 checks must pass.

Exit codes
──────────
  0   — GO: all checks passed
  1   — NO-GO: one or more checks failed
  2   — Usage / environment error

Usage
─────
  python scripts/go_live_validator.py
  python scripts/go_live_validator.py --strict          # exit 1 on any WARNING too
  python scripts/go_live_validator.py --json            # machine-readable output

Design rules
────────────
- ALL credentials come from env vars via os.getenv() — nothing is hardcoded.
- Every check is independent: one failure does not prevent remaining checks.
- The kill switch is read from Redis (canonical source); env var is secondary.
- TimescaleDB connectivity is verified with a real TCP + SQL probe.
- The final verdict is conservative: any FAIL → NO-GO.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import httpx
import redis

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("go_live_validator")

# ─── Result model ─────────────────────────────────────────────────────────────

PASS    = "PASS"
FAIL    = "FAIL"
WARN    = "WARN"
SKIP    = "SKIP"

@dataclass
class CheckResult:
    name:    str
    status:  str          # PASS | FAIL | WARN | SKIP
    detail:  str = ""
    elapsed: float = 0.0  # seconds


@dataclass
class ValidationReport:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks:    list[CheckResult] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(c.status == FAIL for c in self.checks):
            return "NO-GO"
        return "GO"

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == WARN)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "verdict":   self.verdict,
            "fail_count": self.fail_count,
            "warn_count": self.warn_count,
            "checks": [
                {
                    "name":    c.name,
                    "status":  c.status,
                    "detail":  c.detail,
                    "elapsed_ms": round(c.elapsed * 1000),
                }
                for c in self.checks
            ],
        }


# ─── Check runner ─────────────────────────────────────────────────────────────

def run_check(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    """Execute one check function, catching all exceptions as FAIL."""
    t0 = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        result = CheckResult(name=name, status=FAIL, detail=f"Exception: {exc}")
    result.elapsed = time.monotonic() - t0
    result.name = name
    return result


# ─── Individual checks ────────────────────────────────────────────────────────

LIVE_URL = "https://api.alpaca.markets"
PAPER_URL_FRAGMENT = "paper-api.alpaca.markets"

# Required env vars with (description, fatal) tuples.
# fatal=True → FAIL if missing; False → WARN only
REQUIRED_VARS: list[tuple[str, str, bool]] = [
    ("ALPACA_API_KEY",        "Alpaca live API key",              True),
    ("ALPACA_SECRET_KEY",     "Alpaca live secret key",           True),
    ("ALPACA_BASE_URL",       "Alpaca base URL (must be live)",   True),
    ("POSTGRES_PASSWORD",     "TimescaleDB password",             True),
    ("TIMESCALEDB_PASSWORD",  "TimescaleDB password (canonical)", True),
    ("DATABASE_URL",          "Full PostgreSQL connection URL",   True),
    ("REDIS_HOST",            "Redis host",                       False),
    ("REDIS_PORT",            "Redis port",                       False),
    ("ANTHROPIC_API_KEY",     "Anthropic API key (LLM signals)",  False),
    ("POLYGON_API_KEY",       "Polygon.io key (optional — data uses Alpaca)", False),
]


def check_live_url() -> CheckResult:
    """ALPACA_BASE_URL must point to the live endpoint."""
    url = os.getenv("ALPACA_BASE_URL", "")
    if not url:
        return CheckResult(
            name="alpaca_url",
            status=FAIL,
            detail="ALPACA_BASE_URL is not set",
        )
    if PAPER_URL_FRAGMENT in url:
        return CheckResult(
            name="alpaca_url",
            status=FAIL,
            detail=(
                f"ALPACA_BASE_URL='{url}' points to the PAPER endpoint. "
                "Set it to https://api.alpaca.markets for live trading."
            ),
        )
    if url.rstrip("/") != LIVE_URL.rstrip("/"):
        return CheckResult(
            name="alpaca_url",
            status=WARN,
            detail=(
                f"ALPACA_BASE_URL='{url}' is not the standard live URL "
                f"({LIVE_URL}).  Proceeding with caution."
            ),
        )
    return CheckResult(
        name="alpaca_url",
        status=PASS,
        detail=f"Live endpoint confirmed: {url}",
    )


def check_required_env_vars() -> CheckResult:
    """All required environment variables must be present and non-empty."""
    missing_fatal: list[str] = []
    missing_warn:  list[str] = []

    for var, desc, fatal in REQUIRED_VARS:
        val = os.getenv(var, "").strip()
        if not val or val.startswith("your-") or val.startswith("change-me"):
            if fatal:
                missing_fatal.append(f"{var} ({desc})")
            else:
                missing_warn.append(f"{var} ({desc})")

    if missing_fatal:
        return CheckResult(
            name="env_vars",
            status=FAIL,
            detail="Missing or placeholder values: " + ", ".join(missing_fatal),
        )
    if missing_warn:
        return CheckResult(
            name="env_vars",
            status=WARN,
            detail="Optional vars not set: " + ", ".join(missing_warn),
        )
    return CheckResult(
        name="env_vars",
        status=PASS,
        detail=f"All {len(REQUIRED_VARS)} required env vars are set",
    )


def check_alpaca_credentials() -> CheckResult:
    """Verify Alpaca credentials work against the live endpoint."""
    url    = os.getenv("ALPACA_BASE_URL", LIVE_URL).rstrip("/")
    key    = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")

    if not key or not secret:
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail="ALPACA_API_KEY or ALPACA_SECRET_KEY is not set",
        )

    headers = {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{url}/v2/account", headers=headers)
    except httpx.ConnectError as exc:
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail=f"Cannot reach Alpaca at {url}: {exc}",
        )

    if resp.status_code == 401:
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail="Alpaca returned 401 Unauthorized — check API key / secret",
        )
    if resp.status_code == 403:
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail=(
                "Alpaca returned 403 Forbidden — key may be paper-only "
                "or account not approved for live trading"
            ),
        )
    if resp.status_code != 200:
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail=f"Alpaca returned HTTP {resp.status_code}: {resp.text[:200]}",
        )

    data = resp.json()
    account_status = data.get("status", "unknown")
    pattern_day_trader = data.get("pattern_day_trader", False)
    equity = float(data.get("equity", 0))

    detail = (
        f"Account status={account_status}, "
        f"equity=${equity:,.2f}, "
        f"PDT={pattern_day_trader}"
    )

    if account_status != "ACTIVE":
        return CheckResult(
            name="alpaca_credentials",
            status=FAIL,
            detail=f"Alpaca account is not ACTIVE: {detail}",
        )

    return CheckResult(
        name="alpaca_credentials",
        status=PASS,
        detail=detail,
    )


def check_kill_switch_off() -> CheckResult:
    """
    Kill switch must be OFF before starting live trading.
    Checks Redis first (canonical source), then falls back to env var.
    """
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_pass = os.getenv("REDIS_PASSWORD", "") or None
    kill_key   = "apex:kill_switch"

    try:
        r = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_pass,
            socket_timeout=5.0,
            decode_responses=True,
        )
        r.ping()
        raw = r.get(kill_key)

        if raw is None:
            return CheckResult(
                name="kill_switch",
                status=PASS,
                detail=f"Redis key '{kill_key}' is not set (kill switch is OFF)",
            )

        is_active = raw.strip().lower() in ("true", "1", "on", "yes")
        if is_active:
            return CheckResult(
                name="kill_switch",
                status=FAIL,
                detail=(
                    f"Kill switch is ACTIVE in Redis (key='{kill_key}', value='{raw}'). "
                    "Reset manually: redis-cli SET apex:kill_switch false"
                ),
            )
        return CheckResult(
            name="kill_switch",
            status=PASS,
            detail=f"Kill switch is OFF in Redis (value='{raw}')",
        )

    except redis.ConnectionError as exc:
        # Redis unavailable — fall back to env var check
        env_val = os.getenv("KILL_SWITCH", "false").lower()
        if env_val in ("true", "1"):
            return CheckResult(
                name="kill_switch",
                status=FAIL,
                detail=f"Redis unreachable AND KILL_SWITCH env var is '{env_val}'",
            )
        return CheckResult(
            name="kill_switch",
            status=WARN,
            detail=(
                f"Redis unreachable ({exc}); KILL_SWITCH env var is '{env_val}' "
                "(assumed OFF — start Redis before going live)"
            ),
        )


def check_timescaledb() -> CheckResult:
    """Verify TimescaleDB TCP reachability and SQL connectivity."""
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        # Build from components
        host     = os.getenv("TIMESCALEDB_HOST", "localhost")
        port     = int(os.getenv("TIMESCALEDB_PORT", "5432"))
        user     = os.getenv("POSTGRES_USER", "apex")
        password = os.getenv("TIMESCALEDB_PASSWORD") or os.getenv("POSTGRES_PASSWORD", "")
        dbname   = os.getenv("POSTGRES_DB", "apexdb")
        database_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

    # Parse host and port from URL for TCP probe
    try:
        from urllib.parse import urlparse
        parsed = urlparse(database_url)
        host   = parsed.hostname or "localhost"
        port   = parsed.port or 5432
    except Exception:
        host, port = "localhost", 5432

    # TCP probe first
    try:
        sock = socket.create_connection((host, port), timeout=5.0)
        sock.close()
    except OSError as exc:
        return CheckResult(
            name="timescaledb",
            status=FAIL,
            detail=f"Cannot reach TimescaleDB at {host}:{port}: {exc}",
        )

    # SQL probe — requires psycopg2
    try:
        import psycopg2

        conn = psycopg2.connect(database_url, connect_timeout=10)
        cur  = conn.cursor()

        # Verify TimescaleDB extension
        cur.execute(
            "SELECT extname, extversion "
            "FROM pg_extension WHERE extname = 'timescaledb';"
        )
        row = cur.fetchone()

        # Verify live schema exists
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name = 'live';"
        )
        live_schema = cur.fetchone()

        cur.close()
        conn.close()

        if not row:
            return CheckResult(
                name="timescaledb",
                status=FAIL,
                detail="TimescaleDB extension is NOT loaded in database",
            )

        ext_version = row[1]
        schema_note = "live schema exists" if live_schema else "live schema NOT found (run init.sql)"
        status = PASS if live_schema else WARN

        return CheckResult(
            name="timescaledb",
            status=status,
            detail=f"TimescaleDB {ext_version} at {host}:{port} — {schema_note}",
        )

    except ImportError:
        # psycopg2 not installed — TCP passed, that's enough for a WARN
        return CheckResult(
            name="timescaledb",
            status=WARN,
            detail=(
                f"TCP reachable at {host}:{port} but psycopg2 not installed "
                "— install it to verify SQL connectivity"
            ),
        )
    except Exception as exc:
        return CheckResult(
            name="timescaledb",
            status=FAIL,
            detail=f"SQL probe failed: {exc}",
        )


def check_redis_connectivity() -> CheckResult:
    """Verify Redis is reachable and AOF persistence is enabled."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "") or None

    try:
        r = redis.Redis(
            host=host, port=port, password=password,
            socket_timeout=5.0, decode_responses=True,
        )
        r.ping()
        # Check AOF is enabled (required for kill-switch durability)
        aof_info = r.config_get("appendonly")
        aof_enabled = aof_info.get("appendonly", "no") == "yes"
        if not aof_enabled:
            return CheckResult(
                name="redis",
                status=WARN,
                detail=(
                    f"Redis at {host}:{port} is reachable but AOF is DISABLED. "
                    "Kill switch state will not survive a restart."
                ),
            )
        return CheckResult(
            name="redis",
            status=PASS,
            detail=f"Redis at {host}:{port} — PONG, AOF enabled",
        )
    except redis.ConnectionError as exc:
        return CheckResult(
            name="redis",
            status=FAIL,
            detail=f"Cannot reach Redis at {host}:{port}: {exc}",
        )
    except redis.AuthenticationError as exc:
        return CheckResult(
            name="redis",
            status=FAIL,
            detail=f"Redis authentication failed at {host}:{port}: {exc}",
        )


def check_polygon_api() -> CheckResult:
    """Polygon is OPTIONAL — all historical and live data uses Alpaca exclusively.
    This check only warns if the key is present but invalid.
    """
    api_key = os.getenv("POLYGON_API_KEY", "")
    if not api_key or api_key.startswith("your-"):
        return CheckResult(
            name="polygon_api",
            status=WARN,
            detail="POLYGON_API_KEY not set — OK, data ingestion uses Alpaca exclusively",
        )
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.polygon.io/v2/aggs/ticker/AAPL/prev",
                params={"apiKey": api_key},
            )
        if resp.status_code == 200:
            return CheckResult(
                name="polygon_api",
                status=PASS,
                detail="Polygon.io API key is valid (present and working, though not used for data ingestion)",
            )
        if resp.status_code == 403:
            return CheckResult(
                name="polygon_api",
                status=WARN,
                detail="Polygon.io returned 403 — key invalid, but not used for data ingestion (Alpaca handles all data)",
            )
        return CheckResult(
            name="polygon_api",
            status=WARN,
            detail=f"Polygon.io returned HTTP {resp.status_code} — not critical, data ingestion uses Alpaca",
        )
    except httpx.ConnectError as exc:
        return CheckResult(
            name="polygon_api",
            status=WARN,
            detail=f"Cannot reach Polygon.io: {exc} — not critical, data ingestion uses Alpaca exclusively",
        )


def check_no_paper_keys() -> CheckResult:
    """
    Heuristic: Alpaca paper keys typically begin with 'PK'.
    Live keys begin with a different prefix. Warn if paper-style key detected.
    """
    key = os.getenv("ALPACA_API_KEY", "")
    if key.startswith("PK"):
        return CheckResult(
            name="key_type_heuristic",
            status=WARN,
            detail=(
                f"ALPACA_API_KEY starts with 'PK' — this looks like a paper key. "
                "Live keys typically start with a different prefix. "
                "Confirm you are using live credentials."
            ),
        )
    if not key:
        return CheckResult(
            name="key_type_heuristic",
            status=SKIP,
            detail="ALPACA_API_KEY not set (covered by env_vars check)",
        )
    return CheckResult(
        name="key_type_heuristic",
        status=PASS,
        detail="Key prefix does not match known paper key pattern",
    )


def check_position_limits_config() -> CheckResult:
    """live_trading.yaml must exist and have tighter limits than paper."""
    from pathlib import Path

    ws   = Path(__file__).resolve().parent.parent
    cfg  = ws / "configs" / "live_trading.yaml"

    if not cfg.exists():
        return CheckResult(
            name="live_config",
            status=FAIL,
            detail=f"configs/live_trading.yaml not found at {cfg}",
        )

    try:
        import yaml
        with cfg.open() as f:
            data = yaml.safe_load(f)

        env   = data.get("app", {}).get("environment", "")
        limit = data.get("risk", {}).get("max_position_pct", 1.0)
        daily = data.get("risk", {}).get("daily_loss", {}).get("limit_pct", 1.0)

        issues = []
        if env != "live":
            issues.append(f"app.environment='{env}' (must be 'live')")
        if limit > 0.01:
            issues.append(f"max_position_pct={limit} > 1% limit for live trading")
        if daily > 0.02:
            issues.append(f"daily_loss.limit_pct={daily} > 2% limit for live trading")

        if issues:
            return CheckResult(
                name="live_config",
                status=FAIL,
                detail="Config issues: " + "; ".join(issues),
            )

        return CheckResult(
            name="live_config",
            status=PASS,
            detail=(
                f"configs/live_trading.yaml — environment={env}, "
                f"max_position={limit*100:.1f}%, "
                f"daily_loss_limit={daily*100:.1f}%"
            ),
        )
    except Exception as exc:
        return CheckResult(
            name="live_config",
            status=FAIL,
            detail=f"Cannot parse configs/live_trading.yaml: {exc}",
        )


def check_drawdown_monitor_running() -> CheckResult:
    """Verify scripts/circuit_breaker.py process is active."""
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", "circuit_breaker.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split()
            return CheckResult(
                name="circuit_breaker",
                status=PASS,
                detail=f"circuit_breaker.py is running (PID(s): {', '.join(pids)})",
            )
        return CheckResult(
            name="circuit_breaker",
            status=FAIL,
            detail=(
                "circuit_breaker.py is NOT running. "
                "Start it before going live: "
                "python scripts/circuit_breaker.py &"
            ),
        )
    except FileNotFoundError:
        return CheckResult(
            name="circuit_breaker",
            status=WARN,
            detail="pgrep not available — cannot verify circuit_breaker.py is running",
        )


def check_paper_trading_stopped() -> CheckResult:
    """
    Verify no paper-mode services are running (check for paper topic consumers).
    Looks for Docker containers with 'paper' in their name.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=paper", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                name="paper_services_stopped",
                status=SKIP,
                detail="Docker not available — cannot verify paper services are stopped",
            )
        containers = [c for c in result.stdout.strip().split("\n") if c]
        if containers:
            return CheckResult(
                name="paper_services_stopped",
                status=WARN,
                detail=(
                    f"Paper-mode containers still running: {containers}. "
                    "Stop them before starting live trading to avoid duplicate orders."
                ),
            )
        return CheckResult(
            name="paper_services_stopped",
            status=PASS,
            detail="No paper-mode containers detected",
        )
    except Exception as exc:
        return CheckResult(
            name="paper_services_stopped",
            status=SKIP,
            detail=f"Could not check Docker: {exc}",
        )


# ─── All checks registry ──────────────────────────────────────────────────────

CHECKS: list[tuple[str, Callable[[], CheckResult]]] = [
    ("Alpaca live URL",              check_live_url),
    ("Required env vars",            check_required_env_vars),
    ("Alpaca credentials",           check_alpaca_credentials),
    ("Kill switch is OFF",           check_kill_switch_off),
    ("TimescaleDB connectivity",     check_timescaledb),
    ("Redis connectivity",           check_redis_connectivity),
    ("Polygon.io API key",           check_polygon_api),
    ("Key type heuristic",           check_no_paper_keys),
    ("Live config limits",           check_position_limits_config),
    ("Circuit breaker running",      check_drawdown_monitor_running),
    # ("Paper services stopped",     check_paper_trading_stopped),  # uncomment if needed
]


# ─── Output formatters ────────────────────────────────────────────────────────

STATUS_ICONS = {
    PASS: "\033[0;32m✓ PASS\033[0m",
    FAIL: "\033[0;31m✗ FAIL\033[0m",
    WARN: "\033[0;33m⚠ WARN\033[0m",
    SKIP: "\033[0;34m- SKIP\033[0m",
}

VERDICT_GO    = "\033[1;32m  ██████╗  ██████╗ \n  ██╔════╝ ██╔═══██╗\n  ██║  ███╗██║   ██║\n  ██║   ██║██║   ██║\n  ╚██████╔╝╚██████╔╝\n   ╚═════╝  ╚═════╝ \033[0m"
VERDICT_NOGO  = "\033[1;31m  ███╗   ██╗ ██████╗      ██████╗  ██████╗ \n  ████╗  ██║██╔═══██╗    ██╔════╝ ██╔═══██╗\n  ██╔██╗ ██║██║   ██║    ██║  ███╗██║   ██║\n  ██║╚██╗██║██║   ██║    ██║   ██║██║   ██║\n  ██║ ╚████║╚██████╔╝    ╚██████╔╝╚██████╔╝\n  ╚═╝  ╚═══╝ ╚═════╝      ╚═════╝  ╚═════╝ \033[0m"


def print_report(report: ValidationReport, strict: bool = False) -> None:
    print()
    print("═" * 72)
    print("  APEX LIVE TRADING — GO / NO-GO VALIDATOR")
    print(f"  {report.timestamp}")
    print("═" * 72)

    for check in report.checks:
        icon = STATUS_ICONS.get(check.status, check.status)
        name = check.name.ljust(30)
        ms   = f"{check.elapsed * 1000:.0f}ms"
        print(f"  {icon}  {name}  ({ms})")
        if check.detail:
            print(f"         {check.detail}")

    print()
    print("─" * 72)
    print(f"  Checks: {len(report.checks)} total | "
          f"{report.fail_count} FAIL | {report.warn_count} WARN")

    if strict and report.warn_count > 0 and report.verdict == "GO":
        print("  (--strict mode: WARNs treated as FAIL)")
        print()
        print(VERDICT_NOGO)
    elif report.verdict == "GO":
        print()
        print(VERDICT_GO)
    else:
        print()
        print(VERDICT_NOGO)

    print()
    print("═" * 72)
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="APEX Live Trading Go/No-Go Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat WARNs as failures (exit 1)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON report to stdout instead of formatted text",
    )
    parser.add_argument(
        "--skip",
        nargs="*",
        default=[],
        metavar="CHECK_NAME",
        help="Skip checks by name (e.g. --skip alpaca_credentials polygon_api)",
    )
    args = parser.parse_args()

    report = ValidationReport()

    print(f"\nRunning {len(CHECKS)} pre-flight checks...", file=sys.stderr)

    for label, fn in CHECKS:
        result = run_check(label, fn)
        if args.skip and any(s in result.name for s in args.skip):
            result.status = SKIP
            result.detail = "Skipped by --skip flag"
        report.checks.append(result)
        status_char = result.status[0]
        print(f"  [{status_char}] {label}", file=sys.stderr)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report, strict=args.strict)

    # Determine exit code
    if report.verdict == "NO-GO":
        return 1
    if args.strict and report.warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
