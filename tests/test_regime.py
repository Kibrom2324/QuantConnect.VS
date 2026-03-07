"""
tests/test_regime.py — Phase 3 RegimeClassifier tests.
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
from shared.core.regime import (
    RegimeClassifier,
    REGIME_UNKNOWN,
    REGIME_TRENDING_UP,
    REGIME_TRENDING_DOWN,
    REGIME_RANGE,
    REGIME_VOLATILE,
    REGIME_NAMES,
)


class TestRegimeClassifier:
    @pytest.fixture
    def classifier(self):
        return RegimeClassifier()

    def test_trending_up(self, classifier):
        features = {
            "sma_50": 110.0,
            "sma_200": 100.0,
            "realized_vol_20d": 0.15,
            "vol_ratio_5_20": 0.9,
        }
        assert classifier.classify(features) == REGIME_TRENDING_UP

    def test_trending_down(self, classifier):
        features = {
            "sma_50": 90.0,
            "sma_200": 100.0,
            "realized_vol_20d": 0.15,
            "vol_ratio_5_20": 0.9,
        }
        assert classifier.classify(features) == REGIME_TRENDING_DOWN

    def test_volatile_high_vol(self, classifier):
        features = {
            "sma_50": 110.0,
            "sma_200": 100.0,
            "realized_vol_20d": 0.30,  # > 0.25 threshold
            "vol_ratio_5_20": 0.9,
        }
        assert classifier.classify(features) == REGIME_VOLATILE

    def test_volatile_expanding(self, classifier):
        features = {
            "sma_50": 110.0,
            "sma_200": 100.0,
            "realized_vol_20d": 0.15,
            "vol_ratio_5_20": 1.5,  # > 1.2 expansion ratio
        }
        assert classifier.classify(features) == REGIME_VOLATILE

    def test_missing_features_default(self, classifier):
        # Missing features default to 0 / 1.0
        features = {}
        result = classifier.classify(features)
        # sma_50=0 < sma_200=0 → not sma_trend, vol=0<0.25, ratio=1.0<1.2
        assert result == REGIME_TRENDING_DOWN

    def test_regime_names(self):
        assert RegimeClassifier.regime_name(REGIME_TRENDING_UP) == "trending_up"
        assert RegimeClassifier.regime_name(REGIME_VOLATILE) == "volatile"
        assert RegimeClassifier.regime_name(999) == "unknown"

    def test_custom_thresholds(self):
        c = RegimeClassifier(vol_80th_percentile=0.10, vol_expansion_ratio=1.05)
        features = {
            "sma_50": 110.0,
            "sma_200": 100.0,
            "realized_vol_20d": 0.12,  # > 0.10 with custom threshold
            "vol_ratio_5_20": 0.9,
        }
        assert c.classify(features) == REGIME_VOLATILE

    def test_regime_constants(self):
        assert REGIME_UNKNOWN == 0
        assert REGIME_TRENDING_UP == 1
        assert REGIME_TRENDING_DOWN == 2
        assert REGIME_RANGE == 3
        assert REGIME_VOLATILE == 4

    def test_all_regimes_named(self):
        for val in [REGIME_UNKNOWN, REGIME_TRENDING_UP, REGIME_TRENDING_DOWN, REGIME_RANGE, REGIME_VOLATILE]:
            assert val in REGIME_NAMES
