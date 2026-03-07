"""
MACD Alpha — services/lean_alpha/macd_alpha.py

Computes MACD (Moving Average Convergence/Divergence) signal.
Signal convention:
  +1.0 → MACD histogram strongly positive (bullish momentum)
  -1.0 → MACD histogram strongly negative (bearish momentum)
   0.0 → neutral / insufficient data
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.lean_alpha.rsi_alpha import AlphaSignal  # shared dataclass
from services.lean_alpha.ema_cross_alpha import _ema


def compute_macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    """
    Returns (macd_line, signal_line, histogram).
      macd_line  = EMA(fast) - EMA(slow)
      signal_line = EMA(macd_line, signal_period)
      histogram   = macd_line - signal_line
    """
    if len(prices) < slow + signal_period:
        return 0.0, 0.0, 0.0

    arr = np.asarray(prices, dtype=float)
    fast_ema = _ema(arr, fast)
    slow_ema = _ema(arr, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return float(macd_line[-1]), float(signal_line[-1]), float(histogram[-1])


def macd_signal(
    symbol: str,
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
    scale: float = 200.0,
) -> AlphaSignal:
    """
    Normalise MACD histogram to [-1, +1].
    `scale` controls sensitivity: histogram / (price * scale) controls magnitude.
    """
    if len(prices) < slow + signal_period:
        return AlphaSignal(symbol=symbol, value=0.0, confidence=0.0, source="macd")

    _, _, histogram = compute_macd(prices, fast, slow, signal_period)
    last_price = prices[-1] if prices[-1] != 0 else 1.0

    normalised = histogram / (abs(last_price) / scale)
    value = float(np.clip(normalised, -1.0, 1.0))
    confidence = min(1.0, abs(value))

    return AlphaSignal(
        symbol=symbol,
        value=round(value, 4),
        confidence=round(confidence, 4),
        source="macd",
    )
