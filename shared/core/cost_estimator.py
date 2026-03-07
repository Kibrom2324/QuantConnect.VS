"""
APEX Cost Estimator — shared/core/cost_estimator.py

Phase 1: Estimates execution costs (spread + market impact + slippage)
and computes net edge. Vetoes signals with negative net edge.

Cost model:
  total_cost_bps = spread_bps + impact_bps + SLIPPAGE_BPS
  impact_bps = IMPACT_COEF * sqrt(order_dollar_value / adv_20d)
  net_edge_bps = raw_edge_bps - total_cost_bps
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default slippage assumption (bps)
SLIPPAGE_BPS: float = 2.0

# Market impact coefficient — calibrated from Almgren-Chriss style model
# impact_bps = IMPACT_COEF * sqrt(participation_rate)
IMPACT_COEF: float = 10.0


@dataclass
class CostEstimate:
    spread_bps: float
    impact_bps: float
    slippage_bps: float
    total_cost_bps: float
    raw_edge_bps: float
    net_edge_bps: float


class ExecutionCostEstimator:
    """
    Estimates execution costs for a proposed trade and computes net edge.

    Parameters
    ----------
    slippage_bps : float
        Fixed slippage assumption in basis points.
    impact_coef : float
        Market impact coefficient.
    """

    def __init__(
        self,
        slippage_bps: float = SLIPPAGE_BPS,
        impact_coef: float = IMPACT_COEF,
    ) -> None:
        self._slippage_bps = slippage_bps
        self._impact_coef = impact_coef

    def estimate(
        self,
        raw_edge_bps: float,
        spread_bps: float,
        order_dollar_value: float,
        adv_20d: float,
    ) -> CostEstimate:
        """
        Compute estimated execution cost and net edge.

        Parameters
        ----------
        raw_edge_bps : expected alpha in basis points
        spread_bps : current bid-ask spread in basis points
        order_dollar_value : notional value of the order in dollars
        adv_20d : 20-day average daily dollar volume

        Returns
        -------
        CostEstimate with breakdown and net_edge_bps
        """
        # Market impact: square-root model
        if adv_20d > 0:
            participation = order_dollar_value / adv_20d
            impact_bps = self._impact_coef * (participation ** 0.5)
        else:
            # Conservative: assume high impact if ADV unknown
            impact_bps = self._impact_coef

        total_cost_bps = spread_bps + impact_bps + self._slippage_bps
        net_edge = raw_edge_bps - total_cost_bps

        return CostEstimate(
            spread_bps=spread_bps,
            impact_bps=round(impact_bps, 4),
            slippage_bps=self._slippage_bps,
            total_cost_bps=round(total_cost_bps, 4),
            raw_edge_bps=raw_edge_bps,
            net_edge_bps=round(net_edge, 4),
        )

    def should_veto(self, cost: CostEstimate) -> bool:
        """Return True if net edge is non-positive (cost exceeds alpha)."""
        return cost.net_edge_bps <= 0.0
