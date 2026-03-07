"""
tests/test_adaptive_combiner.py — Phase 3 AdaptiveCombiner tests.
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
from shared.core.adaptive_combiner import (
    AdaptiveCombiner,
    ModelAccuracyTracker,
    INITIAL_REGIME_WEIGHTS,
    WEIGHT_FLOOR,
    MIN_TRADES_FOR_ADAPTIVE,
)
from shared.core.regime import REGIME_TRENDING_UP, REGIME_VOLATILE, REGIME_UNKNOWN


class TestModelAccuracyTracker:
    def test_empty_tracker_50pct(self):
        t = ModelAccuracyTracker()
        assert t.accuracy == 0.5
        assert t.sample_count == 0

    def test_record_and_accuracy(self):
        t = ModelAccuracyTracker()
        t.record(True)
        t.record(True)
        t.record(False)
        assert t.accuracy == pytest.approx(2 / 3)
        assert t.sample_count == 3

    def test_rolling_window(self):
        t = ModelAccuracyTracker()
        # Fill with 50 True followed by 10 False
        for _ in range(50):
            t.record(True)
        assert t.accuracy == 1.0
        for _ in range(10):
            t.record(False)
        # Buffer is 50, so oldest 10 True are dropped
        assert t.sample_count == 50
        assert t.accuracy == pytest.approx(40 / 50)


class TestAdaptiveCombiner:
    @pytest.fixture
    def combiner(self):
        return AdaptiveCombiner()

    def test_initial_weights_trending_up(self, combiner):
        w = combiner.get_weights(REGIME_TRENDING_UP)
        expected = INITIAL_REGIME_WEIGHTS[REGIME_TRENDING_UP]
        assert w == expected

    def test_unknown_regime_30pct_reduction(self, combiner):
        w = combiner.get_weights(REGIME_UNKNOWN)
        for model, weight in w.items():
            expected = INITIAL_REGIME_WEIGHTS[REGIME_UNKNOWN][model] * 0.7
            assert weight == pytest.approx(expected)

    def test_weights_sum_close_to_one(self, combiner):
        for regime in [1, 2, 3, 4]:
            w = combiner.get_weights(regime)
            assert sum(w.values()) == pytest.approx(1.0)

    def test_adaptive_after_sufficient_trades(self, combiner):
        # Record enough trades for all models
        for _ in range(MIN_TRADES_FOR_ADAPTIVE + 5):
            combiner.record_outcome(REGIME_TRENDING_UP, "xgboost", True)
            combiner.record_outcome(REGIME_TRENDING_UP, "lstm", True)
            combiner.record_outcome(REGIME_TRENDING_UP, "timesfm", False)
            combiner.record_outcome(REGIME_TRENDING_UP, "indicator_composite", True)

        w = combiner.get_weights(REGIME_TRENDING_UP)
        # xgboost/lstm/indicator should have higher weight than timesfm
        assert w["xgboost"] > w["timesfm"]
        assert sum(w.values()) == pytest.approx(1.0)

    def test_combine_equal_predictions(self, combiner):
        preds = {
            "xgboost": 0.7,
            "lstm": 0.7,
            "timesfm": 0.7,
            "indicator_composite": 0.7,
        }
        combined, weights = combiner.combine(REGIME_TRENDING_UP, preds)
        assert combined == pytest.approx(0.7)

    def test_combine_mixed_predictions(self, combiner):
        preds = {
            "xgboost": 0.9,
            "lstm": 0.1,
            "timesfm": 0.5,
            "indicator_composite": 0.5,
        }
        combined, weights = combiner.combine(REGIME_TRENDING_UP, preds)
        assert 0.1 < combined < 0.9
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_combine_subset_of_models(self, combiner):
        preds = {"xgboost": 0.8, "lstm": 0.6}
        combined, weights = combiner.combine(REGIME_TRENDING_UP, preds)
        assert 0.6 < combined < 0.8

    def test_combine_empty_predictions(self, combiner):
        combined, weights = combiner.combine(REGIME_TRENDING_UP, {})
        assert combined == 0.5

    def test_record_unknown_model_ignored(self, combiner):
        # Should not raise
        combiner.record_outcome(REGIME_TRENDING_UP, "unknown_model", True)

    def test_accuracy_report(self, combiner):
        combiner.record_outcome(1, "xgboost", True)
        combiner.record_outcome(1, "xgboost", True)
        report = combiner.get_accuracy_report()
        assert 1 in report
        assert report[1]["xgboost"]["sample_count"] == 2
        assert report[1]["xgboost"]["accuracy"] == 1.0
