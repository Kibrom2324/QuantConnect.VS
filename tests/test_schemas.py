"""
tests/test_schemas.py — Phase 0 data contract tests.

Validates shared/contracts/schemas.py dataclasses: round-trip serialization,
UUID generation, feature version hashing, and edge cases.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Pre-patch deps before import
if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import pytest
from datetime import datetime, timezone
from shared.contracts.schemas import (
    FeatureVector,
    ModelPrediction,
    ScoredSignal,
    DecisionRecord,
    generate_prediction_id,
    generate_signal_id,
    generate_decision_id,
    compute_feature_version,
)


# ═══════════════════════════════════════════════════════════════════════════
# UUID generators
# ═══════════════════════════════════════════════════════════════════════════


class TestUUIDGenerators:
    def test_prediction_id_is_hex(self):
        pid = generate_prediction_id()
        assert len(pid) == 32
        int(pid, 16)  # must be valid hex

    def test_signal_id_is_hex(self):
        sid = generate_signal_id()
        assert len(sid) == 32
        int(sid, 16)

    def test_decision_id_is_hex(self):
        did = generate_decision_id()
        assert len(did) == 32
        int(did, 16)

    def test_ids_are_unique(self):
        ids = {generate_prediction_id() for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════════════════════════════════════════
# Feature version hashing
# ═══════════════════════════════════════════════════════════════════════════


class TestFeatureVersion:
    def test_deterministic(self):
        features = {"rsi": 55.0, "ema_12": 150.3, "volume": 1000000}
        v1 = compute_feature_version(features)
        v2 = compute_feature_version(features)
        assert v1 == v2

    def test_order_independent(self):
        f1 = {"a": 1.0, "b": 2.0, "c": 3.0}
        f2 = {"c": 3.0, "a": 1.0, "b": 2.0}
        assert compute_feature_version(f1) == compute_feature_version(f2)

    def test_different_values_different_hash(self):
        f1 = {"rsi": 55.0}
        f2 = {"rsi": 56.0}
        assert compute_feature_version(f1) != compute_feature_version(f2)

    def test_length_is_16(self):
        v = compute_feature_version({"x": 1.0})
        assert len(v) == 16


# ═══════════════════════════════════════════════════════════════════════════
# FeatureVector
# ═══════════════════════════════════════════════════════════════════════════


class TestFeatureVector:
    def _make(self, **overrides):
        defaults = {
            "symbol": "AAPL",
            "timestamp": datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
            "feature_version": "abc123def4567890",
            "source_latency_ms": 50,
            "bar_close": 153.0,
            "bar_volume": 500000,
            "return_1d": 0.02,
            "return_5d": 0.05,
            "return_20d": 0.10,
            "log_return_1d": 0.019,
            "realized_vol_20d": 0.25,
            "vol_ratio_5_20": 1.1,
            "rsi_14": 55.0,
            "ema_12": 150.0,
            "ema_26": 148.0,
            "macd_line": 2.0,
            "macd_signal": 1.5,
            "macd_histogram": 0.5,
            "stoch_k": 60.0,
            "stoch_d": 55.0,
            "sma_50": 149.0,
            "sma_200": 140.0,
            "bb_upper": 155.0,
            "bb_lower": 145.0,
            "bb_width": 10.0,
            "spread_bps": 5.0,
            "volume_zscore_20d": 1.2,
            "dollar_volume": 76500000.0,
        }
        defaults.update(overrides)
        return FeatureVector(**defaults)

    def test_round_trip(self):
        fv = self._make()
        d = fv.to_dict()
        fv2 = FeatureVector.from_dict(d)
        assert fv.symbol == fv2.symbol
        assert fv.rsi_14 == fv2.rsi_14
        assert fv.timestamp == fv2.timestamp

    def test_to_dict_has_expected_keys(self):
        fv = self._make()
        d = fv.to_dict()
        assert "symbol" in d
        assert "timestamp" in d
        assert "rsi_14" in d
        assert "bar_close" in d

    def test_feature_version_preserved(self):
        fv = self._make()
        assert fv.feature_version == "abc123def4567890"


# ═══════════════════════════════════════════════════════════════════════════
# ModelPrediction
# ═══════════════════════════════════════════════════════════════════════════


class TestModelPrediction:
    def _make(self, **overrides):
        defaults = {
            "prediction_id": generate_prediction_id(),
            "model_name": "tft_v2",
            "model_version": "1.0.0",
            "symbol": "NVDA",
            "timestamp": datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
            "feature_version": "abc123",
            "direction_prob": 0.75,
            "expected_return_bps": 15.0,
            "confidence_raw": 0.85,
            "confidence_calibrated": None,
        }
        defaults.update(overrides)
        return ModelPrediction(**defaults)

    def test_prediction_id_set(self):
        mp = self._make()
        assert mp.prediction_id is not None
        assert len(mp.prediction_id) == 32

    def test_round_trip(self):
        mp = self._make(confidence_calibrated=0.80)
        d = mp.to_dict()
        mp2 = ModelPrediction.from_dict(d)
        assert mp.prediction_id == mp2.prediction_id
        assert mp.model_name == mp2.model_name
        assert mp.direction_prob == mp2.direction_prob
        assert mp.confidence_calibrated == mp2.confidence_calibrated

    def test_optional_fields_default(self):
        mp = self._make()
        assert mp.ood_score == 0.0
        assert mp.inference_time_ms == 0
        assert mp.staleness_age_seconds == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# ScoredSignal
# ═══════════════════════════════════════════════════════════════════════════


class TestScoredSignal:
    def _make(self, **overrides):
        defaults = {
            "signal_id": generate_signal_id(),
            "prediction_ids": [generate_prediction_id()],
            "symbol": "MSFT",
            "timestamp": datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
            "direction": 1,
            "calibrated_prob": 0.72,
        }
        defaults.update(overrides)
        return ScoredSignal(**defaults)

    def test_signal_id_set(self):
        ss = self._make()
        assert ss.signal_id is not None
        assert len(ss.signal_id) == 32

    def test_round_trip(self):
        pids = [generate_prediction_id(), generate_prediction_id()]
        ss = self._make(
            prediction_ids=pids,
            expected_edge_bps=5.0,
            disagreement_score=0.1,
        )
        d = ss.to_dict()
        ss2 = ScoredSignal.from_dict(d)
        assert ss.signal_id == ss2.signal_id
        assert ss.prediction_ids == ss2.prediction_ids
        assert ss.expected_edge_bps == ss2.expected_edge_bps

    def test_default_fields(self):
        ss = self._make()
        assert ss.ood_flag is False
        assert ss.veto_reason is None
        assert ss.feature_version == "legacy"


# ═══════════════════════════════════════════════════════════════════════════
# DecisionRecord
# ═══════════════════════════════════════════════════════════════════════════


class TestDecisionRecord:
    def _make(self, **overrides):
        defaults = {
            "decision_id": generate_decision_id(),
            "signal_id": generate_signal_id(),
            "prediction_ids": [generate_prediction_id()],
            "symbol": "GOOG",
            "timestamp": datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
            "action": "trade",
            "direction": 1,
        }
        defaults.update(overrides)
        return DecisionRecord(**defaults)

    def test_decision_id_set(self):
        dr = self._make()
        assert dr.decision_id is not None
        assert len(dr.decision_id) == 32

    def test_round_trip(self):
        dr = self._make(
            action="veto_position_limit",
            veto_reason="max_positions_reached",
            calibrated_prob=0.85,
        )
        d = dr.to_dict()
        dr2 = DecisionRecord.from_dict(d)
        assert dr.decision_id == dr2.decision_id
        assert dr.action == dr2.action
        assert dr.veto_reason == dr2.veto_reason
        assert dr.calibrated_prob == dr2.calibrated_prob

    def test_vetoed_record(self):
        dr = self._make(action="veto_position_limit", veto_reason="max_positions_reached")
        assert dr.action == "veto_position_limit"
        assert dr.order_id is None

    def test_traded_with_order_id(self):
        dr = self._make(action="trade", order_id="ord_12345")
        assert dr.order_id == "ord_12345"
