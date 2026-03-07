"""
tests/test_ensemble_lineage.py — Phase 0 ensemble lineage tests.

Validates that EnsembleScorer.score() now returns tuple (score, prediction_ids)
and that prediction IDs are generated correctly under the feature flag.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Pre-patch deps before import
if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

WORKSPACE = Path(__file__).parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Tuple return signature
# ═══════════════════════════════════════════════════════════════════════════


class TestEnsembleTupleReturn:
    """Verify score() returns (score, prediction_ids) tuple."""

    def test_returns_tuple(self):
        from services.signal_engine.ensemble import EnsembleScorer
        scorer = EnsembleScorer()
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "tft_score": 0.5,
            "tft_ts": now,
            "xgb_score": 0.6,
            "factor_score": 0.4,
        }
        result = scorer.score(payload)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_none_returns_empty_prediction_ids(self):
        from services.signal_engine.ensemble import EnsembleScorer
        scorer = EnsembleScorer()
        # Empty payload → no scores → returns (None, [])
        result = scorer.score({})
        score, pids = result
        assert score is None
        assert pids == []

    def test_score_value_unchanged(self):
        from services.signal_engine.ensemble import EnsembleScorer
        scorer = EnsembleScorer()
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "tft_score": 0.5,
            "tft_ts": now,
            "xgb_score": 0.6,
            "factor_score": 0.4,
        }
        score, _ = scorer.score(payload)
        assert isinstance(score, float)
        assert -10.0 <= score <= 10.0  # reasonable range


# ═══════════════════════════════════════════════════════════════════════════
# Lineage flag disabled (default)
# ═══════════════════════════════════════════════════════════════════════════


class TestLineageFlagDisabled:
    """When ENABLE_PREDICTION_LINEAGE=false, prediction_ids should be empty."""

    def test_no_prediction_ids_by_default(self):
        # Ensure flag is off
        os.environ.pop("ENABLE_PREDICTION_LINEAGE", None)

        # Re-import to pick up flag value
        import importlib
        import services.signal_engine.ensemble as mod
        importlib.reload(mod)

        scorer = mod.EnsembleScorer()
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "tft_score": 0.5,
            "tft_ts": now,
            "xgb_score": 0.6,
            "factor_score": 0.4,
        }
        _, pids = scorer.score(payload)
        assert pids == []


# ═══════════════════════════════════════════════════════════════════════════
# Lineage flag enabled
# ═══════════════════════════════════════════════════════════════════════════


class TestLineageFlagEnabled:
    """When ENABLE_PREDICTION_LINEAGE=true, prediction_ids are generated."""

    def test_prediction_ids_generated(self):
        os.environ["ENABLE_PREDICTION_LINEAGE"] = "true"
        try:
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)

            scorer = ens_mod.EnsembleScorer()
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "tft_score": 0.5,
                "tft_ts": now,
                "xgb_score": 0.6,
                "factor_score": 0.4,
            }
            _, pids = scorer.score(payload)
            assert len(pids) == 3  # tft + xgb + factor
            for pid in pids:
                assert len(pid) == 32
                int(pid, 16)  # valid hex
        finally:
            os.environ.pop("ENABLE_PREDICTION_LINEAGE", None)
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)

    def test_prediction_ids_include_llm_when_present(self):
        os.environ["ENABLE_PREDICTION_LINEAGE"] = "true"
        try:
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)

            scorer = ens_mod.EnsembleScorer()
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "tft_score": 0.5,
                "tft_ts": now,
                "xgb_score": 0.6,
                "factor_score": 0.4,
                "llm_score": 0.3,
            }
            _, pids = scorer.score(payload)
            assert len(pids) == 4  # tft + xgb + factor + llm
        finally:
            os.environ.pop("ENABLE_PREDICTION_LINEAGE", None)
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)

    def test_prediction_ids_unique_per_call(self):
        os.environ["ENABLE_PREDICTION_LINEAGE"] = "true"
        try:
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)

            scorer = ens_mod.EnsembleScorer()
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "tft_score": 0.5,
                "tft_ts": now,
                "xgb_score": 0.6,
            }
            _, pids1 = scorer.score(payload)
            _, pids2 = scorer.score(payload)
            # All IDs should be unique across calls
            all_ids = pids1 + pids2
            assert len(set(all_ids)) == len(all_ids)
        finally:
            os.environ.pop("ENABLE_PREDICTION_LINEAGE", None)
            import importlib
            import services.signal_engine.ensemble as ens_mod
            importlib.reload(ens_mod)
