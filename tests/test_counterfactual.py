"""
tests/test_counterfactual.py — Phase 4 CounterfactualTracker tests.
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
from shared.core.counterfactual import CounterfactualTracker, VetoRecord


class TestVetoRecord:
    def test_default_fields(self):
        r = VetoRecord(
            decision_id="d1", symbol="AAPL", direction=1,
            veto_reason="cost", price_at_veto=150.0,
            calibrated_prob=0.55,
        )
        assert r.would_have_won is None
        assert r.counterfactual_exit_price is None
        assert r.counterfactual_pnl_bps is None


class TestCounterfactualTracker:
    @pytest.fixture
    def tracker(self):
        return CounterfactualTracker()

    def test_record_veto(self, tracker):
        r = tracker.record_veto("d1", "AAPL", 1, "cost", 150.0, 0.55)
        assert isinstance(r, VetoRecord)
        assert tracker.buffer_size == 1
        assert tracker.unlabeled_count == 1

    def test_label_long_win(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        # Price went up: long would have won
        labeled = tracker.label_outcomes({"AAPL": 110.0})
        assert labeled == 1
        assert tracker.unlabeled_count == 0
        r = tracker._buffer[0]
        assert r.would_have_won is True
        assert r.counterfactual_pnl_bps == pytest.approx(1000.0)

    def test_label_long_loss(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        labeled = tracker.label_outcomes({"AAPL": 90.0})
        assert labeled == 1
        assert tracker._buffer[0].would_have_won is False
        assert tracker._buffer[0].counterfactual_pnl_bps == pytest.approx(-1000.0)

    def test_label_short_win(self, tracker):
        tracker.record_veto("d1", "TSLA", -1, "ood", 200.0)
        labeled = tracker.label_outcomes({"TSLA": 180.0})
        assert labeled == 1
        assert tracker._buffer[0].would_have_won is True

    def test_label_short_loss(self, tracker):
        tracker.record_veto("d1", "TSLA", -1, "ood", 200.0)
        labeled = tracker.label_outcomes({"TSLA": 220.0})
        assert labeled == 1
        assert tracker._buffer[0].would_have_won is False

    def test_veto_precision_correct_vetoes(self, tracker):
        # Veto a long trade, price drops → correct veto
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.label_outcomes({"AAPL": 90.0})
        assert tracker.veto_precision == 1.0

    def test_veto_precision_incorrect_vetoes(self, tracker):
        # Veto a long trade, price rises → incorrect veto
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.label_outcomes({"AAPL": 110.0})
        assert tracker.veto_precision == 0.0

    def test_veto_precision_mixed(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.record_veto("d2", "MSFT", 1, "ood", 200.0)
        tracker.label_outcomes({"AAPL": 90.0, "MSFT": 210.0})
        assert tracker.veto_precision == 0.5

    def test_no_label_without_exit_price(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        labeled = tracker.label_outcomes({"MSFT": 110.0})
        assert labeled == 0
        assert tracker.unlabeled_count == 1

    def test_buffer_overflow(self):
        t = CounterfactualTracker(max_buffer=3)
        t.record_veto("d1", "A", 1, "x", 10.0)
        t.record_veto("d2", "B", 1, "x", 20.0)
        t.record_veto("d3", "C", 1, "x", 30.0)
        t.record_veto("d4", "D", 1, "x", 40.0)
        assert t.buffer_size == 3
        assert t._buffer[0].symbol == "B"

    def test_summary(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.record_veto("d2", "MSFT", -1, "ood", 200.0)
        s = tracker.get_summary()
        assert s["total_vetoes"] == 2
        assert "cost" in s["by_reason"]
        assert "ood" in s["by_reason"]

    def test_get_unlabeled(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.record_veto("d2", "MSFT", 1, "ood", 200.0)
        tracker.label_outcomes({"AAPL": 110.0})
        unlabeled = tracker.get_unlabeled()
        assert len(unlabeled) == 1
        assert unlabeled[0].symbol == "MSFT"

    def test_double_labeling_ignored(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 100.0)
        tracker.label_outcomes({"AAPL": 110.0})
        # Second labeling should skip already-labeled
        labeled = tracker.label_outcomes({"AAPL": 120.0})
        assert labeled == 0

    def test_zero_entry_price_skipped(self, tracker):
        tracker.record_veto("d1", "AAPL", 1, "cost", 0.0)
        labeled = tracker.label_outcomes({"AAPL": 110.0})
        assert labeled == 0

    def test_empty_precision_is_zero(self, tracker):
        assert tracker.veto_precision == 0.0
