"""
APEX Isotonic Calibrator — shared/core/calibrator.py

Non-parametric probability calibration using isotonic regression.
Replaces Platt scaling (sigmoid assumption) with a data-driven
calibration curve.

Phase 0 deliverable.
"""

from __future__ import annotations

import logging
import pickle
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class IsotonicCalibrator:
    """
    Isotonic regression calibrator with Redis persistence and Brier tracking.

    Safe fallback: if not fitted, calibrate() returns the input unchanged.
    """

    def __init__(self) -> None:
        self._model: Any = None  # IsotonicRegression instance
        self._is_fitted: bool = False

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(self, predicted_probs: np.ndarray, actual_outcomes: np.ndarray) -> None:
        """
        Fit isotonic regression on (predicted_prob, actual_outcome) pairs.

        Parameters
        ----------
        predicted_probs : array of predicted probabilities in [0, 1]
        actual_outcomes : array of binary outcomes (0 or 1)
        """
        from sklearn.isotonic import IsotonicRegression

        self._model = IsotonicRegression(
            y_min=0.0, y_max=1.0, out_of_bounds="clip"
        )
        self._model.fit(
            np.asarray(predicted_probs, dtype=float),
            np.asarray(actual_outcomes, dtype=float),
        )
        self._is_fitted = True

    def calibrate(self, raw_prob: float) -> float:
        """
        Map raw probability → calibrated probability.

        Returns raw_prob unchanged if model is not fitted (safe degradation).
        """
        if not self._is_fitted or self._model is None:
            return raw_prob

        result = self._model.predict([raw_prob])
        return float(result[0])

    def calibrate_batch(self, raw_probs: np.ndarray) -> np.ndarray:
        """Calibrate an array of probabilities."""
        if not self._is_fitted or self._model is None:
            return np.asarray(raw_probs, dtype=float)

        return self._model.predict(np.asarray(raw_probs, dtype=float))

    @staticmethod
    def brier_score(predicted: np.ndarray, actual: np.ndarray) -> float:
        """
        Compute Brier score = mean((predicted - actual)²).

        Lower is better. 0.0 = perfect, 0.25 = random baseline, 1.0 = worst.
        """
        p = np.asarray(predicted, dtype=float)
        a = np.asarray(actual, dtype=float)
        return float(np.mean((p - a) ** 2))

    def reliability_bins(
        self,
        predicted: np.ndarray,
        actual: np.ndarray,
        n_bins: int = 10,
    ) -> list[dict[str, float]]:
        """
        Compute calibration curve bins for reliability diagram.

        Returns list of dicts with keys:
            bin_lower, bin_upper, predicted_prob_avg, actual_freq, sample_count
        """
        p = np.asarray(predicted, dtype=float)
        a = np.asarray(actual, dtype=float)
        bins = []
        edges = np.linspace(0.0, 1.0, n_bins + 1)

        for i in range(n_bins):
            lo, hi = edges[i], edges[i + 1]
            mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
            count = int(mask.sum())
            bins.append({
                "bin_lower": float(lo),
                "bin_upper": float(hi),
                "predicted_prob_avg": float(p[mask].mean()) if count > 0 else float((lo + hi) / 2),
                "actual_freq": float(a[mask].mean()) if count > 0 else 0.0,
                "sample_count": count,
            })

        return bins

    def save_to_redis(self, redis_client: Any, key: str = "apex:calibration:curve") -> None:
        """Serialize fitted model to Redis as pickle bytes."""
        if not self._is_fitted or self._model is None:
            logger.warning("calibrator_not_fitted_skip_redis_save")
            return

        data = pickle.dumps(self._model)
        redis_client.set(key, data)
        logger.info("calibrator_saved_to_redis key=%s bytes=%d", key, len(data))

    def load_from_redis(self, redis_client: Any, key: str = "apex:calibration:curve") -> bool:
        """
        Load fitted model from Redis.

        Returns True on success, False if key missing or data corrupt.
        """
        try:
            data = redis_client.get(key)
            if data is None:
                logger.info("calibrator_redis_key_missing key=%s", key)
                return False

            # Ensure we got bytes (decode_responses=True returns str)
            if isinstance(data, str):
                data = data.encode("latin-1")

            self._model = pickle.loads(data)  # noqa: S301
            self._is_fitted = True
            logger.info("calibrator_loaded_from_redis key=%s", key)
            return True

        except Exception as exc:
            logger.warning("calibrator_redis_load_failed key=%s error=%s", key, str(exc))
            self._model = None
            self._is_fitted = False
            return False
