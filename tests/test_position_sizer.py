"""
tests/test_position_sizer.py — Phase 4 PositionSizer tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import pytest
from shared.core.position_sizer import PositionSizer, SizingResult


class TestPositionSizer:
    @pytest.fixture
    def sizer(self):
        return PositionSizer()

    def test_below_min_edge_zero_position(self, sizer):
        r = sizer.size(0.51)  # below 0.52
        assert r.position_size_pct == 0.0
        assert r.edge_sufficient is False

    def test_at_min_edge(self, sizer):
        r = sizer.size(0.52)
        assert r.edge_sufficient is True
        assert r.position_size_pct > 0.0

    def test_high_probability_capped(self, sizer):
        r = sizer.size(0.99)
        assert r.position_size_pct <= 0.02
        assert r.capped is True

    def test_moderate_probability_within_bounds(self, sizer):
        r = sizer.size(0.60)
        assert 0.005 <= r.position_size_pct <= 0.02

    def test_kelly_raw_positive_for_edge(self, sizer):
        r = sizer.size(0.60)
        assert r.kelly_raw > 0

    def test_half_kelly_is_half(self, sizer):
        r = sizer.size(0.70)
        assert r.kelly_half == pytest.approx(r.kelly_raw * 0.5)

    def test_symmetric_payoff(self, sizer):
        # With payoff_ratio=1, Kelly = 2p - 1
        r = sizer.size(0.60, payoff_ratio=1.0)
        assert r.kelly_raw == pytest.approx(0.20, abs=0.01)

    def test_asymmetric_payoff(self, sizer):
        # Higher payoff ratio → larger position
        r1 = sizer.size(0.60, payoff_ratio=1.0)
        r2 = sizer.size(0.60, payoff_ratio=2.0)
        assert r2.kelly_raw > r1.kelly_raw

    def test_custom_bounds(self):
        s = PositionSizer(min_position_pct=0.01, max_position_pct=0.05)
        r = s.size(0.99)
        assert r.position_size_pct <= 0.05

    def test_result_fields(self, sizer):
        r = sizer.size(0.60)
        assert isinstance(r, SizingResult)
        assert hasattr(r, "position_size_pct")
        assert hasattr(r, "kelly_raw")
        assert hasattr(r, "kelly_half")
        assert hasattr(r, "edge_sufficient")
        assert hasattr(r, "capped")

    def test_exactly_50_pct_no_edge(self, sizer):
        r = sizer.size(0.50)
        assert r.edge_sufficient is False
        assert r.position_size_pct == 0.0

    def test_position_never_negative(self, sizer):
        # Even for probability < 0.5
        r = sizer.size(0.30)
        assert r.position_size_pct >= 0.0
