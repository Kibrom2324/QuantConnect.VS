"""
APEX Adaptive Combiner — shared/core/adaptive_combiner.py

Phase 3: Regime-weighted model combination that replaces static
meta-learner weights.

Per-model per-regime rolling 50-trade accuracy tracking with:
  - 0.1 weight floor to prevent complete suppression
  - Equal-weight fallback when < 10 trades in a regime
  - Regime-specific rolling accuracy window
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

ENABLE_ADAPTIVE_COMBINER: bool = (
    os.environ.get("ENABLE_ADAPTIVE_COMBINER", "false").lower() == "true"
)

# Initial regime weights (from master architecture)
INITIAL_REGIME_WEIGHTS: dict[int, dict[str, float]] = {
    1: {"xgboost": 0.25, "lstm": 0.35, "timesfm": 0.15, "indicator_composite": 0.25},  # trending_up
    2: {"xgboost": 0.25, "lstm": 0.35, "timesfm": 0.15, "indicator_composite": 0.25},  # trending_down
    3: {"xgboost": 0.35, "lstm": 0.20, "timesfm": 0.20, "indicator_composite": 0.25},  # range
    4: {"xgboost": 0.20, "lstm": 0.25, "timesfm": 0.25, "indicator_composite": 0.30},  # volatile
    0: {"xgboost": 0.25, "lstm": 0.25, "timesfm": 0.25, "indicator_composite": 0.25},  # unknown
}

# Weight floor: no model goes below this
WEIGHT_FLOOR: float = 0.1

# Minimum trades before switching from equal weights to adaptive
MIN_TRADES_FOR_ADAPTIVE: int = 10

# Rolling window size for accuracy tracking
ACCURACY_WINDOW: int = 50


@dataclass
class ModelAccuracyTracker:
    """Tracks rolling accuracy for a model in a specific regime."""
    correct: list[bool] = field(default_factory=list)

    def record(self, was_correct: bool) -> None:
        self.correct.append(was_correct)
        if len(self.correct) > ACCURACY_WINDOW:
            self.correct.pop(0)

    @property
    def accuracy(self) -> float:
        if not self.correct:
            return 0.5  # prior: assume 50/50
        return sum(self.correct) / len(self.correct)

    @property
    def sample_count(self) -> int:
        return len(self.correct)


class AdaptiveCombiner:
    """
    Combines model predictions using regime-specific adaptive weights.

    Weights are derived from per-model per-regime rolling accuracy.
    Falls back to initial (static) weights when insufficient data exists.
    """

    def __init__(self, model_names: list[str] | None = None) -> None:
        self._model_names = model_names or ["xgboost", "lstm", "timesfm", "indicator_composite"]
        # Nested dict: regime -> model_name -> tracker
        self._trackers: dict[int, dict[str, ModelAccuracyTracker]] = defaultdict(
            lambda: {name: ModelAccuracyTracker() for name in self._model_names}
        )

    def get_weights(self, regime: int) -> dict[str, float]:
        """
        Compute current model weights for a given regime.

        Returns dict of model_name -> weight (sums to 1.0).
        """
        trackers = self._trackers[regime]
        min_samples = min(t.sample_count for t in trackers.values())

        # Not enough data: use initial regime weights
        if min_samples < MIN_TRADES_FOR_ADAPTIVE:
            initial = INITIAL_REGIME_WEIGHTS.get(regime, INITIAL_REGIME_WEIGHTS[0])
            # Unknown regime: reduce all weights by 30%
            if regime == 0:
                return {k: v * 0.7 for k, v in initial.items()}
            return dict(initial)

        # Compute accuracy-proportional weights with floor
        raw_weights = {}
        for name in self._model_names:
            acc = trackers[name].accuracy
            raw_weights[name] = max(acc, WEIGHT_FLOOR)

        # Normalize to sum to 1
        total = sum(raw_weights.values())
        if total == 0:
            n = len(self._model_names)
            return {name: 1.0 / n for name in self._model_names}

        return {name: w / total for name, w in raw_weights.items()}

    def record_outcome(
        self,
        regime: int,
        model_name: str,
        was_correct: bool,
    ) -> None:
        """Record whether a model's prediction was correct in this regime."""
        if model_name not in self._model_names:
            return
        self._trackers[regime][model_name].record(was_correct)

    def combine(
        self,
        regime: int,
        predictions: dict[str, float],
    ) -> tuple[float, dict[str, float]]:
        """
        Combine model predictions using adaptive weights.

        Parameters
        ----------
        regime : Current regime label
        predictions : dict of model_name -> predicted probability

        Returns
        -------
        (combined_score, weights_used)
        """
        weights = self.get_weights(regime)

        # Only use models that provided predictions
        active = {k: v for k, v in predictions.items() if k in weights}
        if not active:
            return 0.5, weights

        # Re-normalize weights to active models only
        active_weights = {k: weights.get(k, 0.0) for k in active}
        total = sum(active_weights.values())
        if total == 0:
            n = len(active)
            active_weights = {k: 1.0 / n for k in active}
            total = 1.0

        combined = sum(
            (active_weights[k] / total) * active[k]
            for k in active
        )
        normalized = {k: v / total for k, v in active_weights.items()}

        return combined, normalized

    def get_accuracy_report(self) -> dict[int, dict[str, dict]]:
        """Return accuracy stats per regime per model."""
        report = {}
        for regime, trackers in self._trackers.items():
            report[regime] = {
                name: {
                    "accuracy": t.accuracy,
                    "sample_count": t.sample_count,
                }
                for name, t in trackers.items()
            }
        return report
