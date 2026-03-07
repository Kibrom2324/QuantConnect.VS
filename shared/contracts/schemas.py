"""
APEX Data Contracts — shared/contracts/schemas.py

Canonical dataclasses for the APEX pipeline. All services share these
definitions to enforce consistent field names, types, and serialization.

Phase 0 deliverable: prediction lineage + calibration support.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ─── ID generators ────────────────────────────────────────────────────────────

def generate_prediction_id() -> str:
    """Generate a unique prediction ID (32-char hex UUID4)."""
    return uuid.uuid4().hex


def generate_signal_id() -> str:
    """Generate a unique signal ID (32-char hex UUID4)."""
    return uuid.uuid4().hex


def generate_decision_id() -> str:
    """Generate a unique decision ID (32-char hex UUID4)."""
    return uuid.uuid4().hex


def compute_feature_version(fields: dict[str, Any]) -> str:
    """
    Compute a deterministic content hash of a feature dict.

    Sorts keys, serialises to JSON, and returns SHA-256 hex[:16].
    Order-independent: {"a": 1, "b": 2} == {"b": 2, "a": 1}.
    """
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ─── Helper for datetime serialization ────────────────────────────────────────

def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ─── FeatureVector ────────────────────────────────────────────────────────────

@dataclass
class FeatureVector:
    symbol: str
    timestamp: datetime
    feature_version: str
    source_latency_ms: int
    # Price
    bar_close: float
    bar_volume: int
    return_1d: float
    return_5d: float
    return_20d: float
    log_return_1d: float
    # Volatility
    realized_vol_20d: float
    vol_ratio_5_20: float
    # Indicators (raw values, not votes)
    rsi_14: float
    ema_12: float
    ema_26: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    stoch_k: float
    stoch_d: float
    sma_50: float
    sma_200: float
    bb_upper: float
    bb_lower: float
    bb_width: float
    # Microstructure
    spread_bps: float
    volume_zscore_20d: float
    dollar_volume: float
    # Context
    regime_label: int = 0        # 0=unknown, 1=trend_up, 2=trend_down, 3=range, 4=volatile
    sentiment_score: float = 0.0
    days_since_earnings: int = 999

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": _dt_to_iso(self.timestamp),
            "feature_version": self.feature_version,
            "source_latency_ms": self.source_latency_ms,
            "bar_close": self.bar_close,
            "bar_volume": self.bar_volume,
            "return_1d": self.return_1d,
            "return_5d": self.return_5d,
            "return_20d": self.return_20d,
            "log_return_1d": self.log_return_1d,
            "realized_vol_20d": self.realized_vol_20d,
            "vol_ratio_5_20": self.vol_ratio_5_20,
            "rsi_14": self.rsi_14,
            "ema_12": self.ema_12,
            "ema_26": self.ema_26,
            "macd_line": self.macd_line,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "stoch_k": self.stoch_k,
            "stoch_d": self.stoch_d,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower,
            "bb_width": self.bb_width,
            "spread_bps": self.spread_bps,
            "volume_zscore_20d": self.volume_zscore_20d,
            "dollar_volume": self.dollar_volume,
            "regime_label": self.regime_label,
            "sentiment_score": self.sentiment_score,
            "days_since_earnings": self.days_since_earnings,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeatureVector":
        return cls(
            symbol=d["symbol"],
            timestamp=_iso_to_dt(d["timestamp"]),
            feature_version=d["feature_version"],
            source_latency_ms=int(d["source_latency_ms"]),
            bar_close=float(d["bar_close"]),
            bar_volume=int(d["bar_volume"]),
            return_1d=float(d["return_1d"]),
            return_5d=float(d["return_5d"]),
            return_20d=float(d["return_20d"]),
            log_return_1d=float(d["log_return_1d"]),
            realized_vol_20d=float(d["realized_vol_20d"]),
            vol_ratio_5_20=float(d["vol_ratio_5_20"]),
            rsi_14=float(d["rsi_14"]),
            ema_12=float(d["ema_12"]),
            ema_26=float(d["ema_26"]),
            macd_line=float(d["macd_line"]),
            macd_signal=float(d["macd_signal"]),
            macd_histogram=float(d["macd_histogram"]),
            stoch_k=float(d["stoch_k"]),
            stoch_d=float(d["stoch_d"]),
            sma_50=float(d["sma_50"]),
            sma_200=float(d["sma_200"]),
            bb_upper=float(d["bb_upper"]),
            bb_lower=float(d["bb_lower"]),
            bb_width=float(d["bb_width"]),
            spread_bps=float(d["spread_bps"]),
            volume_zscore_20d=float(d["volume_zscore_20d"]),
            dollar_volume=float(d["dollar_volume"]),
            regime_label=int(d.get("regime_label", 0)),
            sentiment_score=float(d.get("sentiment_score", 0.0)),
            days_since_earnings=int(d.get("days_since_earnings", 999)),
        )


# ─── ModelPrediction ──────────────────────────────────────────────────────────

@dataclass
class ModelPrediction:
    prediction_id: str              # UUID — mandatory for lineage
    symbol: str
    timestamp: datetime
    model_name: str
    model_version: str
    feature_version: str            # links to FeatureVector
    direction_prob: float
    expected_return_bps: float
    confidence_raw: float
    confidence_calibrated: float | None
    ood_score: float = 0.0
    regime_at_prediction: int = 0
    inference_time_ms: int = 0
    staleness_age_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "symbol": self.symbol,
            "timestamp": _dt_to_iso(self.timestamp),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "direction_prob": self.direction_prob,
            "expected_return_bps": self.expected_return_bps,
            "confidence_raw": self.confidence_raw,
            "confidence_calibrated": self.confidence_calibrated,
            "ood_score": self.ood_score,
            "regime_at_prediction": self.regime_at_prediction,
            "inference_time_ms": self.inference_time_ms,
            "staleness_age_seconds": self.staleness_age_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelPrediction":
        return cls(
            prediction_id=d["prediction_id"],
            symbol=d["symbol"],
            timestamp=_iso_to_dt(d["timestamp"]),
            model_name=d["model_name"],
            model_version=d["model_version"],
            feature_version=d["feature_version"],
            direction_prob=float(d["direction_prob"]),
            expected_return_bps=float(d["expected_return_bps"]),
            confidence_raw=float(d["confidence_raw"]),
            confidence_calibrated=float(d["confidence_calibrated"]) if d.get("confidence_calibrated") is not None else None,
            ood_score=float(d.get("ood_score", 0.0)),
            regime_at_prediction=int(d.get("regime_at_prediction", 0)),
            inference_time_ms=int(d.get("inference_time_ms", 0)),
            staleness_age_seconds=float(d.get("staleness_age_seconds", 0.0)),
        )


# ─── ScoredSignal ─────────────────────────────────────────────────────────────

@dataclass
class ScoredSignal:
    signal_id: str                  # UUID
    prediction_ids: list[str]       # links to all contributing predictions
    symbol: str
    timestamp: datetime
    direction: int                  # 1=long, -1=short, 0=no trade
    calibrated_prob: float
    expected_edge_bps: float = 0.0
    net_edge_bps: float | None = None
    suggested_size_pct: float | None = None
    ood_score: float = 0.0
    ood_flag: bool = False
    disagreement_score: float = 0.0
    model_weights: dict[str, float] = field(default_factory=dict)
    regime: int = 0
    veto_reason: str | None = None
    feature_version: str = "legacy"
    signal_process_version: str = "v0.1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "prediction_ids": self.prediction_ids,
            "symbol": self.symbol,
            "timestamp": _dt_to_iso(self.timestamp),
            "direction": self.direction,
            "calibrated_prob": self.calibrated_prob,
            "expected_edge_bps": self.expected_edge_bps,
            "net_edge_bps": self.net_edge_bps,
            "suggested_size_pct": self.suggested_size_pct,
            "ood_score": self.ood_score,
            "ood_flag": self.ood_flag,
            "disagreement_score": self.disagreement_score,
            "model_weights": self.model_weights,
            "regime": self.regime,
            "veto_reason": self.veto_reason,
            "feature_version": self.feature_version,
            "signal_process_version": self.signal_process_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScoredSignal":
        return cls(
            signal_id=d["signal_id"],
            prediction_ids=list(d.get("prediction_ids", [])),
            symbol=d["symbol"],
            timestamp=_iso_to_dt(d["timestamp"]),
            direction=int(d["direction"]),
            calibrated_prob=float(d["calibrated_prob"]),
            expected_edge_bps=float(d.get("expected_edge_bps", 0.0)),
            net_edge_bps=float(d["net_edge_bps"]) if d.get("net_edge_bps") is not None else None,
            suggested_size_pct=float(d["suggested_size_pct"]) if d.get("suggested_size_pct") is not None else None,
            ood_score=float(d.get("ood_score", 0.0)),
            ood_flag=bool(d.get("ood_flag", False)),
            disagreement_score=float(d.get("disagreement_score", 0.0)),
            model_weights=dict(d.get("model_weights", {})),
            regime=int(d.get("regime", 0)),
            veto_reason=d.get("veto_reason"),
            feature_version=d.get("feature_version", "legacy"),
            signal_process_version=d.get("signal_process_version", "v0.1"),
        )


# ─── DecisionRecord ──────────────────────────────────────────────────────────

@dataclass
class DecisionRecord:
    decision_id: str                # UUID
    signal_id: str
    prediction_ids: list[str]
    symbol: str
    timestamp: datetime
    action: str                     # 'trade', 'veto_ood', 'veto_cost', 'veto_risk', 'veto_killswitch', 'veto_position_limit'
    direction: int
    calibrated_prob: float = 0.0
    raw_edge_bps: float = 0.0
    net_edge_bps: float = 0.0
    estimated_cost_bps: float = 0.0
    position_size_pct: float = 0.0
    ood_score: float = 0.0
    disagreement_score: float = 0.0
    regime: int = 0
    model_weights: dict[str, float] = field(default_factory=dict)
    veto_reason: str | None = None
    order_id: str | None = None
    fill_price: float | None = None
    realized_cost_bps: float | None = None
    realized_pnl_bps: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "signal_id": self.signal_id,
            "prediction_ids": self.prediction_ids,
            "symbol": self.symbol,
            "timestamp": _dt_to_iso(self.timestamp),
            "action": self.action,
            "direction": self.direction,
            "calibrated_prob": self.calibrated_prob,
            "raw_edge_bps": self.raw_edge_bps,
            "net_edge_bps": self.net_edge_bps,
            "estimated_cost_bps": self.estimated_cost_bps,
            "position_size_pct": self.position_size_pct,
            "ood_score": self.ood_score,
            "disagreement_score": self.disagreement_score,
            "regime": self.regime,
            "model_weights": self.model_weights,
            "veto_reason": self.veto_reason,
            "order_id": self.order_id,
            "fill_price": self.fill_price,
            "realized_cost_bps": self.realized_cost_bps,
            "realized_pnl_bps": self.realized_pnl_bps,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionRecord":
        return cls(
            decision_id=d["decision_id"],
            signal_id=d.get("signal_id", "unknown"),
            prediction_ids=list(d.get("prediction_ids", [])),
            symbol=d["symbol"],
            timestamp=_iso_to_dt(d["timestamp"]),
            action=d["action"],
            direction=int(d.get("direction", 0)),
            calibrated_prob=float(d.get("calibrated_prob", 0.0)),
            raw_edge_bps=float(d.get("raw_edge_bps", 0.0)),
            net_edge_bps=float(d.get("net_edge_bps", 0.0)),
            estimated_cost_bps=float(d.get("estimated_cost_bps", 0.0)),
            position_size_pct=float(d.get("position_size_pct", 0.0)),
            ood_score=float(d.get("ood_score", 0.0)),
            disagreement_score=float(d.get("disagreement_score", 0.0)),
            regime=int(d.get("regime", 0)),
            model_weights=dict(d.get("model_weights", {})),
            veto_reason=d.get("veto_reason"),
            order_id=d.get("order_id"),
            fill_price=float(d["fill_price"]) if d.get("fill_price") is not None else None,
            realized_cost_bps=float(d["realized_cost_bps"]) if d.get("realized_cost_bps") is not None else None,
            realized_pnl_bps=float(d["realized_pnl_bps"]) if d.get("realized_pnl_bps") is not None else None,
        )
