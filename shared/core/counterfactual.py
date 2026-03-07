"""
APEX Counterfactual Tracker — shared/core/counterfactual.py

Phase 4: Records all vetoed trades with prices for counterfactual
analysis. Daily labeling determines what would have happened if the
trade had been taken.

Tracks veto precision: fraction of vetoed trades that would have lost.
Target: > 50% precision (vetoing is better than random).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ENABLE_COUNTERFACTUALS: bool = (
    os.environ.get("ENABLE_COUNTERFACTUALS", "false").lower() == "true"
)


@dataclass
class VetoRecord:
    """Record of a vetoed trade for counterfactual analysis."""
    decision_id: str
    symbol: str
    direction: int          # 1=long, -1=short
    veto_reason: str
    price_at_veto: float
    calibrated_prob: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Filled later by daily labeling:
    counterfactual_exit_price: float | None = None
    counterfactual_pnl_bps: float | None = None
    would_have_won: bool | None = None


class CounterfactualTracker:
    """
    Tracks vetoed trades and computes counterfactual outcomes.

    In-memory buffer for recent vetoes. Persisted to TimescaleDB
    by the feedback worker.
    """

    def __init__(self, max_buffer: int = 1000) -> None:
        self._buffer: list[VetoRecord] = []
        self._max_buffer = max_buffer
        self._labeled_count = 0
        self._correct_vetoes = 0  # vetoes where the trade would have lost

    def record_veto(
        self,
        decision_id: str,
        symbol: str,
        direction: int,
        veto_reason: str,
        price_at_veto: float,
        calibrated_prob: float = 0.0,
    ) -> VetoRecord:
        """Record a vetoed trade for later counterfactual analysis."""
        record = VetoRecord(
            decision_id=decision_id,
            symbol=symbol,
            direction=direction,
            veto_reason=veto_reason,
            price_at_veto=price_at_veto,
            calibrated_prob=calibrated_prob,
        )
        self._buffer.append(record)
        if len(self._buffer) > self._max_buffer:
            self._buffer.pop(0)
        return record

    def label_outcomes(
        self,
        exit_prices: dict[str, float],
    ) -> int:
        """
        Label unlabeled veto records with counterfactual outcomes.

        Parameters
        ----------
        exit_prices : dict of symbol -> current/exit price

        Returns
        -------
        Number of newly labeled records.
        """
        labeled = 0
        for record in self._buffer:
            if record.would_have_won is not None:
                continue
            if record.symbol not in exit_prices:
                continue

            exit_price = exit_prices[record.symbol]
            entry_price = record.price_at_veto

            if entry_price <= 0:
                continue

            # Compute counterfactual PnL
            if record.direction == 1:  # long
                pnl_bps = ((exit_price - entry_price) / entry_price) * 10000
            else:  # short
                pnl_bps = ((entry_price - exit_price) / entry_price) * 10000

            record.counterfactual_exit_price = exit_price
            record.counterfactual_pnl_bps = round(pnl_bps, 2)
            record.would_have_won = pnl_bps > 0

            self._labeled_count += 1
            if not record.would_have_won:
                self._correct_vetoes += 1
            labeled += 1

        return labeled

    @property
    def veto_precision(self) -> float:
        """
        Fraction of vetoed trades that would have lost (correct vetoes).
        Target: > 50%.
        """
        if self._labeled_count == 0:
            return 0.0
        return self._correct_vetoes / self._labeled_count

    @property
    def unlabeled_count(self) -> int:
        return sum(1 for r in self._buffer if r.would_have_won is None)

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def get_unlabeled(self) -> list[VetoRecord]:
        """Get all unlabeled veto records."""
        return [r for r in self._buffer if r.would_have_won is None]

    def get_summary(self) -> dict[str, Any]:
        """Summary statistics for dashboard/monitoring."""
        by_reason: dict[str, int] = {}
        for r in self._buffer:
            by_reason[r.veto_reason] = by_reason.get(r.veto_reason, 0) + 1

        return {
            "total_vetoes": len(self._buffer),
            "labeled": self._labeled_count,
            "unlabeled": self.unlabeled_count,
            "veto_precision": round(self.veto_precision, 4),
            "correct_vetoes": self._correct_vetoes,
            "by_reason": by_reason,
        }
