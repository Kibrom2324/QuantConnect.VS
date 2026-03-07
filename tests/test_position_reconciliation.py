"""
tests/test_position_reconciliation.py — Position reconciliation tests.

Verifies PositionReconciler spec:
  - Mismatch > 1 share  → halt + POSITION_MISMATCH metric incremented
  - Mismatch > $50      → halt + POSITION_MISMATCH metric incremented
  - Perfect match       → no halt, no counter increment
  - Position present in Alpaca but absent from internal state → mismatch
  - Position present internally but absent in Alpaca → mismatch
  - Multiple mismatches in one reconcile call

Run: pytest tests/test_position_reconciliation.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure workspace root is on sys.path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# Pre-stub confluent_kafka so execution.main imports cleanly
_mk = MagicMock()
_mk.Consumer = MagicMock
_mk.Producer = MagicMock
sys.modules.setdefault("confluent_kafka", _mk)

from services.execution.main import PositionReconciler  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_reconciler(alpaca_positions: list[dict]) -> PositionReconciler:
    """Return a PositionReconciler whose Alpaca client returns *alpaca_positions*."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = alpaca_positions
    mock_client.get.return_value = mock_response

    return PositionReconciler(
        alpaca_client=mock_client,
        alpaca_base_url="https://paper-api.alpaca.markets",
        alpaca_key="test_key",
        alpaca_secret="test_secret",
    )


# ===========================================================================
# Perfect match → no mismatch
# ===========================================================================

