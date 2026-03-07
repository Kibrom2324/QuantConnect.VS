"""
APEX Disagreement Modifier — shared/core/disagreement.py

Phase 3: Measures disagreement between model predictions and modifies
confidence accordingly.

Disagreement dimensions:
  1. Direction agreement — how many models agree on direction
  2. Confidence spread — std dev of predicted probabilities
  3. Persistence tracking — how long models have agreed/disagreed
  4. Agreement-to-disagreement detection — sudden loss of consensus
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENABLE_DISAGREEMENT_MODIFIER: bool = (
    os.environ.get("ENABLE_DISAGREEMENT_MODIFIER", "false").lower() == "true"
)


@dataclass
class DisagreementResult:
    """Result of disagreement analysis."""
    direction_agreement: float   # fraction of models agreeing on direction [0, 1]
    confidence_spread: float     # std dev of probabilities
    disagreement_score: float    # composite score [0, 1] where 1 = max disagreement
    consensus_direction: int     # majority direction: 1=long, -1=short, 0=tied
    modifier: float              # confidence multiplier [0.3, 1.0]
    persistence_bars: int        # how many bars current consensus has held
    regime_shift: bool           # True if agreement→disagreement detected


class DisagreementModifier:
    """
    Analyzes model disagreement and modifies confidence.

    High disagreement → reduce confidence (conservative).
    High agreement → maintain confidence (status quo).
    Agreement→disagreement transition → extra caution.
    """

    def __init__(
        self,
        min_modifier: float = 0.3,
        disagreement_threshold: float = 0.6,
    ) -> None:
        self._min_modifier = min_modifier
        self._disagreement_threshold = disagreement_threshold
        # Track last N consensus states per symbol for persistence
        self._consensus_history: dict[str, list[int]] = defaultdict(list)
        self._max_history = 20

    def analyze(
        self,
        predictions: dict[str, float],
        symbol: str = "UNKNOWN",
    ) -> DisagreementResult:
        """
        Analyze disagreement between model predictions.

        Parameters
        ----------
        predictions : dict of model_name -> predicted probability [0, 1]
        symbol : symbol for persistence tracking

        Returns
        -------
        DisagreementResult with composite score and modifier.
        """
        if len(predictions) < 2:
            prob = list(predictions.values())[0] if predictions else 0.5
            direction = 1 if prob > 0.5 else (-1 if prob < 0.5 else 0)
            return DisagreementResult(
                direction_agreement=1.0,
                confidence_spread=0.0,
                disagreement_score=0.0,
                consensus_direction=direction,
                modifier=1.0,
                persistence_bars=0,
                regime_shift=False,
            )

        probs = list(predictions.values())
        n = len(probs)

        # 1. Direction agreement
        bullish = sum(1 for p in probs if p > 0.5)
        bearish = sum(1 for p in probs if p < 0.5)
        neutral = sum(1 for p in probs if p == 0.5)

        if bullish >= bearish:
            majority = bullish
            consensus = 1
        else:
            majority = bearish
            consensus = -1
        if bullish == bearish:
            consensus = 0

        direction_agreement = majority / n

        # 2. Confidence spread (std dev)
        mean_prob = sum(probs) / n
        variance = sum((p - mean_prob) ** 2 for p in probs) / n
        confidence_spread = variance ** 0.5

        # 3. Composite disagreement score [0, 1]
        # Higher = more disagreement
        direction_disagreement = 1.0 - direction_agreement
        # Normalize spread: spread of 0.25 (max for binary divergence) → 1.0
        spread_normalized = min(confidence_spread / 0.25, 1.0)
        disagreement_score = 0.6 * direction_disagreement + 0.4 * spread_normalized

        # 4. Persistence tracking
        history = self._consensus_history[symbol]
        history.append(consensus)
        if len(history) > self._max_history:
            history.pop(0)

        persistence_bars = 0
        for past in reversed(history):
            if past == consensus:
                persistence_bars += 1
            else:
                break

        # 5. Agreement-to-disagreement detection
        regime_shift = False
        if len(history) >= 3:
            # Was high agreement (< threshold) and now high disagreement
            recent_agreement = direction_agreement
            # Check if previous 2 bars had agreement > threshold
            prev_consensus_stable = (
                len(set(history[-3:-1])) == 1  # previous 2 bars same direction
                and history[-1] != history[-2]  # current differs
            )
            if prev_consensus_stable and disagreement_score > self._disagreement_threshold:
                regime_shift = True

        # 6. Compute modifier
        if disagreement_score > self._disagreement_threshold:
            # High disagreement: scale down
            modifier = max(
                self._min_modifier,
                1.0 - (disagreement_score - self._disagreement_threshold) / (1.0 - self._disagreement_threshold),
            )
        else:
            modifier = 1.0

        # Extra penalty for regime shift
        if regime_shift:
            modifier *= 0.8

        modifier = max(self._min_modifier, modifier)

        return DisagreementResult(
            direction_agreement=round(direction_agreement, 4),
            confidence_spread=round(confidence_spread, 6),
            disagreement_score=round(disagreement_score, 4),
            consensus_direction=consensus,
            modifier=round(modifier, 4),
            persistence_bars=persistence_bars,
            regime_shift=regime_shift,
        )
