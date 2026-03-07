"""
tests/test_calibrator.py — Phase 0 IsotonicCalibrator tests.

Validates fit/calibrate, Brier score, reliability bins, Redis
persistence, and edge-case handling.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Pre-patch deps before import
if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import numpy as np
import pytest
from shared.core.calibrator import IsotonicCalibrator


# ═══════════════════════════════════════════════════════════════════════════
# Basic calibration
# ═══════════════════════════════════════════════════════════════════════════


class TestIsotonicCalibrator:
    @pytest.fixture
    def fitted_calibrator(self):
        cal = IsotonicCalibrator()
        # Perfectly calibrated synthetic data
        probs = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
        outcomes = np.array([0, 0, 0, 0, 1, 1, 1, 1, 1])
        cal.fit(probs, outcomes)
        return cal

    def test_unfitted_returns_raw(self):
        cal = IsotonicCalibrator()
        assert cal.calibrate(0.5) == 0.5
        assert cal.calibrate(0.0) == 0.0
        assert cal.calibrate(1.0) == 1.0

    def test_fitted_calibration_is_monotonic(self, fitted_calibrator):
        inputs = np.linspace(0.1, 0.9, 20)
        outputs = [fitted_calibrator.calibrate(x) for x in inputs]
        for i in range(1, len(outputs)):
            assert outputs[i] >= outputs[i - 1], "Isotonic output must be non-decreasing"

    def test_calibrate_batch(self, fitted_calibrator):
        inputs = np.array([0.2, 0.5, 0.8])
        results = fitted_calibrator.calibrate_batch(inputs)
        assert len(results) == 3
        assert results[0] <= results[1] <= results[2]

    def test_output_in_0_1_range(self, fitted_calibrator):
        for x in [0.0, 0.01, 0.5, 0.99, 1.0]:
            result = fitted_calibrator.calibrate(x)
            assert 0.0 <= result <= 1.0, f"Out of range: {result}"

    def test_fit_requires_matching_lengths(self):
        cal = IsotonicCalibrator()
        with pytest.raises(ValueError):
            cal.fit(np.array([0.1, 0.2]), np.array([0, 1, 0]))


# ═══════════════════════════════════════════════════════════════════════════
# Brier score
# ═══════════════════════════════════════════════════════════════════════════


class TestBrierScore:
    def test_perfect_predictions(self):
        probs = np.array([1.0, 0.0, 1.0, 0.0])
        outcomes = np.array([1, 0, 1, 0])
        assert IsotonicCalibrator.brier_score(probs, outcomes) == 0.0

    def test_worst_predictions(self):
        probs = np.array([0.0, 1.0, 0.0, 1.0])
        outcomes = np.array([1, 0, 1, 0])
        assert IsotonicCalibrator.brier_score(probs, outcomes) == 1.0

    def test_random_predictions(self):
        probs = np.array([0.5, 0.5, 0.5, 0.5])
        outcomes = np.array([1, 0, 1, 0])
        brier = IsotonicCalibrator.brier_score(probs, outcomes)
        assert brier == pytest.approx(0.25)


# ═══════════════════════════════════════════════════════════════════════════
# Reliability bins
# ═══════════════════════════════════════════════════════════════════════════


class TestReliabilityBins:
    def test_returns_correct_number_of_bins(self):
        cal = IsotonicCalibrator()
        probs = np.linspace(0.0, 1.0, 100)
        outcomes = (probs > 0.5).astype(int)
        bins = cal.reliability_bins(probs, outcomes, n_bins=5)
        assert len(bins) <= 5

    def test_bin_structure(self):
        cal = IsotonicCalibrator()
        probs = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
        outcomes = np.array([0, 0, 0, 0, 1, 1, 1, 1, 1])
        bins = cal.reliability_bins(probs, outcomes, n_bins=3)
        for b in bins:
            assert "predicted_prob_avg" in b
            assert "actual_freq" in b
            assert "sample_count" in b


# ═══════════════════════════════════════════════════════════════════════════
# Redis persistence
# ═══════════════════════════════════════════════════════════════════════════


class TestRedisPersistence:
    def test_save_and_load_round_trip(self):
        cal = IsotonicCalibrator()
        probs = np.array([0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9])
        outcomes = np.array([0, 0, 0, 1, 1, 1, 1])
        cal.fit(probs, outcomes)

        # Mock Redis
        store = {}
        mock_redis = MagicMock()
        mock_redis.set = lambda k, v: store.__setitem__(k, v)
        mock_redis.get = lambda k: store.get(k)

        cal.save_to_redis(mock_redis)
        assert "apex:calibration:curve" in store

        loaded = IsotonicCalibrator()
        result = loaded.load_from_redis(mock_redis)
        assert result is True
        assert loaded.is_fitted
        # Verify loaded calibrator produces same results
        for x in [0.1, 0.3, 0.5, 0.7, 0.9]:
            assert loaded.calibrate(x) == pytest.approx(cal.calibrate(x), abs=1e-6)

    def test_load_missing_key_returns_false(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        cal = IsotonicCalibrator()
        result = cal.load_from_redis(mock_redis)
        assert result is False

    def test_load_corrupt_data_returns_false(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"not_valid_pickle"
        cal = IsotonicCalibrator()
        result = cal.load_from_redis(mock_redis)
        assert result is False
        assert not cal.is_fitted
