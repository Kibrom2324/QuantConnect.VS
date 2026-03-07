"""
tests/test_cost_estimator.py — Phase 1 ExecutionCostEstimator tests.
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
from shared.core.cost_estimator import ExecutionCostEstimator, CostEstimate


class TestCostEstimate:
    def test_dataclass_fields(self):
        c = CostEstimate(1.0, 2.0, 3.0, 6.0, 10.0, 4.0)
        assert c.spread_bps == 1.0
        assert c.impact_bps == 2.0
        assert c.slippage_bps == 3.0
        assert c.total_cost_bps == 6.0
        assert c.raw_edge_bps == 10.0
        assert c.net_edge_bps == 4.0


class TestExecutionCostEstimator:
    @pytest.fixture
    def estimator(self):
        return ExecutionCostEstimator()

    def test_basic_estimate(self, estimator):
        result = estimator.estimate(
            raw_edge_bps=20.0,
            spread_bps=2.0,
            order_dollar_value=10_000,
            adv_20d=1_000_000,
        )
        assert isinstance(result, CostEstimate)
        assert result.spread_bps == 2.0
        assert result.slippage_bps == 2.0
        assert result.raw_edge_bps == 20.0
        assert result.net_edge_bps == result.raw_edge_bps - result.total_cost_bps

    def test_impact_increases_with_participation(self, estimator):
        small = estimator.estimate(20.0, 2.0, 1_000, 1_000_000)
        large = estimator.estimate(20.0, 2.0, 100_000, 1_000_000)
        assert large.impact_bps > small.impact_bps

    def test_zero_adv_gives_max_impact(self, estimator):
        result = estimator.estimate(20.0, 2.0, 10_000, 0.0)
        assert result.impact_bps == 10.0  # IMPACT_COEF

    def test_net_edge_positive(self, estimator):
        result = estimator.estimate(50.0, 1.0, 1_000, 10_000_000)
        assert result.net_edge_bps > 0

    def test_net_edge_negative(self, estimator):
        result = estimator.estimate(1.0, 5.0, 100_000, 100_000)
        assert result.net_edge_bps < 0

    def test_should_veto_positive_edge(self, estimator):
        result = estimator.estimate(50.0, 1.0, 1_000, 10_000_000)
        assert not estimator.should_veto(result)

    def test_should_veto_negative_edge(self, estimator):
        result = estimator.estimate(1.0, 5.0, 100_000, 100_000)
        assert estimator.should_veto(result)

    def test_should_veto_zero_edge(self, estimator):
        # Create a cost estimate with exactly zero net edge
        c = CostEstimate(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert estimator.should_veto(c)

    def test_custom_slippage(self):
        est = ExecutionCostEstimator(slippage_bps=5.0)
        result = est.estimate(20.0, 2.0, 10_000, 1_000_000)
        assert result.slippage_bps == 5.0

    def test_custom_impact_coef(self):
        est = ExecutionCostEstimator(impact_coef=20.0)
        default_est = ExecutionCostEstimator()
        r1 = est.estimate(20.0, 2.0, 10_000, 1_000_000)
        r2 = default_est.estimate(20.0, 2.0, 10_000, 1_000_000)
        assert r1.impact_bps > r2.impact_bps
