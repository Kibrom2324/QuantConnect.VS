"""
APEX Staleness Policy — shared/core/staleness.py

Phase 1: Two-level staleness policy for model predictions.

Level 1: Exponential decay — confidence is reduced proportionally to age.
Level 2: Hard expiry — prediction is discarded entirely past a threshold.

Staleness parameters per model (from master architecture):
  XGBoost:             halflife=3600s, expiry=14400s (4h)
  LSTM:                halflife=1800s, expiry=7200s  (2h)
  TimesFM:             halflife=600s,  expiry=1800s  (30m)
  Indicator Composite: halflife=300s,  expiry=900s   (15m)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class StalenessConfig:
    """Staleness parameters for a single model."""
    model_name: str
    halflife_seconds: float
    hard_expiry_seconds: float


# Default configs from master architecture
DEFAULT_STALENESS: dict[str, StalenessConfig] = {
    "xgboost": StalenessConfig("xgboost", halflife_seconds=3600, hard_expiry_seconds=14400),
    "lstm": StalenessConfig("lstm", halflife_seconds=1800, hard_expiry_seconds=7200),
    "timesfm": StalenessConfig("timesfm", halflife_seconds=600, hard_expiry_seconds=1800),
    "indicator_composite": StalenessConfig("indicator_composite", halflife_seconds=300, hard_expiry_seconds=900),
}


@dataclass
class StalenessResult:
    """Result of staleness evaluation."""
    model_name: str
    age_seconds: float
    is_expired: bool
    decay_factor: float
    adjusted_confidence: float


class StalenessPolicy:
    """
    Evaluates prediction freshness using two-level staleness policy.

    Level 1: Exponential decay
      adjusted_confidence = confidence * exp(-age / halflife)

    Level 2: Hard expiry
      If age > hard_expiry_seconds, the prediction is discarded entirely.
    """

    def __init__(self, configs: dict[str, StalenessConfig] | None = None) -> None:
        self._configs = configs or DEFAULT_STALENESS

    def evaluate(
        self,
        model_name: str,
        age_seconds: float,
        confidence: float,
    ) -> StalenessResult:
        """
        Evaluate staleness for a prediction.

        Parameters
        ----------
        model_name : Name/key of the model (e.g. "xgboost", "lstm")
        age_seconds : How old the prediction is in seconds
        confidence : Raw confidence score from the model

        Returns
        -------
        StalenessResult with decay factor and adjusted confidence.
        is_expired=True means the prediction should be discarded.
        """
        config = self._configs.get(model_name.lower())
        if config is None:
            # Unknown model: no decay, no expiry
            return StalenessResult(
                model_name=model_name,
                age_seconds=age_seconds,
                is_expired=False,
                decay_factor=1.0,
                adjusted_confidence=confidence,
            )

        # Level 2: hard expiry check
        if age_seconds > config.hard_expiry_seconds:
            return StalenessResult(
                model_name=model_name,
                age_seconds=age_seconds,
                is_expired=True,
                decay_factor=0.0,
                adjusted_confidence=0.0,
            )

        # Level 1: exponential decay
        decay = math.exp(-age_seconds / config.halflife_seconds)
        adjusted = confidence * decay

        return StalenessResult(
            model_name=model_name,
            age_seconds=age_seconds,
            is_expired=False,
            decay_factor=round(decay, 6),
            adjusted_confidence=round(adjusted, 6),
        )

    def evaluate_batch(
        self,
        predictions: list[dict],
    ) -> list[StalenessResult]:
        """
        Evaluate staleness for a batch of predictions.

        Each dict must have keys: model_name, age_seconds, confidence.
        """
        return [
            self.evaluate(
                model_name=p["model_name"],
                age_seconds=p["age_seconds"],
                confidence=p["confidence"],
            )
            for p in predictions
        ]
