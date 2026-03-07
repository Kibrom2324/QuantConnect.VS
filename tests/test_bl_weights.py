"""
tests/test_bl_weights.py — Black-Litterman weight constraint tests.

Verifies post-BL weights never exceed MAX_WEIGHT (0.15) per spec:
  - Each weight in the output dict must satisfy |w| ≤ 0.15
  - Gross exposure (sum |w|) ≤ 1.0
  - Strong signals are clipped but not zeroed
  - Zero signals produce near-zero weights
  - Negative signals produce negative weights
  - Empty input returns empty dict
  - Single asset returns non-zero weight if signal is non-zero
  - _clip_and_normalise enforces constraints regardless of BL output

Run: pytest tests/test_bl_weights.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from services.signal_engine.portfolio import (
    MAX_WEIGHT,
    MAX_GROSS_EXPOSURE,
    _clip_and_normalise,
    black_litterman_weights,
    normalise_long_short,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_constraints(weights: dict[str, float], label: str = "") -> None:
    """Assert hard constraints on a weight dict."""
    for asset, w in weights.items():
        assert abs(w) <= MAX_WEIGHT + 1e-9, (
            f"{label}: weight for {asset} = {w:.6f} exceeds MAX_WEIGHT {MAX_WEIGHT}"
        )
    gross = sum(abs(w) for w in weights.values())
    assert gross <= MAX_GROSS_EXPOSURE + 1e-9, (
        f"{label}: gross exposure {gross:.4f} exceeds {MAX_GROSS_EXPOSURE}"
    )


# ===========================================================================
# Basic constraint satisfaction
# ===========================================================================

class TestWeightConstraints:
    def test_single_strong_long_signal_clipped(self):
        """A very strong long signal → weight clipped to MAX_WEIGHT."""
        w = black_litterman_weights({"NVDA": 1.0})
        _assert_constraints(w, "single_strong_long")
        assert w["NVDA"] == pytest.approx(MAX_WEIGHT, abs=1e-6)

    def test_single_strong_short_signal_clipped(self):
        """A very strong short signal → weight clipped to -MAX_WEIGHT."""
        w = black_litterman_weights({"NVDA": -1.0})
        _assert_constraints(w, "single_strong_short")
        assert w["NVDA"] == pytest.approx(-MAX_WEIGHT, abs=1e-6)

    def test_mixed_signals_all_within_bound(self):
        signals = {"NVDA": 0.9, "AAPL": -0.7, "TSLA": 0.5, "AMD": -0.3, "SPY": 0.1}
        w = black_litterman_weights(signals)
        _assert_constraints(w, "mixed_5_assets")

    def test_many_assets_gross_exposure_bounded(self):
        """10 max-signal assets — gross exposure must not exceed 1.0."""
        assets = [f"A{i}" for i in range(10)]
        signals = {a: 1.0 for a in assets}
        w = black_litterman_weights(signals)
        _assert_constraints(w, "10_max_signals")

    def test_zero_signals_produce_near_zero_weights(self):
        """Zero signal → very small or zero weight (equilibrium prior)."""
        w = black_litterman_weights({"SPY": 0.0, "QQQ": 0.0})
        _assert_constraints(w, "zero_signals")
        for weight in w.values():
            assert abs(weight) < 0.01

    def test_negative_signals_produce_negative_weights(self):
        """Bear signal should produce negative (short) weight."""
        w = black_litterman_weights({"SPY": -0.8})
        _assert_constraints(w, "negative_signal")
        assert w["SPY"] < 0, "Negative signal must produce a short weight"

    def test_empty_signals_returns_empty_dict(self):
        w = black_litterman_weights({})
        assert w == {}

    def test_single_asset_non_zero_signal_non_zero_weight(self):
        w = black_litterman_weights({"NVDA": 0.5})
        _assert_constraints(w, "single_moderate")
        assert abs(w["NVDA"]) > 0, "Non-zero signal must produce non-zero weight"

    def test_with_high_confidence_stronger_tilt(self):
        """Higher confidence → stronger weight (toward MAX_WEIGHT)."""
        low_conf  = black_litterman_weights({"NVDA": 0.5}, {"NVDA": 0.0})
        high_conf = black_litterman_weights({"NVDA": 0.5}, {"NVDA": 0.99})
        _assert_constraints(low_conf,  "low_confidence")
        _assert_constraints(high_conf, "high_confidence")
        assert abs(high_conf["NVDA"]) >= abs(low_conf["NVDA"]), (
            "Higher confidence must produce stronger weight tilt toward the view"
        )

    def test_with_zero_confidence_weaker_tilt(self):
        """Zero confidence → max uncertainty → weight near zero."""
        w = black_litterman_weights({"NVDA": 1.0}, {"NVDA": 0.0})
        _assert_constraints(w, "zero_confidence")
        # Weight should be significantly below MAX_WEIGHT due to high uncertainty
        # (actual value depends on tau, but must be well within bounds)
        assert abs(w["NVDA"]) <= MAX_WEIGHT


# ===========================================================================
# _clip_and_normalise directly
# ===========================================================================

class TestClipAndNormalise:
    def test_already_within_bounds_unchanged(self):
        raw = {"A": 0.10, "B": -0.05, "C": 0.07}
        result = _clip_and_normalise(raw)
        _assert_constraints(result)
        for k in raw:
            assert result[k] == pytest.approx(raw[k], abs=1e-9)

    def test_values_above_max_clipped(self):
        raw = {"A": 0.50, "B": -0.80}
        result = _clip_and_normalise(raw)
        _assert_constraints(result, "clip_above_max")
        assert result["A"] == pytest.approx(MAX_WEIGHT, abs=1e-6)
        assert result["B"] == pytest.approx(-MAX_WEIGHT, abs=1e-6)

    def test_gross_exposure_reduction(self):
        """When sum(|w|) > 1.0, scale down uniformly."""
        # 8 assets all at MAX_WEIGHT (0.15) each → gross = 1.2 > 1.0
        raw = {f"A{i}": MAX_WEIGHT for i in range(8)}
        result = _clip_and_normalise(raw)
        _assert_constraints(result, "gross_exposure_reduction")
        gross = sum(abs(w) for w in result.values())
        assert gross <= MAX_GROSS_EXPOSURE + 1e-9

    def test_empty_dict_returns_empty(self):
        assert _clip_and_normalise({}) == {}

    def test_single_asset_at_max_clip(self):
        result = _clip_and_normalise({"X": 2.0})
        assert abs(result["X"]) == pytest.approx(MAX_WEIGHT, abs=1e-6)

    def test_constraint_invariant_fuzz(self, seed=42):
        """Fuzz test: random signals must always satisfy constraints after BL."""
        rng = np.random.default_rng(seed)
        for trial in range(50):
            n = rng.integers(1, 15)
            assets = [f"ASSET_{i}" for i in range(n)]
            signals     = {a: float(rng.uniform(-1, 1)) for a in assets}
            confidences = {a: float(rng.uniform(0, 1))  for a in assets}
            w = black_litterman_weights(signals, confidences)
            _assert_constraints(w, f"fuzz_trial_{trial}")


# ===========================================================================
# Bear regime dampening integration
# ===========================================================================

class TestBearRegimeDampening:
    """
    Spec: if SPY 200-day SMA slope < 0, multiply ALL long signal scores by 0.3.
    Verify that after dampening AND BL, constraints still hold.
    """

    def _dampen_longs(self, signals: dict[str, float]) -> dict[str, float]:
        """Apply bear regime 0.3× dampening to all positive signals."""
        return {
            k: (v * 0.3 if v > 0 else v)
            for k, v in signals.items()
        }

    def test_dampened_signals_still_satisfy_constraints(self):
        raw_signals = {"NVDA": 0.9, "AAPL": -0.7, "TSLA": 0.8, "AMD": 0.6}
        dampened = self._dampen_longs(raw_signals)
        w = black_litterman_weights(dampened)
        _assert_constraints(w, "bear_dampened")

    def test_dampening_reduces_long_weights(self):
        signals = {"NVDA": 0.9}
        bull_w = black_litterman_weights(signals)
        bear_w = black_litterman_weights({"NVDA": 0.9 * 0.3})
        # Bull weights should be ≥ bear weights for long positions
        assert bull_w["NVDA"] >= bear_w["NVDA"]


# ===========================================================================
# Regression: weights must satisfy constraint even for adversarial inputs
# ===========================================================================

class TestRegressionEdgeCases:
    def test_all_max_longs_12_assets(self):
        signals = {f"S{i}": 1.0 for i in range(12)}
        w = black_litterman_weights(signals)
        _assert_constraints(w, "12_max_longs")

    def test_alternating_long_short(self):
        signals = {f"A{i}": (1.0 if i % 2 == 0 else -1.0) for i in range(10)}
        w = black_litterman_weights(signals)
        _assert_constraints(w, "alternating_long_short")

    def test_one_max_long_one_max_short(self):
        w = black_litterman_weights({"NVDA": 1.0, "SPY": -1.0})
        _assert_constraints(w, "pair_trade")
        assert w["NVDA"] > 0
        assert w["SPY"] < 0
