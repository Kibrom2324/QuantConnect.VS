"""
APEX OOD Detector — shared/core/ood_detector.py

Phase 4: Centroid-distance based out-of-distribution detection
with 4-tier degradation.

Tiers:
  < 0.8  → normal (no action)
  0.8–1.0 → mild OOD (reduce confidence 20%)
  1.0–1.5 → moderate OOD (reduce confidence 50%)
  > 1.5  → severe OOD (suppress prediction entirely)

Distance is computed as the Mahalanobis distance from the training
set centroid, normalized so the training set median distance = 1.0.
"""

from __future__ import annotations

import logging
import os
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ENABLE_OOD_DETECTION: bool = (
    os.environ.get("ENABLE_OOD_DETECTION", "false").lower() == "true"
)


@dataclass
class OODResult:
    """Result of OOD evaluation."""
    ood_score: float          # normalized distance (1.0 = median training distance)
    tier: int                 # 0=normal, 1=mild, 2=moderate, 3=severe
    tier_name: str
    confidence_modifier: float  # multiplier to apply to confidence
    should_suppress: bool     # True if prediction should be discarded


# Tier thresholds and modifiers
OOD_TIERS = [
    (0.8, "normal", 1.0, False),
    (1.0, "mild", 0.8, False),
    (1.5, "moderate", 0.5, False),
    (float("inf"), "severe", 0.0, True),
]


class OODDetector:
    """
    Centroid-distance out-of-distribution detector.

    Computes Mahalanobis distance from the training centroid,
    normalized by the training set median distance.
    """

    def __init__(self) -> None:
        self._centroid: np.ndarray | None = None
        self._inv_cov: np.ndarray | None = None
        self._median_distance: float = 1.0
        self._is_fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(self, X: np.ndarray) -> "OODDetector":
        """
        Fit the detector from training data.

        Computes centroid, inverse covariance matrix, and median distance.

        Parameters
        ----------
        X : Training feature matrix (n_samples, n_features)
        """
        self._centroid = np.mean(X, axis=0)

        # Regularized covariance
        cov = np.cov(X, rowvar=False)
        # Add small regularization for numerical stability
        cov += np.eye(cov.shape[0]) * 1e-6
        self._inv_cov = np.linalg.inv(cov)

        # Compute distances for all training points
        distances = np.array([self._mahalanobis(x) for x in X])
        self._median_distance = float(np.median(distances))
        if self._median_distance == 0:
            self._median_distance = 1.0

        self._is_fitted = True
        logger.info(
            "OOD detector fitted: n=%d, features=%d, median_dist=%.4f",
            X.shape[0], X.shape[1], self._median_distance,
        )
        return self

    def _mahalanobis(self, x: np.ndarray) -> float:
        """Compute Mahalanobis distance from centroid."""
        diff = x - self._centroid
        return float(np.sqrt(diff @ self._inv_cov @ diff))

    def evaluate(self, features: np.ndarray) -> OODResult:
        """
        Evaluate whether a feature vector is out-of-distribution.

        Parameters
        ----------
        features : 1D feature vector (same dimensionality as training data)

        Returns
        -------
        OODResult with score, tier, and confidence modifier.
        """
        if not self._is_fitted:
            return OODResult(
                ood_score=0.0,
                tier=0,
                tier_name="not_fitted",
                confidence_modifier=1.0,
                should_suppress=False,
            )

        raw_distance = self._mahalanobis(features)
        normalized = raw_distance / self._median_distance

        # Determine tier
        for tier_idx, (threshold, name, modifier, suppress) in enumerate(OOD_TIERS):
            if normalized < threshold:
                return OODResult(
                    ood_score=round(normalized, 4),
                    tier=tier_idx,
                    tier_name=name,
                    confidence_modifier=modifier,
                    should_suppress=suppress,
                )

        # Should never reach here, but handle anyway
        return OODResult(
            ood_score=round(normalized, 4),
            tier=3,
            tier_name="severe",
            confidence_modifier=0.0,
            should_suppress=True,
        )

    def save(self, path: str) -> None:
        """Persist detector to disk."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "centroid": self._centroid,
            "inv_cov": self._inv_cov,
            "median_distance": self._median_distance,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def load(self, path: str) -> "OODDetector":
        """Load detector from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._centroid = data["centroid"]
        self._inv_cov = data["inv_cov"]
        self._median_distance = data["median_distance"]
        self._is_fitted = True
        return self
