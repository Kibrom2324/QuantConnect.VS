"""
tests/test_ood_detector.py — Phase 4 OODDetector tests.
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
from shared.core.ood_detector import OODDetector, OODResult, OOD_TIERS


class TestOODDetector:
    @pytest.fixture
    def fitted_detector(self):
        np.random.seed(42)
        X = np.random.randn(200, 5)
        det = OODDetector()
        det.fit(X)
        return det

    def test_unfitted_returns_safe(self):
        det = OODDetector()
        r = det.evaluate(np.array([1.0, 2.0, 3.0]))
        assert r.tier_name == "not_fitted"
        assert r.confidence_modifier == 1.0
        assert r.should_suppress is False

    def test_fit_sets_fitted(self, fitted_detector):
        assert fitted_detector.is_fitted is True

    def test_training_point_is_normal(self, fitted_detector):
        # A point near the centroid should be normal
        r = fitted_detector.evaluate(np.zeros(5))
        assert r.tier_name == "normal"
        assert r.confidence_modifier == 1.0
        assert r.should_suppress is False

    def test_far_point_is_severe(self, fitted_detector):
        # A point very far from training distribution
        r = fitted_detector.evaluate(np.ones(5) * 100)
        assert r.tier_name == "severe"
        assert r.confidence_modifier == 0.0
        assert r.should_suppress is True

    def test_mild_ood(self, fitted_detector):
        # Find a point that puts us in the mild zone by scaling up
        np.random.seed(99)
        for scale in [2.0, 3.0, 4.0, 5.0]:
            r = fitted_detector.evaluate(np.ones(5) * scale)
            if r.tier_name == "mild":
                assert r.confidence_modifier == 0.8
                break

    def test_ood_score_is_positive(self, fitted_detector):
        r = fitted_detector.evaluate(np.ones(5) * 5)
        assert r.ood_score >= 0.0

    def test_save_load(self, fitted_detector):
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name
        fitted_detector.save(path)

        loaded = OODDetector()
        loaded.load(path)
        assert loaded.is_fitted is True

        # Both should give same result for same point
        point = np.array([1.0, 0.5, -0.5, 0.0, 0.2])
        r1 = fitted_detector.evaluate(point)
        r2 = loaded.evaluate(point)
        assert r1.ood_score == pytest.approx(r2.ood_score, abs=1e-6)

        Path(path).unlink()

    def test_tiers_are_exhaustive(self):
        # Last tier should have inf threshold
        assert OOD_TIERS[-1][0] == float("inf")

    def test_result_dataclass_fields(self):
        r = OODResult(1.0, 2, "moderate", 0.5, False)
        assert r.ood_score == 1.0
        assert r.tier == 2
        assert r.tier_name == "moderate"
        assert r.confidence_modifier == 0.5
        assert r.should_suppress is False

    def test_two_features(self):
        """Detector works with small feature count."""
        np.random.seed(42)
        X = np.random.randn(100, 2)
        det = OODDetector()
        det.fit(X)
        r = det.evaluate(np.array([0.0, 0.0]))
        assert r.tier in [0, 1, 2, 3]
