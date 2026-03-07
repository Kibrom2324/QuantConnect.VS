"""
tests/test_disagreement.py — Phase 3 DisagreementModifier tests.
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
from shared.core.disagreement import DisagreementModifier, DisagreementResult


class TestDisagreementModifier:
    @pytest.fixture
    def modifier(self):
        return DisagreementModifier()

    def test_single_model_no_disagreement(self, modifier):
        r = modifier.analyze({"xgboost": 0.8}, symbol="AAPL")
        assert r.direction_agreement == 1.0
        assert r.disagreement_score == 0.0
        assert r.modifier == 1.0
        assert r.consensus_direction == 1

    def test_full_agreement_bullish(self, modifier):
        preds = {"xgb": 0.7, "lstm": 0.8, "tsfm": 0.6}
        r = modifier.analyze(preds, symbol="MSFT")
        assert r.direction_agreement == 1.0
        assert r.consensus_direction == 1
        assert r.modifier == 1.0

    def test_full_agreement_bearish(self, modifier):
        preds = {"xgb": 0.3, "lstm": 0.2, "tsfm": 0.4}
        r = modifier.analyze(preds, symbol="TSLA")
        assert r.direction_agreement == 1.0
        assert r.consensus_direction == -1

    def test_split_disagreement(self, modifier):
        preds = {"xgb": 0.9, "lstm": 0.1}
        r = modifier.analyze(preds, symbol="AMD")
        assert r.direction_agreement == 0.5
        assert r.disagreement_score > 0.0
        assert r.modifier < 1.0

    def test_high_disagreement_reduces_modifier(self, modifier):
        preds = {"a": 0.9, "b": 0.1, "c": 0.8, "d": 0.2}
        r = modifier.analyze(preds, symbol="NVDA")
        assert r.modifier < 1.0
        assert r.modifier >= 0.3  # min_modifier

    def test_modifier_bounded_above_min(self):
        m = DisagreementModifier(min_modifier=0.5)
        preds = {"a": 0.99, "b": 0.01}
        r = m.analyze(preds, symbol="TEST")
        assert r.modifier >= 0.5

    def test_persistence_tracking(self, modifier):
        # Same bullish consensus repeated
        for _ in range(5):
            r = modifier.analyze({"a": 0.7, "b": 0.8}, symbol="AAPL")
        assert r.persistence_bars == 5

    def test_consensus_changes_resets_persistence(self, modifier):
        # Bullish consensus
        modifier.analyze({"a": 0.7, "b": 0.8}, symbol="AAPL")
        modifier.analyze({"a": 0.7, "b": 0.8}, symbol="AAPL")
        # Switch to bearish
        r = modifier.analyze({"a": 0.3, "b": 0.2}, symbol="AAPL")
        assert r.persistence_bars == 1

    def test_output_fields(self, modifier):
        r = modifier.analyze({"a": 0.7, "b": 0.6}, symbol="X")
        assert isinstance(r, DisagreementResult)
        assert hasattr(r, "direction_agreement")
        assert hasattr(r, "confidence_spread")
        assert hasattr(r, "disagreement_score")
        assert hasattr(r, "consensus_direction")
        assert hasattr(r, "modifier")
        assert hasattr(r, "persistence_bars")
        assert hasattr(r, "regime_shift")

    def test_spread_increases_with_divergence(self, modifier):
        tight = modifier.analyze({"a": 0.6, "b": 0.65}, symbol="T1")
        wide = modifier.analyze({"a": 0.9, "b": 0.1}, symbol="T2")
        assert wide.confidence_spread > tight.confidence_spread
