"""
RSI Alpha — services/lean_alpha/rsi_alpha.py

Computes a Relative Strength Index (RSI) signal from a price series.
Signal convention:
  +1.0 → strongly oversold  (buy)
   0.0 → neutral
  -1.0 → strongly overbought (sell)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AlphaSignal:
    symbol: str
    value: float      # -1.0 (sell) to +1.0 (buy)
    confidence: float # 0.0 to 1.0
    source: str


def compute_rsi(prices: list[float], period: int = 14) -> float:
    """
    Wilder RSI (non-smoothed EMA version).
    Returns a value in [0, 100].  Returns 50.0 if there are insufficient bars.
    """
    if len(prices) < period + 1:
        return 50.0

    arr = np.asarray(prices, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial averages
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # Wilder smoothing over remaining deltas
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_signal(
    symbol: str,
    prices: list[float],
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> AlphaSignal:
    """
    Convert RSI to a normalised [-1, +1] signal.
      RSI <= oversold  → +1.0 (buy)
      RSI >= overbought → -1.0 (sell)
      Between → linearly interpolated
    """
    rsi = compute_rsi(prices, period)

    if rsi <= oversold:
        value = 1.0
        confidence = min(1.0, (oversold - rsi) / oversold)
    elif rsi >= overbought:
        value = -1.0
        confidence = min(1.0, (rsi - overbought) / (100.0 - overbought))
    else:
        # Linear scale from +1 at oversold to -1 at overbought
        mid = (oversold + overbought) / 2.0
        span = (overbought - oversold) / 2.0
        value = -(rsi - mid) / span
        confidence = abs(value) * 0.5  # low confidence in the neutral zone

    return AlphaSignal(
        symbol=symbol,
        value=round(float(value), 4),
        confidence=round(float(confidence), 4),
        source="rsi",
    )
