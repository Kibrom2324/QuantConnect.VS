"""
APEX Position Sizer — shared/core/position_sizer.py

Phase 4: Half-Kelly criterion for position sizing based on
calibrated probability and net-of-cost edge.

Formula:
  f* = (p * b - q) / b   at 0.5× Kelly
  where:
    p = calibrated probability of winning
    q = 1 - p
    b = expected win/loss ratio (payoff odds)

Constraints:
  - Minimum edge: 52% calibrated probability
  - Maximum size: capped by risk limits (per-symbol and portfolio)
  - Minimum size: 0.5% of portfolio
  - Maximum size: 2% of portfolio (before risk engine cap)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENABLE_KELLY_SIZING: bool = (
    os.environ.get("ENABLE_KELLY_SIZING", "false").lower() == "true"
)

# Kelly fraction (half-Kelly for safety)
KELLY_FRACTION: float = 0.5

# Minimum probability to take a position
MIN_EDGE_PROBABILITY: float = 0.52

# Position size bounds (fraction of portfolio)
MIN_POSITION_PCT: float = 0.005   # 0.5%
MAX_POSITION_PCT: float = 0.02    # 2.0%


@dataclass
class SizingResult:
    """Result of position sizing calculation."""
    position_size_pct: float    # fraction of portfolio [0, max_pct]
    kelly_raw: float            # raw Kelly fraction before constraints
    kelly_half: float           # half-Kelly
    edge_sufficient: bool       # True if probability >= min edge
    capped: bool                # True if size was constrained


class PositionSizer:
    """
    Half-Kelly position sizer.

    Sizes positions proportional to the calibrated edge, with safety
    constraints on minimum probability and maximum position size.
    """

    def __init__(
        self,
        kelly_fraction: float = KELLY_FRACTION,
        min_edge_prob: float = MIN_EDGE_PROBABILITY,
        min_position_pct: float = MIN_POSITION_PCT,
        max_position_pct: float = MAX_POSITION_PCT,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._min_edge_prob = min_edge_prob
        self._min_position_pct = min_position_pct
        self._max_position_pct = max_position_pct

    def size(
        self,
        calibrated_prob: float,
        payoff_ratio: float = 1.0,
    ) -> SizingResult:
        """
        Calculate position size using half-Kelly.

        Parameters
        ----------
        calibrated_prob : Calibrated probability of winning [0, 1]
        payoff_ratio : Expected win / expected loss magnitude.
                       Default 1.0 (symmetric payoff).

        Returns
        -------
        SizingResult with position size and diagnostics.
        """
        # Edge check: must exceed minimum probability
        if calibrated_prob < self._min_edge_prob:
            return SizingResult(
                position_size_pct=0.0,
                kelly_raw=0.0,
                kelly_half=0.0,
                edge_sufficient=False,
                capped=False,
            )

        p = calibrated_prob
        q = 1.0 - p
        b = payoff_ratio

        # Kelly formula: f* = (p*b - q) / b
        kelly_raw = (p * b - q) / b if b > 0 else 0.0

        # Half-Kelly for safety
        kelly_half = kelly_raw * self._kelly_fraction

        # Cap within bounds
        capped = False
        if kelly_half < self._min_position_pct:
            position_pct = self._min_position_pct
            capped = True
        elif kelly_half > self._max_position_pct:
            position_pct = self._max_position_pct
            capped = True
        else:
            position_pct = kelly_half

        # Never go negative
        position_pct = max(0.0, position_pct)

        return SizingResult(
            position_size_pct=round(position_pct, 6),
            kelly_raw=round(kelly_raw, 6),
            kelly_half=round(kelly_half, 6),
            edge_sufficient=True,
            capped=capped,
        )
