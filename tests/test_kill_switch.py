"""
tests/test_kill_switch.py — Dual-layer kill switch tests.

Verifies the fail-closed behaviour of check_dual_kill_switch():
  1. Redis unavailable → HALTED
  2. Redis key == "true" → HALTED
  3. File flag exists → HALTED
  4. Both layers clear → ACTIVE (trading allowed)
  5. Both layers independently trigger (OR logic)
  6. TradingLimits.is_safe_to_trade() honours kill_switch_active flag

Run: pytest tests/test_kill_switch.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure shared/ is importable
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from shared.core.trading_safety import (
    TradingLimits,
    check_dual_kill_switch,
    clear_file_kill,
    is_file_kill_active,
    KILL_FLAG_PATH,
    set_file_kill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis(kill_value: str | None = None, raises: bool = False):
    """Return a mock async Redis client."""
    client = AsyncMock()
    if raises:
        client.get.side_effect = ConnectionError("Redis not available")
    else:
        client.get.return_value = kill_value
    return client


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixture: always clean up the kill flag file after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup_kill_flag():
    clear_file_kill()
    yield
    clear_file_kill()


# ===========================================================================
# Layer 1 — Redis
# ===========================================================================

class TestRedisKillSwitch:
    def test_redis_down_returns_halted(self):
        """Redis unreachable → check_dual_kill_switch returns True (HALTED)."""
        redis = _make_redis(raises=True)
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True, "Redis failure must result in HALTED (fail-closed)"

    def test_redis_key_true_returns_halted(self):
        """Redis key apex:kill_switch == 'true' → HALTED."""
        redis = _make_redis(kill_value="true")
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True

    def test_redis_key_false_returns_active(self):
        """Redis key apex:kill_switch == 'false' → NOT halted (when file flag absent)."""
        redis = _make_redis(kill_value="false")
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is False

    def test_redis_key_none_returns_active(self):
        """Redis key not set (None) → NOT halted."""
        redis = _make_redis(kill_value=None)
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is False

    def test_no_redis_client_returns_halted(self):
        """No Redis client provided → fail-closed → HALTED."""
        result = _run(check_dual_kill_switch(redis_client=None))
        assert result is True


# ===========================================================================
# Layer 2 — File flag
# ===========================================================================

class TestFileKillSwitch:
    def test_file_flag_present_returns_halted(self):
        """File /tmp/apex_kill.flag exists → HALTED regardless of Redis state."""
        redis = _make_redis(kill_value="false")  # Redis says OK
        set_file_kill("manual halt")

        assert KILL_FLAG_PATH.exists(), "Flag should have been created"
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True, "File flag should override Redis-OK and halt trading"

    def test_file_flag_absent_does_not_halt(self):
        """File flag absent + Redis clear → ACTIVE."""
        redis = _make_redis(kill_value=None)
        assert not KILL_FLAG_PATH.exists()
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is False

    def test_is_file_kill_active_helpers(self):
        """set_file_kill() and is_file_kill_active() work correctly."""
        assert not is_file_kill_active()
        set_file_kill("test")
        assert is_file_kill_active()
        clear_file_kill()
        assert not is_file_kill_active()


# ===========================================================================
# OR logic — either layer can halt
# ===========================================================================

class TestDualLayerOrLogic:
    def test_both_active_returns_halted(self):
        """Both layers active → HALTED."""
        redis = _make_redis(kill_value="true")
        set_file_kill()
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True

    def test_only_redis_active_returns_halted(self):
        """Only Redis layer active → HALTED."""
        redis = _make_redis(kill_value="true")
        assert not KILL_FLAG_PATH.exists()
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True

    def test_only_file_active_returns_halted(self):
        """Only file layer active → HALTED."""
        redis = _make_redis(kill_value="false")
        set_file_kill()
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is True

    def test_neither_active_returns_false(self):
        """Neither layer active → NOT halted (trading may proceed)."""
        redis = _make_redis(kill_value="false")
        assert not KILL_FLAG_PATH.exists()
        result = _run(check_dual_kill_switch(redis_client=redis))
        assert result is False


# ===========================================================================
# TradingLimits.is_safe_to_trade()
# ===========================================================================

class TestTradingLimits:
    def test_default_kill_switch_is_inactive(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "false")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        limits = TradingLimits()
        assert limits.is_safe_to_trade() is True

    def test_kill_switch_env_active(self, monkeypatch):
        monkeypatch.setenv("KILL_SWITCH", "true")
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        limits = TradingLimits()
        assert limits.is_safe_to_trade() is False

    def test_activate_kill_switch_method(self):
        limits = TradingLimits.__new__(TradingLimits)
        object.__setattr__(limits, "trading_enabled", True)
        object.__setattr__(limits, "kill_switch_active", False)
        object.__setattr__(limits, "max_trades_per_day", 20)
        object.__setattr__(limits, "max_position_pct", 0.02)
        object.__setattr__(limits, "max_daily_loss_pct", 0.05)
        object.__setattr__(limits, "_alpaca_base_url", "https://paper-api.alpaca.markets")
        limits.activate_kill_switch("test reason")
        assert limits.kill_switch_active is True
        assert limits.is_safe_to_trade() is False

    def test_non_paper_url_fails_safe(self, monkeypatch):
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "false")
        limits = TradingLimits()
        assert limits.is_safe_to_trade() is False

    def test_trading_disabled_fails_safe(self, monkeypatch):
        monkeypatch.setenv("TRADING_ENABLED", "false")
        monkeypatch.setenv("KILL_SWITCH", "false")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        limits = TradingLimits()
        assert limits.is_safe_to_trade() is False

    def test_validate_raises_when_kill_active(self, monkeypatch):
        monkeypatch.setenv("KILL_SWITCH", "true")
        monkeypatch.setenv("TRADING_ENABLED", "true")
        limits = TradingLimits()
        with pytest.raises(RuntimeError, match="KILL_SWITCH is active"):
            limits.validate()

    def test_validate_raises_when_trading_disabled(self, monkeypatch):
        monkeypatch.setenv("KILL_SWITCH", "false")
        monkeypatch.setenv("TRADING_ENABLED", "false")
        limits = TradingLimits()
        with pytest.raises(RuntimeError, match="TRADING_ENABLED is false"):
            limits.validate()

    def test_redis_failure_is_treated_as_halted_in_risk_engine(self):
        """
        Regression test for CF-6: risk engine must not silently continue
        when Redis raises an exception.  We verify this via the
        is_redis_kill_active helper which is used by check_dual_kill_switch.
        """
        from shared.core.trading_safety import is_redis_kill_active  # noqa: PLC0415
        redis = _make_redis(raises=True)
        result = _run(is_redis_kill_active(redis))
        assert result is True, "Redis failure must map to kill-active (fail-closed)"
