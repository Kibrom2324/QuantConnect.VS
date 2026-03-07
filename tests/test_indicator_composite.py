"""
tests/test_indicator_composite.py — Phase 2 IndicatorComposite tests.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import numpy as np
import pytest
from models.indicator_composite import IndicatorComposite, INDICATOR_FEATURES


class TestIndicatorComposite:
    @pytest.fixture
    def sample_payload(self):
        return {
            "rsi_14": 55.0,
            "ema_12": 150.0,
            "ema_26": 148.0,
            "macd_line": 2.0,
            "macd_signal": 1.5,
            "macd_histogram": 0.5,
            "stoch_k": 60.0,
            "stoch_d": 55.0,
            "sma_50": 145.0,
            "sma_200": 140.0,
            "bb_upper": 155.0,
            "bb_lower": 135.0,
            "bb_width": 20.0,
            "realized_vol_20d": 0.18,
            "volume_zscore_20d": 1.2,
        }

    @pytest.fixture
    def fitted_model(self):
        np.random.seed(42)
        X = np.random.randn(200, 18)
        y = (X[:, 0] > 0).astype(int)
        model = IndicatorComposite()
        model.fit(X, y)
        return model

    def test_feature_count(self):
        assert len(INDICATOR_FEATURES) == 18

    def test_extract_features_shape(self, sample_payload):
        model = IndicatorComposite()
        features = model.extract_features(sample_payload)
        assert features.shape == (18,)
        assert features.dtype == np.float64

    def test_interaction_terms(self, sample_payload):
        model = IndicatorComposite()
        features = model.extract_features(sample_payload)
        # rsi_x_macd = 55.0 * 0.5 = 27.5
        assert features[15] == pytest.approx(55.0 * 0.5)
        # stoch_x_volume = 60.0 * 1.2 = 72.0
        assert features[16] == pytest.approx(60.0 * 1.2)
        # sma_cross_x_vol = 1.0 * 0.18 = 0.18 (sma_50 > sma_200)
        assert features[17] == pytest.approx(0.18)

    def test_sma_cross_negative(self, sample_payload):
        sample_payload["sma_50"] = 130.0  # < sma_200
        model = IndicatorComposite()
        features = model.extract_features(sample_payload)
        assert features[17] == pytest.approx(-0.18)

    def test_missing_features_use_defaults(self):
        model = IndicatorComposite()
        features = model.extract_features({})
        assert features[0] == 50.0  # default rsi
        assert features[6] == 50.0  # default stoch_k

    def test_predict_unfitted_returns_none(self, sample_payload):
        model = IndicatorComposite()
        assert model.predict(sample_payload) is None

    def test_predict_fitted_returns_probability(self, fitted_model, sample_payload):
        prob = fitted_model.predict(sample_payload)
        assert prob is not None
        assert 0.0 <= prob <= 1.0

    def test_fit_uses_sklearn_fallback(self):
        """Fit uses GradientBoosting when lightgbm is absent."""
        np.random.seed(42)
        X = np.random.randn(100, 18)
        y = (X[:, 0] > 0).astype(int)
        model = IndicatorComposite()
        model.fit(X, y)
        assert model.is_fitted is True

    def test_save_load(self, fitted_model, sample_payload):
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name
        fitted_model.save(path)

        loaded = IndicatorComposite()
        loaded.load(path)
        assert loaded.is_fitted is True

        p1 = fitted_model.predict(sample_payload)
        p2 = loaded.predict(sample_payload)
        assert p1 == pytest.approx(p2, abs=1e-6)

        Path(path).unlink()

    def test_is_fitted_property(self):
        model = IndicatorComposite()
        assert model.is_fitted is False
        np.random.seed(42)
        X = np.random.randn(50, 18)
        y = np.random.randint(0, 2, 50)
        model.fit(X, y)
        assert model.is_fitted is True
