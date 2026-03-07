"""
APEX Smart Ensemble
Dynamic weight redistribution on model failure, weekly scipy optimization.
Default weights (TFT mode):     TFT 45% · XGB 35% · LSTM 20%
Default weights (TimesFM mode): TimesFM 45% · XGB 35% · LSTM 20%
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import numpy as np
import redis
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS: dict[str, float] = {"tft": 0.45, "xgb": 0.35, "lstm": 0.20}
DEFAULT_WEIGHTS_TIMESFM: dict[str, float] = {"timesfm": 0.45, "xgb": 0.35, "lstm": 0.20}

_TIMESFM_SERVICE_URL = os.getenv("TIMESFM_SERVICE_URL", "http://timesfm-service:8010")
_TIMESFM_HTTP_TIMEOUT = 10.0  # seconds

# How many consecutive errors before a model is considered degraded
ERROR_THRESHOLD = 5
# Hard-stop after 3 critical failures in sliding window
CRITICAL_FAILURE_THRESHOLD = 3


# ── TimesFM HTTP adapter ──────────────────────────────────────────────────────


class TimesFMHttpAdapter:
    """
    Thin wrapper that calls the TimesFM microservice via HTTP.
    Implements a .predict(features) interface matching other model objects
    so SmartEnsemble can treat it identically.

    ``features`` is expected to be a dict with a 'bars' key (list of OHLCV bars)
    or a dict with a 'symbol' key and a 'close_series' key (list of floats).
    If 'bars' is absent the adapter synthesises minimal bars from 'close_series'.
    """

    def __init__(
        self,
        symbol:    str,
        model_id:  str = "timesfm_v1",
        service_url: str = _TIMESFM_SERVICE_URL,
    ) -> None:
        """Initialise the adapter for a specific symbol."""
        self.symbol      = symbol
        self.model_id    = model_id
        self.service_url = service_url

    def predict(self, features: Any) -> float:
        """
        Call the TimesFM /predict endpoint and return the predicted close price
        normalised to a [0, 1] direction probability.

        If 'features' is a dict with 'bars', those are used directly.
        Otherwise falls back to a single bar synthesised from scalar features.

        Args:
            features: Feature dict or object consumed elsewhere in the ensemble.

        Returns:
            Float in [0, 1] representing probabilistic upward direction.

        Raises:
            RuntimeError: On HTTP failure (caught by SmartEnsemble error handler).
        """
        bars = self._extract_bars(features)
        payload = {
            "symbol":   self.symbol,
            "horizon":  "next_1h",
            "bars":     bars,
            "model_id": self.model_id,
        }
        try:
            resp = httpx.post(
                f"{self.service_url}/predict",
                json=payload,
                timeout=_TIMESFM_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"TimesFM service returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RuntimeError(f"TimesFM service unreachable: {exc}") from exc

        # Convert absolute price prediction to a direction probability:
        # We treat predicted_value > last_close as bullish (score > 0.5).
        last_close = float(bars[-1]["close"]) if bars else 0.0
        predicted  = float(data["predicted_value"])
        confidence = float(data.get("confidence", 0.5))

        if last_close > 0:
            # Blend direction signal with confidence
            direction_prob = 1.0 if predicted >= last_close else 0.0
            score = direction_prob * confidence + 0.5 * (1.0 - confidence)
        else:
            score = 0.5  # neutral when no reference price available

        return round(score, 6)

    @staticmethod
    def _extract_bars(features: Any) -> list[dict]:
        """
        Extract or synthesise OHLCV bars from an arbitrary features object.

        Supports:
        - dict with 'bars' key  → returned directly
        - dict with 'close_series' key → converted to minimal OHLCV dicts
        - any other format → returns a single synthetic bar with close=1.0
        """
        if isinstance(features, dict):
            if "bars" in features:
                return features["bars"]
            if "close_series" in features:
                return [
                    {"open": c, "high": c, "low": c, "close": c, "volume": 0.0}
                    for c in features["close_series"]
                ]
        return [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}]


class ModelHealth:
    def __init__(self, name: str):
        self.name:           str   = name
        self.healthy:        bool  = True
        self.error_count:    int   = 0
        self.last_error:     str   = ""
        self.last_error_ts:  float = 0.0
        self.latency_ms:     float = 0.0   # rolling average
        self.recent_errors:  list  = []    # timestamps of last 10 errors

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "healthy":      self.healthy,
            "error_count":  self.error_count,
            "last_error":   self.last_error,
            "last_error_ts": self.last_error_ts,
            "latency_ms":   round(self.latency_ms, 2),
        }


class SmartEnsemble:
    """
    Weighted-average ensemble with:
    - Automatic weight redistribution when a component fails
    - scipy.optimize SLSQP weight optimization (weekly)
    - Emergency halt after 3 critical failures
    - Full audit trail in Redis
    """

    REDIS_WEIGHTS  = "apex:ensemble:weights"
    REDIS_PREDS    = "apex:ensemble:predictions"
    REDIS_ALERTS   = "apex:model_alerts"
    REDIS_LOG      = "apex:agent_log"
    REDIS_HALT     = "apex:kill_switch"

    # ─────────────────────────────────────────────
    # Construction
    # ─────────────────────────────────────────────

    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis   = redis_client or redis.Redis(
            host="redis", port=6379, decode_responses=True
        )
        self.health  = {k: ModelHealth(k) for k in DEFAULT_WEIGHTS}
        self._load_weights()
        self._using_timesfm = False  # toggled when TimesFM replaces TFT

    # ─────────────────────────────────────────────
    # Prediction
    # ─────────────────────────────────────────────

    def predict(
        self,
        features: Any,
        xgb_model: Any,
        lstm_model: Any,
        tft_model: Optional[Any] = None,
        timesfm_model: Optional[Any] = None,
    ) -> dict:
        """
        Run all component models, compute weighted average, return full breakdown.
        Falls back to equal weight on component failure.

        Exactly one of *tft_model* or *timesfm_model* should be supplied.
        If both are None the method operates in 2-model degraded mode.
        If TimesFM is down its weight is redistributed between XGB and LSTM.

        Returns:
          {signal, confidence, breakdown, weights_used,
           degraded_mode, halt_active, timestamp}
        """
        if self.redis.get(self.REDIS_HALT):
            return {
                "signal":     0.0,
                "confidence": 0.0,
                "halt_active": True,
                "breakdown":  {},
                "weights_used": {"tft": 0.0, "xgb": 0.0, "lstm": 0.0},
                "degraded_mode": True,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            }

        # Determine which third-component key and model object to use
        if timesfm_model is not None:
            third_key   = "timesfm"
            third_model = timesfm_model
            if not self._using_timesfm:
                self._using_timesfm = True
                # Ensure health dict tracks all active components
                if "timesfm" not in self.health:
                    self.health["timesfm"] = ModelHealth("timesfm")
                    self.health.pop("tft", None)
        elif tft_model is not None:
            third_key   = "tft"
            third_model = tft_model
            self._using_timesfm = False
        else:
            # 2-model degraded mode — no TFT/TimesFM
            third_key   = None
            third_model = None

        component_map = [
            ("xgb",     xgb_model,    self._run_model),
            ("lstm",    lstm_model,   self._run_model),
        ]
        if third_key and third_model is not None:
            component_map.append((third_key, third_model, self._run_model))

        # Determine effective default weights for the active component set
        active_keys   = {c[0] for c in component_map}
        base_defaults = DEFAULT_WEIGHTS_TIMESFM if self._using_timesfm else DEFAULT_WEIGHTS
        # Restrict to keys that are actually running
        component_defaults = {k: v for k, v in base_defaults.items() if k in active_keys}

        predictions: dict[str, float] = {}
        active: list[str] = []

        for name, model, func in component_map:
            # Ensure health entry exists for any dynamic component
            if name not in self.health:
                self.health[name] = ModelHealth(name)

            try:
                t0   = time.perf_counter()
                pred = func(name, model, features)
                ms   = (time.perf_counter() - t0) * 1_000
                self._update_latency(name, ms)

                if not self.health[name].healthy:
                    self.restore_model(name)

                predictions[name] = float(pred)
                active.append(name)

            except Exception as e:
                self._record_error(name, str(e))
                logger.warning(f"Ensemble component {name} failed: {e}")

        if not active:
            self._activate_emergency_halt("All ensemble components failed")
            return {
                "signal": 0.0, "confidence": 0.0,
                "halt_active": True, "breakdown": {},
                "weights_used": {k: 0.0 for k in component_defaults},
                "degraded_mode": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        weights  = self._get_effective_weights(active, component_defaults)
        signal   = sum(predictions.get(k, 0.0) * w for k, w in weights.items())
        conf     = self._confidence(predictions, weights)
        # Degraded when fewer than expected components responded
        degraded = len(active) < len(component_map)

        # Ensure all base keys appear in breakdown (zero for absent)
        breakdown_keys = set(component_defaults.keys()) | set(predictions.keys())
        record = {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "signal":     round(signal, 6),
            "confidence": round(conf, 4),
            "breakdown":  {k: round(predictions.get(k, 0.0), 6) for k in breakdown_keys},
            "weights":    {k: round(w, 4) for k, w in weights.items()},
            "degraded":   degraded,
        }
        self.redis.lpush(self.REDIS_PREDS, json.dumps(record))
        self.redis.ltrim(self.REDIS_PREDS, 0, 4999)

        return {
            "signal":       round(signal, 6),
            "confidence":   round(conf, 4),
            "halt_active":  False,
            "breakdown":    record["breakdown"],
            "weights_used": weights,
            "degraded_mode": degraded,
            "timestamp":    record["timestamp"],
        }

    # ─────────────────────────────────────────────
    # Weight helpers
    # ─────────────────────────────────────────────

    def get_weights(self) -> dict[str, float]:
        raw = self.redis.get(self.REDIS_WEIGHTS)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        return dict(DEFAULT_WEIGHTS)

    def set_weights(self, weights: dict[str, float], source: str = "manual") -> None:
        total = sum(weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Weights must sum to 1.0 (got {total:.4f})")
        self.redis.set(self.REDIS_WEIGHTS, json.dumps(weights))
        weight_str = "  ".join(f"{k.upper()} {v:.0%}" for k, v in weights.items())
        self._log(
            "ENSEMBLE_WEIGHTS_UPDATED",
            f"Weights set by {source}: {weight_str}",
        )

    def _load_weights(self) -> None:
        self.current_weights = self.get_weights()

    def _get_effective_weights(
        self,
        active: list[str],
        base_defaults: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """Normalize base weights to only healthy models."""
        if base_defaults is None:
            base_defaults = DEFAULT_WEIGHTS_TIMESFM if self._using_timesfm else DEFAULT_WEIGHTS
        stored = self.get_weights()
        # Merge stored weights with defaults for any key not yet persisted
        base = {**base_defaults, **{k: v for k, v in stored.items() if k in active}}
        raw     = {k: base.get(k, 0.1) for k in active}
        total   = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()} | {
            k: 0.0 for k in base_defaults if k not in active
        }

    # ─────────────────────────────────────────────
    # Weight optimization (scipy SLSQP)
    # ─────────────────────────────────────────────

    def optimize_weights(self, lookback_days: int = 28) -> dict[str, float]:
        """
        Fetch recent ensemble predictions, compute per-component Sharpe,
        then minimize negative portfolio Sharpe subject to w ≥ 0, sum(w)=1.
        Falls back to current weights if insufficient data.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        raw    = self.redis.lrange(self.REDIS_PREDS, 0, -1)

        records = []
        for r in raw:
            try:
                d = json.loads(r)
                if d.get("timestamp", "") >= cutoff:
                    records.append(d)
            except Exception:
                pass

        if len(records) < 50:
            logger.warning(f"Only {len(records)} records — skipping optimization")
            return self.get_weights()

        # Use the active weight set (timesfm or tft) as the canonical key order
        active_defaults = DEFAULT_WEIGHTS_TIMESFM if self._using_timesfm else DEFAULT_WEIGHTS
        active_keys     = list(active_defaults.keys())

        comps  = {k: [] for k in active_keys}
        actual = []
        for rec in records:
            bd  = rec.get("breakdown", {})
            sig = rec.get("signal", 0.0)
            actual.append(sig)
            for k in active_keys:
                comps[k].append(bd.get(k, 0.0))

        R = np.column_stack([comps[k] for k in active_keys])
        if R.shape[0] < 2:
            return self.get_weights()

        def neg_sharpe(w: np.ndarray) -> float:
            port   = R @ w
            excess = port - 0.0
            return -(excess.mean() / (excess.std() + 1e-9)) * np.sqrt(252)

        x0  = np.array(list(active_defaults.values()))
        res = minimize(
            neg_sharpe,
            x0,
            method="SLSQP",
            bounds=[(0.05, 0.80)] * len(active_keys),
            constraints={"type": "eq", "fun": lambda w: w.sum() - 1.0},
        )

        if not res.success:
            logger.warning(f"Weight optimization did not converge: {res.message}")
            return self.get_weights()

        new_w = {k: float(round(v, 4)) for k, v in zip(active_keys, res.x)}
        # Force exact sum=1 by adjusting the first key
        diff  = 1.0 - sum(new_w.values())
        new_w[active_keys[0]] = round(new_w[active_keys[0]] + diff, 4)

        current = self.get_weights()
        self.set_weights(new_w, source="optimizer")
        parts = "  ".join(
            f"{k.upper()} {current.get(k, 0):.0%}→{new_w[k]:.0%}" for k in active_keys
        )
        logger.info(f"Weights optimized. {parts}")
        return new_w

    # ─────────────────────────────────────────────
    # Health management
    # ─────────────────────────────────────────────

    def _record_error(self, name: str, error: str) -> None:
        h = self.health[name]
        h.error_count   += 1
        h.last_error     = error
        h.last_error_ts  = time.time()
        h.recent_errors.append(time.time())
        # Keep last 10
        h.recent_errors  = h.recent_errors[-10:]

        critical_window  = [t for t in h.recent_errors if time.time() - t < 300]
        if len(critical_window) >= CRITICAL_FAILURE_THRESHOLD:
            self._activate_emergency_halt(
                f"{name.upper()} failed {len(critical_window)}× in 5 minutes"
            )
            return

        if h.error_count >= ERROR_THRESHOLD and h.healthy:
            h.healthy = False
            alert = {
                "id":        f"alert-{time.time()}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type":      "MODEL_DEGRADED",
                "severity":  "HIGH",
                "model_id":  name,
                "details":   f"{name.upper()} reached {h.error_count} errors — excluded from ensemble",
                "dismissed": False,
            }
            self.redis.lpush(self.REDIS_ALERTS, json.dumps(alert))
            self._log(
                "ENSEMBLE_DEGRADED",
                f"{name.upper()} excluded from ensemble after {h.error_count} errors"
            )

    def check_health(self) -> dict[str, Any]:
        return {
            "degraded_mode":   any(not h.healthy for h in self.health.values()),
            "healthy_models":  [k for k, h in self.health.items() if h.healthy],
            "degraded_models": [k for k, h in self.health.items() if not h.healthy],
            "models":          {k: h.to_dict() for k, h in self.health.items()},
            "current_weights": self.get_weights(),
            "halt_active":     bool(self.redis.get(self.REDIS_HALT)),
        }

    def restore_model(self, name: str) -> None:
        h = self.health.get(name)
        if h:
            h.healthy     = True
            h.error_count = 0
            h.recent_errors = []
            self._log("ENSEMBLE_RESTORED", f"{name.upper()} restored to ensemble")

    def _activate_emergency_halt(self, reason: str) -> None:
        self.redis.set(self.REDIS_HALT, "1")
        self._log("EMERGENCY_HALT", f"HALT TRIGGERED: {reason}")
        logger.critical(f"EMERGENCY HALT: {reason}")

    # ─────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _run_model(_name: str, model: Any, features: Any) -> float:
        """Thin wrapper — model.predict(features) → scalar."""
        result = model.predict(features)
        if hasattr(result, "__len__"):
            return float(result[0])
        return float(result)

    def _update_latency(self, name: str, ms: float) -> None:
        h = self.health[name]
        h.latency_ms = (h.latency_ms * 0.9) + (ms * 0.1)

    @staticmethod
    def _confidence(preds: dict[str, float], weights: dict[str, float]) -> float:
        """
        Confidence = 1 - normalized variance across component predictions.
        Range [0, 1]; higher = more agreement.
        """
        vals = [preds[k] for k in preds]
        arr  = np.array(vals)
        if arr.std() < 1e-9:
            return 1.0
        spread = arr.std() / (abs(arr.mean()) + 1e-9)
        return float(max(0.0, 1.0 - min(spread, 1.0)))

    def _log(self, event_type: str, details: str) -> None:
        entry = {
            "id":        f"ens-{time.time()}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type":      event_type,
            "details":   details,
            "source":    "ensemble",
        }
        self.redis.lpush(self.REDIS_LOG, json.dumps(entry))
        self.redis.ltrim(self.REDIS_LOG, 0, 999)
