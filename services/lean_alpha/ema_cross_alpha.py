"""
EMA Crossover Alpha — services/lean_alpha/ema_cross_alpha.py

Computes a fast/slow EMA crossover signal.
Signal convention:
  +1.0 → fast EMA crossed above slow EMA (bullish)
  -1.0 → fast EMA crossed below slow EMA (bearish)
   0.0 → no recent crossover / insufficient data
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.lean_alpha.rsi_alpha import AlphaSignal  # shared dataclass


def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Vectorised EMA using pandas-style ewm decay."""
    k = 2.0 / (period + 1)
    out = np.empty_like(prices)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def compute_ema_cross(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
) -> tuple[float, float]:
    """
    Returns (fast_ema[-1], slow_ema[-1]).
    Raises ValueError if prices is too short.
    """
    if len(prices) < slow + 1:
        raise ValueError(
            f"Need at least {slow + 1} prices for EMA({slow}); got {len(prices)}"
        )
    arr = np.asarray(prices, dtype=float)
    fast_ema = _ema(arr, fast)
    slow_ema = _ema(arr, slow)
    return float(fast_ema[-1]), float(slow_ema[-1])


def ema_cross_signal(
    symbol: str,
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
) -> AlphaSignal:
    """
    Translate EMA spread into a [-1, +1] signal.
    Signal magnitude = normalised spread relative to price level.
    """
    if len(prices) < slow + 1:
        return AlphaSignal(symbol=symbol, value=0.0, confidence=0.0, source="ema_cross")

    fast_val, slow_val = compute_ema_cross(prices, fast, slow)
    mid_price = (fast_val + slow_val) / 2.0
    if mid_price == 0:
        return AlphaSignal(symbol=symbol, value=0.0, confidence=0.0, source="ema_cross")

    spread_pct = (fast_val - slow_val) / mid_price   # positive = bullish

    # Clip signal to [-1, +1] and use |spread| as confidence proxy
    value = float(np.clip(spread_pct * 20, -1.0, 1.0))   # scale: 5% spread → full signal
    confidence = min(1.0, abs(spread_pct) * 40)

    return AlphaSignal(
        symbol=symbol,
        value=round(value, 4),
        confidence=round(confidence, 4),
        source="ema_cross",
    )
