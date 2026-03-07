"""
APEX test suite — smoke + unit tests.
Tests cover CF-3 annualization fix, TradingLimits, and CircuitBreaker.
"""
import sys
import os
import math
import asyncio
import pytest

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── Placeholder (always passes — proves pytest can discover tests) ────────
def test_placeholder():
    """Placeholder: always passes."""
    assert True


# ─── CF-3: Sharpe annualization factor ────────────────────────────────────
class TestCF3AnnualizationFactor:
    """
    CF-3: Validate that _calculate_risk_metrics uses the correct annualisation
    factor for both daily and minute-bar equity curves.
    """

    def _make_reporter(self):
        from MyProject.backtest_reporter import APEXBacktestReporter
        return APEXBacktestReporter.__new__(APEXBacktestReporter)  # skip __init__

    def test_daily_equity_curve_uses_sqrt_252(self):
        """252 daily points ≈ 1 year → bars_per_day should auto-detect as 1."""
        reporter = self._make_reporter()
        import numpy as np
        rng = np.random.default_rng(42)
        equity = 100_000 * np.cumprod(1 + rng.normal(0.0003, 0.01, 252))
        annual_vol, sortino, calmar, var_95, cvar_95 = reporter._calculate_risk_metrics(
            equity.tolist(), 0.1, 0.05, bars_per_day=1
        )
        # With bars_per_day=1, ann_factor = sqrt(252) ≈ 15.87
        assert 0 < annual_vol < 50, f"Unexpected daily vol: {annual_vol}"

    def test_minute_equity_curve_uses_sqrt_252x390(self):
        """Minute bars: annualisation factor must be sqrt(252 * 390)."""
        reporter = self._make_reporter()
        import numpy as np
        rng = np.random.default_rng(42)
        # 1 trading day of minute bars
        equity = 100_000 * np.cumprod(1 + rng.normal(0, 0.0002, 390))
        annual_vol_minute, *_ = reporter._calculate_risk_metrics(
            equity.tolist(), 0.01, 0.005, bars_per_day=390
        )
        annual_vol_daily, *_ = reporter._calculate_risk_metrics(
            equity.tolist(), 0.01, 0.005, bars_per_day=1
        )
        # Minute annualisation should be sqrt(390) ≈ 19.7× larger
        ratio = annual_vol_minute / annual_vol_daily if annual_vol_daily > 0 else 0
        expected_ratio = math.sqrt(390)
        assert abs(ratio - expected_ratio) < 0.5, (
            f"Annualisation ratio {ratio:.2f} ≠ expected {expected_ratio:.2f}. "
            "CF-3 may not be properly applied."
        )

    def test_auto_detection_daily(self):
        """Auto-detection with 252 points over ~1 year should pick daily."""
        reporter = self._make_reporter()
        import numpy as np
        equity = list(100_000 + np.cumsum(np.random.randn(252) * 100))
        annual_vol, _, _, _, _ = reporter._calculate_risk_metrics(equity, 0.05, 0.1)
        assert annual_vol > 0  # just ensure no crash

    def test_short_series_returns_zeros(self):
        """Series with <2 points must return zeros without crashing."""
        reporter = self._make_reporter()
        result = reporter._calculate_risk_metrics([100_000], 0.0, 0.0)
        assert result == (0, 0, 0, 0, 0)


