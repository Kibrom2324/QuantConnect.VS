"""
tests/test_metrics_phase0.py — Phase 0 metrics tests.

Validates that CALIBRATION_BRIER gauge exists and is properly defined.
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
from prometheus_client import REGISTRY


class TestCalibrationBrierMetric:
    def test_calibration_brier_exists(self):
        from shared.core.metrics import CALIBRATION_BRIER
        assert CALIBRATION_BRIER is not None

    def test_calibration_brier_is_gauge(self):
        from shared.core.metrics import CALIBRATION_BRIER
        from prometheus_client import Gauge
        assert isinstance(CALIBRATION_BRIER, Gauge)

    def test_calibration_brier_set_and_read(self):
        from shared.core.metrics import CALIBRATION_BRIER
        CALIBRATION_BRIER.set(0.123)
        # Read back via the sample value
        val = CALIBRATION_BRIER._value.get()
        assert val == pytest.approx(0.123)

    def test_existing_metrics_still_present(self):
        """Ensure adding CALIBRATION_BRIER didn't break existing metrics."""
        from shared.core.metrics import (
            PIPELINE_STALE,
            SIGNAL_SCORE,
            KILL_SWITCH_STATE,
            POSITION_MISMATCH,
            DAILY_LOSS_PCT,
            ORDER_LATENCY,
            ORDERS_TOTAL,
        )
        # All existing metrics should still be importable
        assert PIPELINE_STALE is not None
        assert SIGNAL_SCORE is not None
        assert KILL_SWITCH_STATE is not None
        assert POSITION_MISMATCH is not None
        assert DAILY_LOSS_PCT is not None
        assert ORDER_LATENCY is not None
        assert ORDERS_TOTAL is not None
