"""
APEX Regime Classifier — shared/core/regime.py

Phase 3: Rule-based regime detection using SMA cross × vol level × vol trend.

Regime labels:
  0 = UNKNOWN        (suppress new entries)
  1 = TRENDING_UP    (SMA50 > SMA200, low vol)
  2 = TRENDING_DOWN  (SMA50 < SMA200, low vol)
  3 = RANGE          (low vol, not expanding)
  4 = VOLATILE       (high vol or expanding)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Regime constants
REGIME_UNKNOWN = 0
REGIME_TRENDING_UP = 1
REGIME_TRENDING_DOWN = 2
REGIME_RANGE = 3
REGIME_VOLATILE = 4

REGIME_NAMES = {
    REGIME_UNKNOWN: "unknown",
    REGIME_TRENDING_UP: "trending_up",
    REGIME_TRENDING_DOWN: "trending_down",
    REGIME_RANGE: "range",
    REGIME_VOLATILE: "volatile",
}

# Feature flag
ENABLE_REGIME_DETECTION: bool = (
    os.environ.get("ENABLE_REGIME_DETECTION", "false").lower() == "true"
)


class RegimeClassifier:
    """
    Rule-based regime classifier.

    Uses SMA cross × volatility level × volatility trend to determine
    current market regime for a symbol.

    Parameters
    ----------
    vol_80th_percentile : float
        Threshold for "high volatility" — 80th percentile of historical
        20-day realized vol. Default 0.25 (25% annualized).
    vol_expansion_ratio : float
        Ratio of 5-day vol to 20-day vol indicating expansion. Default 1.2.
    """

    def __init__(
        self,
        vol_80th_percentile: float = 0.25,
        vol_expansion_ratio: float = 1.2,
    ) -> None:
        self._vol_threshold = vol_80th_percentile
        self._expansion_ratio = vol_expansion_ratio

    def classify(self, features: dict[str, Any]) -> int:
        """
        Classify regime from feature dict.

        Required keys: sma_50, sma_200, realized_vol_20d, vol_ratio_5_20.
        """
        sma_50 = float(features.get("sma_50", 0.0))
        sma_200 = float(features.get("sma_200", 0.0))
        vol = float(features.get("realized_vol_20d", 0.0))
        vol_ratio = float(features.get("vol_ratio_5_20", 1.0))

        sma_trend = sma_50 > sma_200
        vol_high = vol > self._vol_threshold
        vol_expanding = vol_ratio > self._expansion_ratio

        if vol_high or vol_expanding:
            return REGIME_VOLATILE
        elif sma_trend and not vol_high:
            return REGIME_TRENDING_UP
        elif not sma_trend and not vol_high:
            return REGIME_TRENDING_DOWN
        elif not vol_high and not vol_expanding:
            return REGIME_RANGE
        else:
            return REGIME_UNKNOWN

    def classify_from_contract(self, fv: Any) -> int:
        """Classify from a FeatureVector dataclass instance."""
        return self.classify({
            "sma_50": fv.sma_50,
            "sma_200": fv.sma_200,
            "realized_vol_20d": fv.realized_vol_20d,
            "vol_ratio_5_20": fv.vol_ratio_5_20,
        })

    @staticmethod
    def regime_name(regime: int) -> str:
        """Return human-readable regime name."""
        return REGIME_NAMES.get(regime, "unknown")
