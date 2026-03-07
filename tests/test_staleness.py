"""
tests/test_staleness.py — Phase 1 StalenessPolicy tests.
"""

from __future__ import annotations

import sys
import math
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
from shared.core.staleness import StalenessPolicy, StalenessConfig, DEFAULT_STALENESS


class TestStalenessConfig:
    def test_default_configs_exist(self):
        assert "xgboost" in DEFAULT_STALENESS
        assert "lstm" in DEFAULT_STALENESS
        assert "timesfm" in DEFAULT_STALENESS
        assert "indicator_composite" in DEFAULT_STALENESS

    def test_xgboost_defaults(self):
        c = DEFAULT_STALENESS["xgboost"]
        assert c.halflife_seconds == 3600
        assert c.hard_expiry_seconds == 14400


class TestStalenessPolicy:
    @pytest.fixture
    def policy(self):
        return StalenessPolicy()

    def test_fresh_prediction_no_decay(self, policy):
        r = policy.evaluate("xgboost", 0.0, 0.8)
        assert r.is_expired is False
        assert r.decay_factor == 1.0
        assert r.adjusted_confidence == 0.8

    def test_one_halflife_decays_50pct(self, policy):
        r = policy.evaluate("xgboost", 3600, 1.0)
        # At one halflife, decay = exp(-1) ≈ 0.3679
        assert abs(r.decay_factor - math.exp(-1)) < 1e-4
        assert abs(r.adjusted_confidence - math.exp(-1)) < 1e-4

    def test_hard_expiry(self, policy):
        r = policy.evaluate("xgboost", 15000, 0.9)
        assert r.is_expired is True
        assert r.decay_factor == 0.0
        assert r.adjusted_confidence == 0.0

    def test_just_before_expiry(self, policy):
        r = policy.evaluate("xgboost", 14399, 0.9)
        assert r.is_expired is False
        assert r.decay_factor > 0

    def test_lstm_faster_decay(self, policy):
        xgb = policy.evaluate("xgboost", 1800, 1.0)
        lstm = policy.evaluate("lstm", 1800, 1.0)
        # LSTM at its halflife should decay more than XGBoost at same age
        assert lstm.decay_factor < xgb.decay_factor

    def test_timesfm_fastest_decay(self, policy):
        r = policy.evaluate("timesfm", 600, 1.0)
        assert abs(r.decay_factor - math.exp(-1)) < 1e-4

    def test_unknown_model_no_decay(self, policy):
        r = policy.evaluate("unknown_model", 99999, 0.7)
        assert r.is_expired is False
        assert r.decay_factor == 1.0
        assert r.adjusted_confidence == 0.7

    def test_batch_evaluation(self, policy):
        preds = [
            {"model_name": "xgboost", "age_seconds": 0, "confidence": 0.8},
            {"model_name": "lstm", "age_seconds": 1800, "confidence": 0.9},
            {"model_name": "timesfm", "age_seconds": 2000, "confidence": 0.7},
        ]
        results = policy.evaluate_batch(preds)
        assert len(results) == 3
        assert results[0].adjusted_confidence == 0.8  # fresh
        assert results[2].is_expired is True  # timesfm expired at 2000 > 1800

    def test_custom_config(self):
        custom = {
            "fast": StalenessConfig("fast", halflife_seconds=10, hard_expiry_seconds=30),
        }
        policy = StalenessPolicy(configs=custom)
        r = policy.evaluate("fast", 10, 1.0)
        assert abs(r.decay_factor - math.exp(-1)) < 1e-4
        r2 = policy.evaluate("fast", 31, 1.0)
        assert r2.is_expired is True

    def test_zero_confidence(self, policy):
        r = policy.evaluate("xgboost", 1800, 0.0)
        assert r.adjusted_confidence == 0.0
