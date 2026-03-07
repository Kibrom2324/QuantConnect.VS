"""
services/signal_engine/portfolio.py — Black-Litterman portfolio construction.

Spec:
  Prior:               1/n equal-weight equilibrium
  Views (P, Q):        per-asset composite signal score as the view vector
  Uncertainty (Ω):     diagonal, scaled by (1 - |signal_confidence|)
  Output:              per-asset target weights, clipped to [-0.15, +0.15]
                       and renormalised so |weights| sums to ≤ 1.0

Algorithm (closed-form Idzorek / canonical BL):
  μ_BL = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ [(τΣ)⁻¹ π + PᵀΩ⁻¹Q]

  where:
    π   = equilibrium excess returns (equal weight prior → all zeros for simplicity)
    τ   = scaling scalar (typically 0.05)
    Σ   = identity proxy (we don't have a full covariance matrix here)
    P   = identity (one view per asset)
    Q   = view vector (signal scores as target returns)
    Ω   = diag(1 - |confidence|) × uncertainty_scale

Constraints enforced AFTER BL solve:
  - Each weight clipped to [-MAX_WEIGHT, +MAX_WEIGHT]
  - Gross exposure (sum of |weights|) clipped to MAX_GROSS_EXPOSURE
  - Renormalised so long + short sides don't exceed caps

Usage:
    from services.signal_engine.portfolio import black_litterman_weights

    weights = black_litterman_weights(
        signals={"NVDA": 0.8, "AAPL": -0.3, "SPY": 0.1},
        confidences={"NVDA": 0.9, "AAPL": 0.6, "SPY": 0.4},
    )
    # weights: {"NVDA": 0.15, "AAPL": -0.07, "SPY": 0.02}
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Hard constraints (spec requirements — never change via env)
# ---------------------------------------------------------------------------
MAX_WEIGHT         = 0.15   # max |weight| per asset after clipping
MAX_GROSS_EXPOSURE = 1.00   # max sum(|weights|)

# BL tuning parameters
_TAU               = 0.05   # τ — uncertainty in the prior
_UNCERTAINTY_SCALE = 0.10   # base uncertainty before confidence scaling


def black_litterman_weights(
    signals:     dict[str, float],
    confidences: dict[str, float] | None = None,
    *,
    tau:               float = _TAU,
    uncertainty_scale: float = _UNCERTAINTY_SCALE,
    max_weight:        float = MAX_WEIGHT,
    max_gross_exposure: float = MAX_GROSS_EXPOSURE,
) -> dict[str, float]:
    """
    Compute Black-Litterman target weights from per-asset signal scores.

    Parameters
    ----------
    signals : dict[str, float]
        Per-asset signal scores in [-1, +1].  Positive = long view.
    confidences : dict[str, float] | None
        Per-asset confidence values in [0, 1].  Defaults to 0.5 for all.
        Higher confidence → lower uncertainty diagonal → stronger view tilt.
    tau : float
        BL scaling parameter.  Default 0.05 (standard literature value).
    uncertainty_scale : float
        Base uncertainty scale before confidence adjustment.
    max_weight : float
        Maximum absolute weight per asset (hard clip).
    max_gross_exposure : float
        Maximum sum of |weights| across all assets (hard clip).

    Returns
    -------
    dict[str, float]
        Post-BL weights, clipped and normalised.  Keys match *signals* keys.
        All weights guaranteed to be in [-max_weight, +max_weight].
    """
    if not signals:
        return {}

    assets = sorted(signals)  # deterministic ordering
    n = len(assets)

    if n == 0:
        return {}

    confs = confidences or {}

    # ── Build vectors ────────────────────────────────────────────────────────
    # Q: view vector — signal scores treated as expected excess returns
    Q = np.array([float(signals[a]) for a in assets], dtype=float)

    # π: equilibrium prior — equal weight (all zeros in excess-return space)
    pi = np.zeros(n, dtype=float)

    # Σ: use identity as a proxy for the covariance matrix
    Sigma = np.eye(n, dtype=float)

    # ── Build Ω (uncertainty matrix) ─────────────────────────────────────────
    # Ω = diag( uncertainty_scale × (1 - |confidence_i|) )
    # High confidence → small uncertainty → view dominates prior
    omega_diag = np.array(
        [uncertainty_scale * (1.0 - abs(float(confs.get(a, 0.5)))) for a in assets],
        dtype=float,
    )
    # Guard against zero uncertainty (fully confident view)
    omega_diag = np.maximum(omega_diag, 1e-8)
    Omega = np.diag(omega_diag)

    # ── P matrix: identity (one view per asset) ───────────────────────────────
    P = np.eye(n, dtype=float)

    # ── Closed-form BL posterior mean ────────────────────────────────────────
    # A = (τΣ)⁻¹
    tauSigma_inv = np.linalg.inv(tau * Sigma)
    # B = PᵀΩ⁻¹P
    Omega_inv = np.diag(1.0 / np.diag(Omega))
    B = P.T @ Omega_inv @ P
    # C = (A + B)⁻¹
    C = np.linalg.inv(tauSigma_inv + B)
    # mu_BL = C ( A π + PᵀΩ⁻¹Q )
    mu_bl = C @ (tauSigma_inv @ pi + P.T @ Omega_inv @ Q)

    # ── Post-process ──────────────────────────────────────────────────────────
    weights_raw = dict(zip(assets, mu_bl.tolist()))
    return _clip_and_normalise(weights_raw, max_weight, max_gross_exposure)


def _clip_and_normalise(
    weights: dict[str, float],
    max_weight: float = MAX_WEIGHT,
    max_gross_exposure: float = MAX_GROSS_EXPOSURE,
) -> dict[str, float]:
    """
    Apply hard constraints:
      1. Clip each weight to [-max_weight, +max_weight]
      2. If gross exposure still exceeds max_gross_exposure, scale down uniformly

    These constraints are MANDATORY — never bypass them.
    """
    # Step 1: clip each weight individually
    clipped = {
        asset: max(-max_weight, min(max_weight, w))
        for asset, w in weights.items()
    }

    # Step 2: check gross exposure
    gross = sum(abs(w) for w in clipped.values())
    if gross > max_gross_exposure:
        scale = max_gross_exposure / gross
        clipped = {a: w * scale for a, w in clipped.items()}

    # Verify (should always hold after clipping)
    for asset, w in clipped.items():
        assert abs(w) <= max_weight + 1e-9, (
            f"Post-clip weight for {asset} is {w:.6f}, exceeds limit {max_weight}"
        )

    return clipped


def normalise_long_short(
    weights: dict[str, float],
) -> dict[str, float]:
    """
    Separate long and short sides, normalise each independently to ≤ MAX_WEIGHT.
    Returns combined dict.  Zero weights are included with value 0.0.
    """
    long_assets  = {a: w for a, w in weights.items() if w > 0}
    short_assets = {a: w for a, w in weights.items() if w < 0}
    zero_assets  = {a: w for a, w in weights.items() if w == 0.0}

    def _scale_side(side: dict[str, float]) -> dict[str, float]:
        total = sum(abs(w) for w in side.values())
        if total <= MAX_GROSS_EXPOSURE / 2:
            return side
        factor = (MAX_GROSS_EXPOSURE / 2) / total
        return {a: w * factor for a, w in side.items()}

    result = {}
    result.update(_scale_side(long_assets))
    result.update(_scale_side(short_assets))
    result.update(zero_assets)
    return result