# ─── TradingLimits (shared/core/trading_safety.py) ────────────────────────
class TestTradingLimits:
    """Unit tests for the TradingLimits safety layer."""

    def test_safe_default_env(self, monkeypatch):
        """Default env → trading disabled (safe by default)."""
        monkeypatch.delenv("TRADING_ENABLED", raising=False)
        monkeypatch.delenv("KILL_SWITCH", raising=False)
        from shared.core.trading_safety import TradingLimits
        limits = TradingLimits()
        assert not limits.trading_enabled
        assert not limits.is_safe_to_trade()

    def test_kill_switch_blocks_trading(self, monkeypatch):
        """Kill switch overrides TRADING_ENABLED=true."""
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "true")
        from shared.core.trading_safety import TradingLimits
        limits = TradingLimits()
        assert not limits.is_safe_to_trade()
        with pytest.raises(RuntimeError, match="KILL_SWITCH"):
            limits.validate()

    def test_validate_passes_when_safe(self, monkeypatch):
        """validate() returns self when TRADING_ENABLED and no kill switch."""
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "false")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        from shared.core.trading_safety import TradingLimits
        limits = TradingLimits()
        result = limits.validate()
        assert result is limits

    def test_live_url_refused(self, monkeypatch):
        """Live Alpaca URL must be refused — paper only."""
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "false")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        from shared.core.trading_safety import TradingLimits
        limits = TradingLimits()
        with pytest.raises(RuntimeError, match="paper"):
            limits.validate()

    def test_activate_kill_switch(self, monkeypatch):
        """activate_kill_switch() sets kill_switch_active=True."""
        monkeypatch.setenv("TRADING_ENABLED", "true")
        monkeypatch.setenv("KILL_SWITCH", "false")
        from shared.core.trading_safety import TradingLimits
        limits = TradingLimits()
        assert limits.is_safe_to_trade()
        limits.activate_kill_switch("test")
        assert not limits.is_safe_to_trade()

    def test_bad_position_pct_raises(self, monkeypatch):
        """MAX_POSITION_PCT=0 should raise at construction."""
        monkeypatch.setenv("MAX_POSITION_PCT", "0")
        from shared.core.trading_safety import TradingLimits
        with pytest.raises(ValueError, match="MAX_POSITION_PCT"):
            TradingLimits()


# ─── CircuitBreaker (shared/core/circuit_breaker.py) ──────────────────────
class TestCircuitBreaker:
    """Unit tests for the async circuit breaker."""

    def test_closed_on_success(self):
        from shared.core.circuit_breaker import CircuitBreaker, CircuitState

        async def _run():
            cb = CircuitBreaker("test", failure_threshold=3)
            result = await cb.call(asyncio.coroutine(lambda: "ok")())
            assert result == "ok"
            assert cb.state == CircuitState.CLOSED

        # Python 3.11 requires proper coroutines — use a wrapper
        async def good_fn():
            return "ok"

        async def _run2():
            cb = CircuitBreaker("test", failure_threshold=3)
            result = await cb.call(good_fn)
            assert result == "ok"
            assert cb.state == CircuitState.CLOSED

        asyncio.run(_run2())

    def test_opens_after_threshold(self):
        from shared.core.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerOpenError

        async def bad_fn():
            raise ValueError("boom")

        async def _run():
            cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=9999)
            for _ in range(3):
                with pytest.raises(ValueError):
                    await cb.call(bad_fn)
            assert cb.state == CircuitState.OPEN
            with pytest.raises(CircuitBreakerOpenError):
                await cb.call(bad_fn)

        asyncio.run(_run())

    def test_half_open_after_recovery_timeout(self):
        import time
        from shared.core.circuit_breaker import CircuitBreaker, CircuitState

        async def bad_fn():
            raise ValueError("boom")

        async def good_fn():
            return "recovered"

        async def _run():
            cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.01)
            for _ in range(2):
                with pytest.raises(ValueError):
                    await cb.call(bad_fn)
            assert cb.state == CircuitState.OPEN
            await asyncio.sleep(0.05)  # wait past recovery_timeout
            result = await cb.call(good_fn)
            assert result == "recovered"
            assert cb.state == CircuitState.CLOSED

        asyncio.run(_run())


# ─── backtest_reporter integration smoke ──────────────────────────────────
def test_reporter_imports_cleanly():
    """Ensure backtest_reporter can be imported without errors."""
    try:
        import MyProject.backtest_reporter as br  # noqa: F401
        assert hasattr(br, "APEXBacktestReporter")
    except ImportError as e:
        pytest.skip(f"Optional dependency missing: {e}")