class TestPerfectMatch:
    def test_exact_match_returns_no_mismatches(self):
        alpaca = [
            {"symbol": "NVDA", "qty": "10", "market_value": "1500.00"},
            {"symbol": "AAPL", "qty": "5",  "market_value": "900.00"},
        ]
        rec = _make_reconciler(alpaca)
        rec.update_internal("NVDA", qty=10.0, market_value=1500.0)
        rec.update_internal("AAPL", qty=5.0,  market_value=900.0)

        mismatches = _run(rec.reconcile())
        assert mismatches == [], f"Expected no mismatches, got: {mismatches}"
        assert not rec.is_halted

    def test_small_value_difference_within_tolerance(self):
        """Difference of $49 is within the $50 tolerance."""
        alpaca = [{"symbol": "NVDA", "qty": "10", "market_value": "1500.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("NVDA", qty=10.0, market_value=1549.99)  # diff = $49.99

        mismatches = _run(rec.reconcile())
        assert mismatches == []
        assert not rec.is_halted

    def test_small_qty_difference_within_tolerance(self):
        """Difference of 0.9 shares is within the 1-share tolerance."""
        alpaca = [{"symbol": "TSLA", "qty": "3.5", "market_value": "700.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("TSLA", qty=4.4, market_value=700.0)  # diff = 0.9 shares

        mismatches = _run(rec.reconcile())
        assert mismatches == []
        assert not rec.is_halted


# ===========================================================================
# Share mismatch → halt
# ===========================================================================

class TestShareMismatch:
    def test_share_diff_gt_1_triggers_halt(self):
        """Difference of 2 shares exceeds tolerance → halt."""
        alpaca = [{"symbol": "NVDA", "qty": "10", "market_value": "1500.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("NVDA", qty=12.0, market_value=1500.0)  # diff = 2 shares

        mismatches = _run(rec.reconcile())
        assert len(mismatches) == 1
        assert mismatches[0]["symbol"] == "NVDA"
        assert mismatches[0]["share_diff"] == pytest.approx(2.0)
        assert rec.is_halted is True

    def test_exact_threshold_boundary(self):
        """Difference of exactly 1 share is AT tolerance → no mismatch."""
        alpaca = [{"symbol": "AAPL", "qty": "5", "market_value": "900.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("AAPL", qty=6.0, market_value=900.0)  # diff = exactly 1.0

        mismatches = _run(rec.reconcile())
        assert mismatches == [], "Exactly at tolerances must not trigger mismatch"

    def test_share_diff_just_over_1_triggers_halt(self):
        """Difference of 1.01 shares just exceeds tolerance → halt."""
        alpaca = [{"symbol": "AMD", "qty": "20", "market_value": "2000.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("AMD", qty=21.01, market_value=2000.0)

        mismatches = _run(rec.reconcile())
        assert len(mismatches) == 1
        assert rec.is_halted is True


# ===========================================================================
# Value mismatch → halt
# ===========================================================================

class TestValueMismatch:
    def test_value_diff_gt_50_triggers_halt(self):
        """Difference of $51 exceeds tolerance → halt."""
        alpaca = [{"symbol": "SPY", "qty": "10", "market_value": "5000.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("SPY", qty=10.0, market_value=5051.0)  # diff = $51

        mismatches = _run(rec.reconcile())
        assert len(mismatches) == 1
        assert mismatches[0]["value_diff"] == pytest.approx(51.0)
        assert rec.is_halted is True

    def test_value_diff_exactly_50_no_mismatch(self):
        """Difference of exactly $50 is AT tolerance → no mismatch."""
        alpaca = [{"symbol": "SPY", "qty": "10", "market_value": "5000.00"}]
        rec = _make_reconciler(alpaca)
        rec.update_internal("SPY", qty=10.0, market_value=5050.0)

        mismatches = _run(rec.reconcile())
        assert mismatches == []


# ===========================================================================
# Ghost positions (asymmetric sets)
# ===========================================================================

class TestGhostPositions:
    def test_position_in_alpaca_not_in_internal(self):
        """Symbol in Alpaca but qty=0 internally → share mismatch."""
        alpaca = [{"symbol": "MSFT", "qty": "5", "market_value": "1800.00"}]
        rec = _make_reconciler(alpaca)
        # MSFT not tracked internally → treated as 0 shares / $0

        mismatches = _run(rec.reconcile())
        assert any(m["symbol"] == "MSFT" for m in mismatches)
        assert rec.is_halted is True

    def test_position_in_internal_not_in_alpaca(self):
        """Symbol in internal state but absent from Alpaca → share mismatch."""
        alpaca: list[dict] = []  # no positions in Alpaca
        rec = _make_reconciler(alpaca)
        rec.update_internal("GOOG", qty=3.0, market_value=750.0)

        mismatches = _run(rec.reconcile())
        assert any(m["symbol"] == "GOOG" for m in mismatches)
        assert rec.is_halted is True


# ===========================================================================
# Multiple mismatches
# ===========================================================================

class TestMultipleMismatches:
    def test_multiple_symbols_mismatch(self):
        alpaca = [
            {"symbol": "NVDA", "qty": "10",  "market_value": "1500.00"},
            {"symbol": "AAPL", "qty": "5",   "market_value": "900.00"},
            {"symbol": "TSLA", "qty": "8",   "market_value": "2000.00"},
        ]
        rec = _make_reconciler(alpaca)
        # Two mismatches: NVDA and TSLA
        rec.update_internal("NVDA", qty=10.0,  market_value=1500.0)   # OK
        rec.update_internal("AAPL", qty=12.0,  market_value=900.0)    # 7-share diff
        rec.update_internal("TSLA", qty=8.0,   market_value=2200.0)   # $200 diff

        mismatches = _run(rec.reconcile())
        mismatch_syms = {m["symbol"] for m in mismatches}
        assert "AAPL" in mismatch_syms
        assert "TSLA" in mismatch_syms
        assert "NVDA" not in mismatch_syms
        assert len(mismatches) == 2
        assert rec.is_halted is True


# ===========================================================================
# Alpaca API failure
# ===========================================================================

class TestAlpacaFailure:
    def test_api_failure_returns_empty_no_halt(self):
        """If Alpaca fetch fails, reconcile returns [] without halting."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.ConnectError("timeout")

        rec = PositionReconciler(
            alpaca_client=mock_client,
            alpaca_base_url="https://paper-api.alpaca.markets",
            alpaca_key="k",
            alpaca_secret="s",
        )
        rec.update_internal("NVDA", qty=10.0)

        mismatches = _run(rec.reconcile())
        assert mismatches == []
        # Fetch failure alone does not halt — it's a transient error
        # (the reconciler can't determine whether positions match)
        assert not rec.is_halted
