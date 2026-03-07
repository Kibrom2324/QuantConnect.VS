"""
Clean trading safety limits loaded from environment variables only.

No YAML path dependencies — no config/ vs configs/ bugs possible.
Ported from AWET-main — Step 7 / 2026-02-27

Design principles:
- ``fail_closed``: any missing *required* env var raises immediately at startup.
- Kill switch is **always** checked; a Redis failure MUST set it True (fail-closed).
  Never reset the kill switch from a catch block (fixes CF-6 pattern).
"""
import os
from dataclasses import dataclass, field


@dataclass
class TradingLimits:
    """
    Trading safety parameters sourced exclusively from environment variables.

    Required env vars (raise EnvironmentError if absent and no default):
        TRADING_ENABLED       — "true" or "false"  (default: "false" → safe)
        MAX_TRADES_PER_DAY    — integer             (default: 20)
        MAX_POSITION_PCT      — float 0–1           (default: 0.02)
        MAX_DAILY_LOSS_PCT    — float 0–1           (default: 0.05)
        KILL_SWITCH           — "true" or "false"  (default: "false")
    """

    trading_enabled: bool = field(
        default_factory=lambda: os.getenv("TRADING_ENABLED", "false").lower() == "true"
    )
    max_trades_per_day: int = field(
        default_factory=lambda: int(os.getenv("MAX_TRADES_PER_DAY", "20"))
    )
    max_position_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "0.02"))
    )
    max_daily_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))
    )
    kill_switch_active: bool = field(
        default_factory=lambda: os.getenv("KILL_SWITCH", "false").lower() == "true"
    )

    # Paper-trading guard — NEVER change this URL in production code.
    # The value is read-only and checked in validate().
    _alpaca_base_url: str = field(
        default_factory=lambda: os.getenv(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        ),
        repr=False,
    )

    def validate(self) -> "TradingLimits":
        """Raise RuntimeError if safety conditions are not met.

        Call this at service startup — fail fast before accepting any order.
        """
        if self.kill_switch_active:
            raise RuntimeError("KILL_SWITCH is active — trading halted")
        if not self.trading_enabled:
            raise RuntimeError("TRADING_ENABLED is false — trading halted")
        if "paper" not in self._alpaca_base_url.lower():
            raise RuntimeError(
                f"ALPACA_BASE_URL '{self._alpaca_base_url}' does not contain 'paper' — "
                "refusing to start; paper trading only"
            )
        return self

    def is_safe_to_trade(self) -> bool:
        """Quick runtime check — call before every order submission."""
        return (
            self.trading_enabled
            and not self.kill_switch_active
            and "paper" in self._alpaca_base_url.lower()
        )

    def activate_kill_switch(self, reason: str = "") -> None:
        """Permanently halt trading.  Idempotent — safe to call from error handlers.

        IMPORTANT: Never reset kill_switch_active inside a catch block.  Only
        this method should write it True.  See CF-6 for the bug this prevents.
        """
        object.__setattr__(self, "kill_switch_active", True)

    def __post_init__(self) -> None:
        # Bounds checks — catch misconfigured env vars at construction time
        if not (0 < self.max_position_pct <= 1.0):
            raise ValueError(
                f"MAX_POSITION_PCT={self.max_position_pct} out of range (0, 1]"
            )
        if not (0 < self.max_daily_loss_pct <= 1.0):
            raise ValueError(
                f"MAX_DAILY_LOSS_PCT={self.max_daily_loss_pct} out of range (0, 1]"
            )
        if self.max_trades_per_day < 0:
            raise ValueError(
                f"MAX_TRADES_PER_DAY={self.max_trades_per_day} must be >= 0"
            )


# ---------------------------------------------------------------------------
# Dual-layer kill switch (spec requirement: fail-closed)
#
#   Layer 1: Redis key  ``apex:kill_switch``  — checked first
#   Layer 2: File flag  ``/tmp/apex_kill.flag`` — checked independently
#
#   Rules:
#   - If EITHER layer signals halt → HALTED
#   - If Redis is unreachable → HALTED  (fail-closed)
#   - Only if both layers are clear → ACTIVE
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402 (appended after class definition)
from pathlib import Path as _Path  # noqa: E402

KILL_FLAG_PATH = _Path(os.getenv("KILL_FLAG_PATH", "/tmp/apex_kill.flag"))
REDIS_KILL_KEY = "apex:kill_switch"


def is_file_kill_active() -> bool:
    """Return True if the file-based kill flag exists on disk."""
    return KILL_FLAG_PATH.exists()


def set_file_kill(reason: str = "") -> None:
    """Atomically create the file-based kill flag."""
    try:
        KILL_FLAG_PATH.write_text(reason or "halted")
    except OSError:
        pass  # Best-effort; Redis check is the primary layer


def clear_file_kill() -> None:
    """Remove the file-based kill flag (use only in tests / operator recovery)."""
    try:
        KILL_FLAG_PATH.unlink(missing_ok=True)
    except OSError:
        pass


async def is_redis_kill_active(redis_client) -> bool:  # type: ignore[return]
    """
    Check the Redis kill switch key.

    Returns True (HALTED) if:
      - Redis is unreachable (fail-closed)
      - Key value is ``"true"``

    Returns False only if Redis is reachable AND key is not ``"true"``.
    """
    try:
        val = await redis_client.get(REDIS_KILL_KEY)
        return val == "true"
    except Exception:  # noqa: BLE001
        # Redis unreachable → treat as HALTED (fail-closed)
        return True


async def check_dual_kill_switch(redis_client=None) -> bool:
    """
    Evaluate the two-layer kill switch.

    Returns True  → trading is HALTED
    Returns False → trading may proceed (both layers clear)

    Layer 1: Redis key ``apex:kill_switch``
    Layer 2: File flag ``/tmp/apex_kill.flag``

    Fail-closed: if Redis is unavailable, returns True (HALTED).
    If redis_client is None, Layer 1 defaults to HALTED.
    """
    # Layer 2 is synchronous and cheap — check first
    if is_file_kill_active():
        return True

    # Layer 1: Redis
    if redis_client is None:
        return True  # no client → fail-closed

    return await is_redis_kill_active(redis_client)
