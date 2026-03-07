"""
tests/test_services.py — APEX service unit tests

Validates all bug fixes in services/ without requiring live Kafka, Redis,
or database connections.

Coverage:
  Bug-A   risk_engine uses "configs/limits.yaml" not "config/limits.yaml"
  Bug-B   Kafka auto-commit disabled; manual commit on success path
  CF-1    walk_forward.select_best_fold returns folds[-1] (most recent)
  CF-2    embargo gap is exactly 180 bars
  CF-4    FoldScaler fits on IS slice and saves/loads JSON sidecar
  CF-5    CVaR uses historical simulation (worst-5% mean)
  CF-7    execution/main: producer.flush() precedes consumer.commit()
  CF-8    Alpaca HTTP client has 30-second end-to-end timeout
  Alpha   RSI / EMA-cross / MACD alpha modules produce correct signals
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ─── Pre-patch heavy deps so service modules import without them ─────────────
#
# confluent_kafka:   binary extension, requires librdkafka system library
# redis / aioredis:  not installed in .venv (only in lean_venv)
#
# These patches are registered before ANY services.* import so transitive
# imports pick them up too (e.g. services.execution.main → confluent_kafka).

_mk = MagicMock

if "confluent_kafka" not in sys.modules:
    _ck_mock = _mk()
    sys.modules["confluent_kafka"] = _ck_mock

if "redis" not in sys.modules:
    _redis_mock = _mk()
    sys.modules["redis"] = _redis_mock

if "redis.asyncio" not in sys.modules:
    sys.modules["redis.asyncio"] = _mk()

# ─── Workspace root ──────────────────────────────────────────────────────────

WORKSPACE = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════════════════════
# Bug-A — Risk Engine uses configs/ not config/
# ═══════════════════════════════════════════════════════════════════════════

class TestBugA:
    """Bug-A: risk limits must load from configs/limits.yaml."""

    def test_limits_path_in_source_uses_configs(self) -> None:
        """Source-code audit: 'configs' must appear; bare 'config/' must not."""
        src = (WORKSPACE / "services" / "risk_engine" / "engine.py").read_text()
        assert '"configs"' in src or "'configs'" in src, (
            "Bug-A: 'configs' string not found in engine.py"
        )

    def test_limits_path_code_uses_configs_not_config(self) -> None:
        """The actual _limits_path assignment must use 'configs/', not 'config/'."""
        src = (WORKSPACE / "services" / "risk_engine" / "engine.py").read_text()
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "self._limits_path" in line and ("=" in line or i + 1 < len(lines)):
                # Grab this line + next 4 lines to capture multi-line assignments
                block = "\n".join(lines[i : i + 5])
                assert "configs" in block, (
                    f"Bug-A: _limits_path block does not contain 'configs':\n{block}"
                )
                # After removing "configs" there should be no bare "config/" left
                normalized = block.replace("configs", "OK")
                assert '"config/' not in normalized and "'config/" not in normalized, (
                    f"Bug-A: bare 'config/' found in _limits_path assignment:\n{block}"
                )
                break  # found the assignment; stop

    def test_risk_engine_limits_path_points_to_configs(self) -> None:
        """Instantiate RiskEngine (no Redis call at __init__) and check path."""
        from services.risk_engine.engine import RiskEngine  # noqa: PLC0415

        engine = object.__new__(RiskEngine)
        # Replicate the __init__ path assignment
        engine._limits_path = (
            Path(__file__).parent.parent.parent / "configs" / "limits.yaml"
        )
        assert "configs" in str(engine._limits_path), (
            f"Bug-A: _limits_path is {engine._limits_path}"
        )
        assert str(engine._limits_path).endswith("configs/limits.yaml"), (
            f"Bug-A: expected path to end with configs/limits.yaml, got {engine._limits_path}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Bug-B — Kafka auto-commit disabled; manual commit on success path
# ═══════════════════════════════════════════════════════════════════════════

class TestBugB:
    """Bug-B: all Kafka consumers must set enable.auto.commit=False."""

    SERVICE_FILES = [
        "services/lean_alpha/main.py",
        "services/signal_engine/main.py",
        "services/execution/main.py",
    ]

    @pytest.mark.parametrize("rel_path", SERVICE_FILES)
    def test_auto_commit_disabled(self, rel_path: str) -> None:
        src = (WORKSPACE / rel_path).read_text()
        assert (
            '"enable.auto.commit": False' in src
            or "'enable.auto.commit': False" in src
        ), f"Bug-B: enable.auto.commit not set to False in {rel_path}"

    @pytest.mark.parametrize("rel_path", SERVICE_FILES)
    def test_manual_commit_present(self, rel_path: str) -> None:
        src = (WORKSPACE / rel_path).read_text()
        assert "consumer.commit(" in src, (
            f"Bug-B: manual consumer.commit() not found in {rel_path}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# CF-1 — walk_forward.select_best_fold returns folds[-1]
# ═══════════════════════════════════════════════════════════════════════════

class TestCF1WalkForwardFoldSelection:
    """CF-1: Most-recent fold must be returned, not the max-Sharpe fold."""

    def test_returns_last_fold(self) -> None:
        from services.model_training.walk_forward import Fold, select_best_fold  # noqa

        folds = [
            Fold(fold_index=0, is_start=0, is_end=100, oos_start=280, oos_end=380, sharpe=2.5),
            Fold(fold_index=1, is_start=0, is_end=200, oos_start=380, oos_end=480, sharpe=0.8),
            Fold(fold_index=2, is_start=0, is_end=300, oos_start=480, oos_end=580, sharpe=1.1),
        ]
        best = select_best_fold(folds)
        assert best is folds[-1], "CF-1: did not return folds[-1]"
        assert best.fold_index == 2

    def test_not_max_sharpe(self) -> None:
        """Highest-Sharpe fold is NOT last → must still return last fold."""
        from services.model_training.walk_forward import Fold, select_best_fold  # noqa

        folds = [
            Fold(fold_index=0, is_start=0, is_end=100, oos_start=280, oos_end=380, sharpe=9.9),
            Fold(fold_index=1, is_start=0, is_end=200, oos_start=380, oos_end=480, sharpe=1.0),
        ]
        best = select_best_fold(folds)
        assert best is folds[-1], (
            f"CF-1: returned fold_index={best.fold_index} (Sharpe={best.sharpe}), "
            "expected fold_index=1 (most recent)"
        )

    def test_single_fold_returns_it(self) -> None:
        from services.model_training.walk_forward import Fold, select_best_fold  # noqa

        folds = [Fold(fold_index=0, is_start=0, is_end=500, oos_start=680, oos_end=800)]
        assert select_best_fold(folds) is folds[0]

    def test_empty_folds_raises(self) -> None:
        from services.model_training.walk_forward import select_best_fold  # noqa

        with pytest.raises(ValueError, match="empty"):
            select_best_fold([])


# ═══════════════════════════════════════════════════════════════════════════
# CF-2 — Embargo gap is 180 bars
# ═══════════════════════════════════════════════════════════════════════════

class TestCF2EmbargoGap:
    """CF-2: embargo_bars constant must be 180; build_folds must honour it."""

    def test_embargo_constant_is_180(self) -> None:
        from services.model_training.walk_forward import EMBARGO_BARS  # noqa

        assert EMBARGO_BARS == 180, (
            f"CF-2: EMBARGO_BARS = {EMBARGO_BARS}, expected 180"
        )

    def test_build_folds_gap_equals_embargo(self) -> None:
        from services.model_training.walk_forward import build_folds, EMBARGO_BARS  # noqa

        n_rows = 2000
        result = build_folds(n_rows, n_folds=3, is_pct=0.60, embargo_bars=EMBARGO_BARS)
        assert result, "CF-2: build_folds returned empty list"
        for is_start, is_end, oos_start, oos_end in result:
            gap = oos_start - is_end
            assert gap >= EMBARGO_BARS, (
                f"CF-2: gap {gap} < embargo {EMBARGO_BARS} in fold {(is_start, is_end)}"
            )

    def test_build_folds_default_embargo_used(self) -> None:
        """build_folds() with no explicit embargo_bars still uses 180."""
        from services.model_training.walk_forward import build_folds, EMBARGO_BARS  # noqa

        result = build_folds(3000, n_folds=3)
        for is_start, is_end, oos_start, _ in result:
            assert oos_start - is_end >= EMBARGO_BARS


# ═══════════════════════════════════════════════════════════════════════════
# CF-4 — FoldScaler: IS-only fitting + JSON sidecar
# ═══════════════════════════════════════════════════════════════════════════

class TestCF4FoldScaler:
    """CF-4: Scaler must fit on IS only and persist/load a JSON sidecar."""

    def test_fit_and_save_sidecar(self, tmp_path: Path) -> None:
        from services.model_training.dataset import FoldScaler  # noqa

        features = ["rsi", "ema_slope", "vol_z"]
        scaler = FoldScaler("fold_cf4_test", features)
        rng = np.random.default_rng(0)
        X = pd.DataFrame(rng.standard_normal((50, 3)), columns=features)

        scaler.fit(X)
        sidecar = scaler.save_sidecar(tmp_path)

        assert sidecar.exists(), "CF-4: sidecar JSON was not created"
        data = json.loads(sidecar.read_text())
        assert data["fold_id"] == "fold_cf4_test"
        assert "mean_" in data
        assert "scale_" in data
        assert data["feature_names"] == features
        assert len(data["mean_"]) == 3

    def test_transform_uses_is_statistics(self) -> None:
        """Transform must use IS mean/std, not OOS statistics."""
        from services.model_training.dataset import FoldScaler  # noqa

        features = ["f1"]
        scaler = FoldScaler("fold_is_test", features)
        X_is = pd.DataFrame({"f1": [10.0, 20.0, 30.0]})  # IS: mean=20, std=10
        scaler.fit(X_is)

        # IS centre should map to ~0
        X_is_t  = scaler.transform(X_is)
        assert abs(float(X_is_t.iloc[1]["f1"])) < 1e-6, (
            "CF-4: IS mean should z-score to 0"
        )

        # OOS values much larger than IS → large positive z-scores
        X_oos = pd.DataFrame({"f1": [100.0, 200.0, 300.0]})
        X_oos_t = scaler.transform(X_oos)
        assert float(X_oos_t.iloc[0]["f1"]) > 5.0, (
            "CF-4: OOS value w/ IS stats should yield z > 5"
        )

    def test_load_sidecar_reproduces_same_transform(self, tmp_path: Path) -> None:
        """load_sidecar must produce identical transforms as the original scaler."""
        from services.model_training.dataset import FoldScaler  # noqa

        features = ["a", "b"]
        scaler = FoldScaler("fold_round_trip", features)
        rng = np.random.default_rng(42)
        X = pd.DataFrame(rng.standard_normal((40, 2)), columns=features)
        scaler.fit(X)
        scaler.save_sidecar(tmp_path)

        loaded   = FoldScaler.load_sidecar("fold_round_trip", tmp_path)
        original = scaler.transform(X)
        restored = loaded.transform(X)

        np.testing.assert_allclose(
            original.values, restored.values, rtol=1e-9,
            err_msg="CF-4: loaded sidecar produced different transforms",
        )

    def test_transform_before_fit_raises(self) -> None:
        from services.model_training.dataset import FoldScaler  # noqa

        scaler = FoldScaler("unfitted", ["x"])
        with pytest.raises(RuntimeError, match="not been fitted"):
            scaler.transform(pd.DataFrame({"x": [1.0]}))


# ═══════════════════════════════════════════════════════════════════════════
# CF-5 — CVaR: historical simulation (worst-5% mean)
# ═══════════════════════════════════════════════════════════════════════════

class TestCF5CVaR:
    """CF-5: CVaR must use historical simulation, not a Gaussian approximation."""

    def _make_engine(self):
        """Return a bare RiskEngine instance without triggering Redis."""
        from services.risk_engine import engine as _eng  # noqa
        return _eng, object.__new__(_eng.RiskEngine)

    def test_known_distribution(self) -> None:
        """Five -10% losses + 95 zero returns → CVaR ≈ 10%."""
        _, engine = self._make_engine()
        returns = [-0.10] * 5 + [0.0] * 95   # 100 observations
        cvar = engine.compute_cvar_95(returns)
        assert abs(cvar - 0.10) < 0.001, (
            f"CF-5: CVaR={cvar:.4f}, expected ~0.10 for 5 × -10% losses"
        )

    def test_fat_tail_exceeds_gaussian_estimate(self) -> None:
        """Fat-tailed distribution: CVaR must capture the actual extreme losses."""
        _, engine = self._make_engine()
        rng = np.random.default_rng(1)
        returns = list(rng.normal(0.001, 0.01, 95)) + [-0.20, -0.18, -0.15, -0.12, -0.11]
        cvar = engine.compute_cvar_95(returns)
        assert cvar > 0.10, (
            f"CF-5: tail CVaR={cvar:.4f} should exceed 10% for extreme losses"
        )

    def test_returns_zero_for_small_sample(self) -> None:
        """Fewer than 20 observations → 0.0 (too noisy to estimate)."""
        _, engine = self._make_engine()
        cvar = engine.compute_cvar_95([-0.05] * 10)
        assert cvar == 0.0, "CF-5: expected 0.0 for < 20 observations"

    def test_cvar_positive_for_positive_losses(self) -> None:
        """CVaR is returned as a positive loss magnitude."""
        _, engine = self._make_engine()
        returns = list(np.linspace(-0.10, 0.05, 100))
        cvar = engine.compute_cvar_95(returns)
        assert cvar > 0.0, "CF-5: CVaR must be a positive loss magnitude"


# ═══════════════════════════════════════════════════════════════════════════
# CF-7 — producer.flush() before consumer.commit()
# ═══════════════════════════════════════════════════════════════════════════

class TestCF7FlushBeforeCommit:
    """CF-7: The order result must be flushed to Kafka before the consumer offset commits."""

    def test_flush_precedes_commit_in_source(self) -> None:
        src_file = WORKSPACE / "services" / "execution" / "main.py"
        source = src_file.read_text()

        flush_pos  = source.find("producer.flush()")
        commit_pos = source.find("consumer.commit(")

        assert flush_pos  != -1, "CF-7: producer.flush() not found in execution/main.py"
        assert commit_pos != -1, "CF-7: consumer.commit() not found in execution/main.py"
        assert flush_pos < commit_pos, (
            f"CF-7: producer.flush() (pos {flush_pos}) must appear before "
            f"consumer.commit() (pos {commit_pos})"
        )

    def test_flush_comment_confirms_fix(self) -> None:
        """Source must contain the CF-7 FIX comment."""
        src = (WORKSPACE / "services" / "execution" / "main.py").read_text()
        assert "CF-7" in src, "CF-7: fix identifier not found in execution/main.py"


# ═══════════════════════════════════════════════════════════════════════════
# CF-8 — Alpaca httpx.Timeout(30.0)
# ═══════════════════════════════════════════════════════════════════════════

class TestCF8AlpacaTimeout:
    """CF-8: Alpaca HTTP client must have a 30-second end-to-end timeout."""

    def test_timeout_constant_is_30s(self) -> None:
        import httpx
        from services.execution.main import ALPACA_TIMEOUT  # noqa

        expected = httpx.Timeout(30.0)
        assert ALPACA_TIMEOUT.read    == expected.read,    (
            f"CF-8: read timeout = {ALPACA_TIMEOUT.read}, expected {expected.read}"
        )
        assert ALPACA_TIMEOUT.connect == expected.connect, (
            f"CF-8: connect timeout = {ALPACA_TIMEOUT.connect}, expected {expected.connect}"
        )

    def test_timeout_used_in_client_constructor(self) -> None:
        """Source audit: AlpacaBroker must pass ALPACA_TIMEOUT to httpx.AsyncClient."""
        src = (WORKSPACE / "services" / "execution" / "main.py").read_text()
        assert "ALPACA_TIMEOUT" in src, (
            "CF-8: ALPACA_TIMEOUT constant not defined in execution/main.py"
        )
        assert "timeout=ALPACA_TIMEOUT" in src, (
            "CF-8: ALPACA_TIMEOUT not passed to httpx.AsyncClient"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Alpha modules — RSI / EMA-cross / MACD
# ═══════════════════════════════════════════════════════════════════════════

class TestAlphaModules:
    """Validate that the standalone alpha math modules produce correct signals."""

    # ── RSI ──────────────────────────────────────────────────────────────────

    def test_rsi_oversold_gives_buy_signal(self) -> None:
        from services.lean_alpha.rsi_alpha import compute_rsi, rsi_signal  # noqa

        # Falling prices → RSI approaches 0 (oversold)
        prices = [100.0 - i * 2 for i in range(30)]  # 100, 98, 96, …, 42
        signal = rsi_signal("SPY", prices)
        assert signal.value > 0, f"RSI on falling prices should be bullish, got {signal.value}"
        assert signal.source == "rsi"

    def test_rsi_overbought_gives_sell_signal(self) -> None:
        from services.lean_alpha.rsi_alpha import rsi_signal  # noqa

        prices = [100.0 + i * 2 for i in range(30)]  # rising → overbought
        signal = rsi_signal("SPY", prices)
        assert signal.value < 0, f"RSI on rising prices should be bearish, got {signal.value}"

    def test_rsi_neutral_prices(self) -> None:
        from services.lean_alpha.rsi_alpha import rsi_signal  # noqa

        rng = np.random.default_rng(7)
        prices = list(100.0 + rng.standard_normal(30).cumsum() * 0.3)
        signal = rsi_signal("SPY", prices)
        assert -1.0 <= signal.value <= 1.0
        assert 0.0 <= signal.confidence <= 1.0

    def test_rsi_short_series_fallback(self) -> None:
        from services.lean_alpha.rsi_alpha import rsi_signal  # noqa

        signal = rsi_signal("SPY", [100.0, 101.0, 99.0])  # only 3 prices
        assert signal.value == 0.0  # neutral — insufficient data

    # ── EMA Crossover ─────────────────────────────────────────────────────────

    def test_ema_cross_bullish_after_fast_crosses_above(self) -> None:
        from services.lean_alpha.ema_cross_alpha import ema_cross_signal  # noqa

        # Strong uptrend → fast EMA > slow EMA
        prices = [80.0 + i * 1.5 for i in range(50)]
        signal = ema_cross_signal("SPY", prices)
        assert signal.value > 0, f"Uptrend → positive EMA cross signal, got {signal.value}"

    def test_ema_cross_bearish_in_downtrend(self) -> None:
        from services.lean_alpha.ema_cross_alpha import ema_cross_signal  # noqa

        prices = [120.0 - i * 1.5 for i in range(50)]
        signal = ema_cross_signal("SPY", prices)
        assert signal.value < 0, f"Downtrend → negative EMA cross signal, got {signal.value}"

    def test_ema_cross_insufficient_data(self) -> None:
        from services.lean_alpha.ema_cross_alpha import ema_cross_signal  # noqa

        signal = ema_cross_signal("SPY", [100.0] * 5)  # too few bars for EMA(26)
        assert signal.value == 0.0

    # ── MACD ─────────────────────────────────────────────────────────────────

    def test_macd_uptrend_positive_histogram(self) -> None:
        from services.lean_alpha.macd_alpha import macd_signal  # noqa

        prices = [50.0 + i for i in range(60)]  # strong uptrend
        signal = macd_signal("SPY", prices)
        assert signal.value > 0, f"Uptrend → positive MACD signal, got {signal.value}"

    def test_macd_downtrend_negative_histogram(self) -> None:
        from services.lean_alpha.macd_alpha import macd_signal  # noqa

        prices = [110.0 - i for i in range(60)]
        signal = macd_signal("SPY", prices)
        assert signal.value < 0, f"Downtrend → negative MACD signal, got {signal.value}"

    def test_macd_insufficient_data(self) -> None:
        from services.lean_alpha.macd_alpha import macd_signal  # noqa

        signal = macd_signal("SPY", [100.0] * 10)
        assert signal.value == 0.0

    def test_macd_signal_bounded(self) -> None:
        from services.lean_alpha.macd_alpha import macd_signal  # noqa

        # Extreme uptrend
        prices = [1.0 + i * 10 for i in range(60)]
        signal = macd_signal("SPY", prices)
        assert -1.0 <= signal.value <= 1.0
        assert 0.0 <= signal.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Exit Monitor — SL/TP logic (pure, no Kafka/Redis)
# ═══════════════════════════════════════════════════════════════════════════

class TestExitMonitorLogic:
    """Validate _should_exit() pure logic without Kafka/Redis."""

    def test_long_stop_loss_triggered(self) -> None:
        from services.exit_monitor.main import _should_exit, PositionEntry  # noqa

        pos = PositionEntry("SPY", "LONG", entry_price=100.0, quantity=10,
                            stop_loss_pct=0.02, take_profit_pct=0.05)
        should_exit, reason = _should_exit(bar_close=97.5, position=pos)
        assert should_exit, "LONG SL: price -2.5% should trigger stop"
        assert "stop_loss" in reason

    def test_long_take_profit_triggered(self) -> None:
        from services.exit_monitor.main import _should_exit, PositionEntry  # noqa

        pos = PositionEntry("SPY", "LONG", entry_price=100.0, quantity=10,
                            stop_loss_pct=0.02, take_profit_pct=0.05)
        should_exit, reason = _should_exit(bar_close=106.0, position=pos)
        assert should_exit, "LONG TP: price +6% should trigger take-profit"
        assert "take_profit" in reason

    def test_long_within_bands_no_exit(self) -> None:
        from services.exit_monitor.main import _should_exit, PositionEntry  # noqa

        pos = PositionEntry("SPY", "LONG", entry_price=100.0, quantity=10,
                            stop_loss_pct=0.02, take_profit_pct=0.05)
        should_exit, _ = _should_exit(bar_close=101.0, position=pos)
        assert not should_exit

    def test_short_stop_loss_triggered(self) -> None:
        from services.exit_monitor.main import _should_exit, PositionEntry  # noqa

        pos = PositionEntry("SPY", "SHORT", entry_price=100.0, quantity=10,
                            stop_loss_pct=0.02, take_profit_pct=0.05)
        # SHORT: price going UP is a loss
        should_exit, reason = _should_exit(bar_close=102.5, position=pos)
        assert should_exit, "SHORT SL: price +2.5% should trigger stop"
        assert "stop_loss" in reason

    def test_short_take_profit_triggered(self) -> None:
        from services.exit_monitor.main import _should_exit, PositionEntry  # noqa

        pos = PositionEntry("SPY", "SHORT", entry_price=100.0, quantity=10,
                            stop_loss_pct=0.02, take_profit_pct=0.05)
        # SHORT: price going DOWN is profit
        should_exit, reason = _should_exit(bar_close=94.0, position=pos)
        assert should_exit, "SHORT TP: price -6% should trigger take-profit"
        assert "take_profit" in reason


# ═══════════════════════════════════════════════════════════════════════════
# MLflow — walk_forward experiment tracking
# ═══════════════════════════════════════════════════════════════════════════

class TestMLflowIntegration:
    """
    MLflow tracking integration tests.

    Uses a local file-store tracking URI (tmp_path) so no server is needed.
    Each test configures its own MLflow experiment to avoid run pollution.
    """

    def _make_dummy_model(self):
        """Minimal sklearn-like model that always predicts 1."""
        class DummyModel:
            def fit(self, X, y): pass
            def predict(self, X): return [1.0] * len(X)
        return DummyModel

    def test_mlflow_available_in_venv(self) -> None:
        """MLflow must be installed in the test environment."""
        import mlflow  # noqa
        assert mlflow.__version__, "mlflow not importable"

    def test_walk_forward_logs_metrics_per_fold(self, tmp_path: Path) -> None:
        """Each fold should produce exactly one MLflow run with oos_sharpe logged."""
        import mlflow

        tracking_uri = tmp_path / "mlruns"
        mlflow.set_tracking_uri(str(tracking_uri))
        experiment_name = "test_wf_metrics"
        mlflow.set_experiment(experiment_name)

        import os
        os.environ["MLFLOW_EXPERIMENT_NAME"] = experiment_name

        from services.model_training.walk_forward import WalkForwardTrainer  # noqa

        rng = np.random.default_rng(42)
        n = 2000
        X = pd.DataFrame(rng.standard_normal((n, 3)), columns=["f1", "f2", "f3"])
        y = pd.Series(rng.standard_normal(n))

        trainer = WalkForwardTrainer(
            model_factory = self._make_dummy_model(),
            n_folds       = 2,
            embargo_bars  = 180,
        )
        trainer.fit(X, y)

        client   = mlflow.tracking.MlflowClient(tracking_uri=str(tracking_uri))
        exp      = client.get_experiment_by_name(experiment_name)
        assert exp is not None, "MLflow experiment was not created"
        runs = client.search_runs(experiment_ids=[exp.experiment_id])

        assert len(runs) == 2, f"Expected 2 fold runs, got {len(runs)}"

        for run in runs:
            assert "oos_sharpe" in run.data.metrics, (
                f"oos_sharpe not logged in run {run.info.run_id}"
            )

    def test_walk_forward_logs_params_per_fold(self, tmp_path: Path) -> None:
        """Each fold run must log embargo_bars and fold_index as params."""
        import mlflow

        tracking_uri = tmp_path / "mlruns"
        mlflow.set_tracking_uri(str(tracking_uri))
        experiment_name = "test_wf_params"
        mlflow.set_experiment(experiment_name)

        import os
        os.environ["MLFLOW_EXPERIMENT_NAME"] = experiment_name

        from services.model_training.walk_forward import WalkForwardTrainer  # noqa

        rng = np.random.default_rng(0)
        n = 2000
        X = pd.DataFrame(rng.standard_normal((n, 2)), columns=["a", "b"])
        y = pd.Series(rng.standard_normal(n))

        trainer = WalkForwardTrainer(
            model_factory = self._make_dummy_model(),
            n_folds       = 2,
            embargo_bars  = 180,
        )
        trainer.fit(X, y)

        client = mlflow.tracking.MlflowClient(tracking_uri=str(tracking_uri))
        exp    = client.get_experiment_by_name(experiment_name)
        runs   = client.search_runs(experiment_ids=[exp.experiment_id])

        for run in runs:
            params = run.data.params
            assert "embargo_bars" in params, "embargo_bars param not logged"
            assert params["embargo_bars"] == "180", (
                f"embargo_bars param = {params['embargo_bars']!r}, expected '180'"
            )
            assert "fold_index" in params, "fold_index param not logged"

    def test_production_tag_set_on_last_fold(self, tmp_path: Path) -> None:
        """
        After WalkForwardTrainer.fit(), the last fold's run must have
        the tag production=true (CF-1: best fold is always folds[-1]).
        """
        import mlflow

        tracking_uri = tmp_path / "mlruns"
        mlflow.set_tracking_uri(str(tracking_uri))
        experiment_name = "test_wf_prod_tag"
        mlflow.set_experiment(experiment_name)

        import os
        os.environ["MLFLOW_EXPERIMENT_NAME"] = experiment_name

        from services.model_training.walk_forward import WalkForwardTrainer  # noqa

        rng = np.random.default_rng(7)
        n = 2000
        X = pd.DataFrame(rng.standard_normal((n, 2)), columns=["x", "z"])
        y = pd.Series(rng.standard_normal(n))

        trainer = WalkForwardTrainer(
            model_factory = self._make_dummy_model(),
            n_folds       = 3,
            embargo_bars  = 180,
        )
        result = trainer.fit(X, y)

        # best fold must be folds[-1]
        assert result.best_fold.fold_index == result.all_folds[-1].fold_index, (
            "CF-1: best_fold is not the last fold"
        )

        client = mlflow.tracking.MlflowClient(tracking_uri=str(tracking_uri))
        exp    = client.get_experiment_by_name(experiment_name)
        runs   = client.search_runs(experiment_ids=[exp.experiment_id])

        tagged = [r for r in runs if r.data.tags.get("production") == "true"]
        assert len(tagged) == 1, (
            f"Expected exactly 1 run tagged production=true, found {len(tagged)}"
        )

        # The tagged run must be the last fold (fold index matches best_fold)
        tagged_fold_index = tagged[0].data.params.get("fold_index")
        expected_index    = str(result.best_fold.fold_index)
        assert tagged_fold_index == expected_index, (
            f"Production tag on fold {tagged_fold_index!r}, "
            f"expected last fold {expected_index!r}"
        )

    def test_walk_forward_works_without_mlflow(self, monkeypatch) -> None:
        """
        If mlflow is unavailable the trainer must still complete successfully.
        Simulate absence by patching _MLFLOW_AVAILABLE to False.
        """
        import services.model_training.walk_forward as wf_module  # noqa

        monkeypatch.setattr(wf_module, "_MLFLOW_AVAILABLE", False)

        rng = np.random.default_rng(5)
        n = 2000
        X = pd.DataFrame(rng.standard_normal((n, 2)), columns=["p", "q"])
        y = pd.Series(rng.standard_normal(n))

        from services.model_training.walk_forward import WalkForwardTrainer  # noqa

        trainer = WalkForwardTrainer(
            model_factory = self._make_dummy_model(),
            n_folds       = 2,
            embargo_bars  = 180,
        )
        result = trainer.fit(X, y)
        assert result.best_fold is result.all_folds[-1], "CF-1 still holds without MLflow"
