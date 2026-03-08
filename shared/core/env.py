"""
shared/core/env.py — Required environment variable loader

Every secret (API keys, passwords) must be present at service startup.
Fails with a clear EnvironmentError rather than running with empty credentials.

Usage
-----
from shared.core.env import require_env, optional_env

ALPACA_API_KEY = require_env("ALPACA_API_KEY")          # raises if missing/empty
KAFKA_SERVERS  = optional_env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")  # default ok
"""

from __future__ import annotations

import os
import sys


def require_env(name: str) -> str:
    """
    Return the value of *name* from the environment.

    Raises EnvironmentError immediately if the variable is absent or empty.
    This short-circuits service startup rather than failing mid-operation
    with a cryptic auth error.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is missing or empty.\n"
            f"  • In Docker/K8s: mount it as a Secret and reference via secretKeyRef\n"
            f"  • In local dev: add it to infra/.env and run: set -a && source infra/.env && set +a\n"
            f"  • See infra/.env for the full list of required vars"
        )
    return value


def optional_env(name: str, default: str = "") -> str:
    """Return the value of *name* or *default* if not set."""
    return os.environ.get(name, default)


# ─── Validate all secrets at import time (used by services) ──────────────────

KNOWN_REQUIRED_SECRETS: tuple[str, ...] = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    # TIMESCALEDB_PASSWORD is the canonical name sealed via K8s SealedSecrets.
    # POSTGRES_PASSWORD is accepted as a fallback for docker-compose local dev.
    "TIMESCALEDB_PASSWORD",
    # ANTHROPIC_API_KEY is OPTIONAL — LLM signals use local Ollama by default.
    # POLYGON_API_KEY is OPTIONAL — all data ingestion uses Alpaca exclusively.
)


def assert_secrets_present(names: tuple[str, ...] | list[str]) -> None:
    """
    Called once at service startup to validate a specific list of secrets.
    Collects all missing variables before raising so the user sees them
    all at once.
    """
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        joined = ", ".join(f"'{m}'" for m in missing)
        raise EnvironmentError(
            f"Service cannot start — required secrets not found: {joined}\n"
            f"See infra/.env for the full list."
        )
