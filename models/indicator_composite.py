"""
APEX Indicator Composite — models/indicator_composite.py

Phase 2: LightGBM-based composite that replaces simple indicator voting.
Uses raw indicator values + interaction terms to produce a single
direction probability.

Interaction terms:
  - RSI × MACD histogram
  - Stochastic K × volume z-score
  - SMA cross (SMA50 > SMA200) × realized volatility

Walk-forward training with 63/21/10/21 split handled by
scripts/prepare_indicator_data.py.
"""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Feature flag for shadow mode
ENABLE_INDICATOR_COMPOSITE: bool = (
    os.environ.get("ENABLE_INDICATOR_COMPOSITE", "false").lower() == "true"
)

# Feature names used by the composite model
INDICATOR_FEATURES = [
    "rsi_14",
    "ema_12",
    "ema_26",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "stoch_k",
    "stoch_d",
    "sma_50",
    "sma_200",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "realized_vol_20d",
    "volume_zscore_20d",
    # Interaction terms (computed at inference time)
    "rsi_x_macd",
    "stoch_x_volume",
    "sma_cross_x_vol",
]


class IndicatorComposite:
    """
    LightGBM-based indicator composite model.

    Replaces simple voting (RSI > 50 → +1) with a learned model that
    captures nonlinear indicator interactions.
    """

    def __init__(self, model_path: str | None = None) -> None:
        self._model = None
        self._is_fitted = False
        if model_path and Path(model_path).exists():
            self.load(model_path)

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def extract_features(self, payload: dict[str, Any]) -> np.ndarray:
        """
        Extract indicator features and compute interaction terms.

        Parameters
        ----------
        payload : dict with raw indicator values

        Returns
        -------
        Feature vector as numpy array matching INDICATOR_FEATURES order.
        """
        rsi = float(payload.get("rsi_14", 50.0))
        ema_12 = float(payload.get("ema_12", 0.0))
        ema_26 = float(payload.get("ema_26", 0.0))
        macd_line = float(payload.get("macd_line", 0.0))
        macd_signal = float(payload.get("macd_signal", 0.0))
        macd_hist = float(payload.get("macd_histogram", 0.0))
        stoch_k = float(payload.get("stoch_k", 50.0))
        stoch_d = float(payload.get("stoch_d", 50.0))
        sma_50 = float(payload.get("sma_50", 0.0))
        sma_200 = float(payload.get("sma_200", 0.0))
        bb_upper = float(payload.get("bb_upper", 0.0))
        bb_lower = float(payload.get("bb_lower", 0.0))
        bb_width = float(payload.get("bb_width", 0.0))
        vol = float(payload.get("realized_vol_20d", 0.0))
        vol_zscore = float(payload.get("volume_zscore_20d", 0.0))

        # Interaction terms
        rsi_x_macd = rsi * macd_hist
        stoch_x_volume = stoch_k * vol_zscore
        sma_cross = 1.0 if sma_50 > sma_200 else -1.0
        sma_cross_x_vol = sma_cross * vol

        return np.array([
            rsi, ema_12, ema_26, macd_line, macd_signal, macd_hist,
            stoch_k, stoch_d, sma_50, sma_200, bb_upper, bb_lower, bb_width,
            vol, vol_zscore,
            rsi_x_macd, stoch_x_volume, sma_cross_x_vol,
        ], dtype=np.float64)

    def predict(self, payload: dict[str, Any]) -> float | None:
        """
        Predict direction probability from indicator values.

        Returns
        -------
        Probability of positive direction [0, 1], or None if model not fitted.
        """
        if not self._is_fitted or self._model is None:
            return None

        features = self.extract_features(payload).reshape(1, -1)
        prob = self._model.predict_proba(features)[0, 1]
        return float(prob)

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> "IndicatorComposite":
        """
        Train LightGBM classifier on indicator features.

        Parameters
        ----------
        X : Feature matrix (n_samples, n_features)
        y : Binary labels (0 or 1)
        **kwargs : Additional LightGBM parameters
        """
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm not installed, using sklearn GradientBoosting as fallback")
            from sklearn.ensemble import GradientBoostingClassifier
            params = {
                "n_estimators": kwargs.get("n_estimators", 200),
                "max_depth": kwargs.get("max_depth", 5),
                "learning_rate": kwargs.get("learning_rate", 0.05),
                "subsample": kwargs.get("subsample", 0.8),
                "random_state": 42,
            }
            self._model = GradientBoostingClassifier(**params)
            self._model.fit(X, y)
            self._is_fitted = True
            return self

        params = {
            "n_estimators": kwargs.get("n_estimators", 200),
            "max_depth": kwargs.get("max_depth", 5),
            "learning_rate": kwargs.get("learning_rate", 0.05),
            "subsample": kwargs.get("subsample", 0.8),
            "colsample_bytree": kwargs.get("colsample_bytree", 0.8),
            "reg_alpha": kwargs.get("reg_alpha", 0.1),
            "reg_lambda": kwargs.get("reg_lambda", 0.1),
            "random_state": 42,
            "verbose": -1,
        }
        self._model = lgb.LGBMClassifier(**params)
        self._model.fit(X, y)
        self._is_fitted = True
        return self

    def save(self, path: str) -> None:
        """Persist model to disk."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self._model, f)

    def load(self, path: str) -> "IndicatorComposite":
        """Load model from disk."""
        with open(path, "rb") as f:
            self._model = pickle.load(f)
        self._is_fitted = True
        return self
