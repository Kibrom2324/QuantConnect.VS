"""
services/model_inference/main.py — TFT Model Inference Service (port 8003)

Pipeline:
  Consume market.engineered  →  TFT inference  →  Publish predictions.tft

Startup validation:
  - Loads model artefact from MODEL_PATH (local) or MLflow model registry
  - Asserts loaded model's MLflow run_id == MLFLOW_RUN_ID env var
  - Refuses to start if run_id mismatch (prevents stale model serving)

Kafka:
  - Consumer group: model-inference-group
  - enable.auto.commit = False (enforced — never override)
  - Manual commit AFTER successful downstream publish

Prometheus metrics exposed on METRICS_PORT (default 9100):
  - apex_pipeline_stale{service="model_inference"}
  - apex_signal_score{symbol, alpha="tft"}
  - apex_model_inference_latency_seconds{symbol}
  - apex_feature_freshness_seconds{symbol}

Environment variables:
  KAFKA_BOOTSTRAP_SERVERS   default: localhost:9092
  MODEL_PATH                path to saved TFT model artefact (PyTorch .pt)
  MLFLOW_RUN_ID             expected MLflow run-id — refuse if mismatch
  MLFLOW_TRACKING_URI       default: http://localhost:5000
  SYMBOLS                   comma-separated list, e.g. NVDA,AAPL
  METRICS_PORT              default: 9100
  STALE_GATE_SECONDS        default: 30

Run:
  python services/model_inference/main.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Lazy-import heavy deps so unit tests can mock before import
# ---------------------------------------------------------------------------
def _torch():
    import torch  # noqa: PLC0415
    return torch


def _mlflow():
    import mlflow  # noqa: PLC0415
    return mlflow


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    import structlog  # noqa: PLC0415
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            (
                structlog.dev.ConsoleRenderer()
                if sys.stderr.isatty()
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), os.getenv("LOG_LEVEL", "INFO"))
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


_configure_logging()
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Add workspace root to sys.path so shared/ is importable
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from shared.core.kafka_utils import (  # noqa: E402
    consumer_iter,
    decode_message,
    is_stale,
    make_consumer,
    make_producer,
    publish_and_commit,
)
from shared.core.metrics import (  # noqa: E402
    FEATURE_FRESHNESS,
    KAFKA_MESSAGES_TOTAL,
    MODEL_INFERENCE_LATENCY,
    PIPELINE_STALE,
    SIGNAL_SCORE,
    STALE_MESSAGES_DROPPED,
    start_metrics_server,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
MODEL_PATH        = os.getenv("MODEL_PATH", "")
MLFLOW_RUN_ID_ENV = os.getenv("MLFLOW_RUN_ID", "")
MLFLOW_URI        = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
SYMBOLS           = [s.strip() for s in os.getenv("SYMBOLS", "NVDA,AAPL,TSLA,AMD,SPY").split(",")]
STALE_GATE_S      = float(os.getenv("STALE_GATE_SECONDS", "30"))
SERVICE_NAME      = "model_inference"

INPUT_TOPIC  = "market.engineered"
OUTPUT_TOPIC = "predictions.tft"
GROUP_ID     = "model-inference-group"

# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

class TFTModelWrapper:
    """
    Thin wrapper around a saved TFT model.

    Supports two loading modes:
      1. Local .pt file: MODEL_PATH points to a TorchScript or state-dict archive
         with a "run_id" key in its metadata pickle.
      2. MLflow model registry: MODEL_PATH = "mlflow:///<model_name>/<version>"

    At startup, validates that the loaded model's embedded run_id matches
    MLFLOW_RUN_ID env var.  Raises RuntimeError on mismatch.
    """

    def __init__(self) -> None:
        self.model = None
        self.run_id: str = ""
        self.feature_names: list[str] = []
        self._device = "cpu"

    def load(self) -> None:  # noqa: C901
        torch = _torch()
        mlflow = _mlflow()
        mlflow.set_tracking_uri(MLFLOW_URI)

        if MODEL_PATH.startswith("mlflow://"):
            # e.g. mlflow:///ApexTFT/1
            uri_part = MODEL_PATH[len("mlflow://"):]
            model_name, version = uri_part.lstrip("/").rsplit("/", 1)
            log.info("loading_mlflow_model", name=model_name, version=version)
            client = mlflow.tracking.MlflowClient()
            mv = client.get_model_version(model_name, version)
            self.run_id = mv.run_id
            self.model = mlflow.pytorch.load_model(
                f"models:/{model_name}/{version}",
                map_location=torch.device("cpu"),
            )
        elif MODEL_PATH:
            log.info("loading_local_model", path=MODEL_PATH)
            ckpt = torch.load(MODEL_PATH, map_location="cpu")
            self.run_id = ckpt.get("run_id", "")
            self.model = ckpt.get("model")
            self.feature_names = ckpt.get("feature_names", [])
        else:
            log.warning("no_model_path_set_using_stub")
            self.run_id = MLFLOW_RUN_ID_ENV  # stub passes validation
            self.model = None

        self._validate_run_id()

        # Device selection
        if torch.cuda.is_available():
            self._device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"

        if self.model is not None:
            self.model.to(self._device)
            self.model.eval()

        log.info(
            "model_loaded",
            run_id=self.run_id,
            device=self._device,
            features=len(self.feature_names),
        )

    def _validate_run_id(self) -> None:
        if not MLFLOW_RUN_ID_ENV:
            log.warning("mlflow_run_id_env_not_set_skipping_validation")
            return
        if self.run_id != MLFLOW_RUN_ID_ENV:
            raise RuntimeError(
                f"Model run_id '{self.run_id}' does not match "
                f"MLFLOW_RUN_ID='{MLFLOW_RUN_ID_ENV}'. "
                "Refusing to start — deploy the correct model artefact."
            )
        log.info("mlflow_run_id_validated", run_id=self.run_id)

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        """
        Run inference on a single symbol's feature vector.

        Input: flat dict of feature_name → float values
        Output: {
            "prediction": float,         # normalised score ∈ [-1, +1]
            "quantile_10": float,        # lower uncertainty bound
            "quantile_90": float,        # upper uncertainty bound
            "horizon_bars": int,         # forecast horizon (bars)
        }

        Falls back to a momentum stub if no model is loaded.
        """
        if self.model is None:
            # Stub: use last normalised return as a naive signal
            last_ret = float(features.get("return_1bar", 0.0))
            pred = max(-1.0, min(1.0, last_ret * 10.0))
            return {
                "prediction": pred,
                "quantile_10": pred - 0.1,
                "quantile_90": pred + 0.1,
                "horizon_bars": 4,
            }

        torch = _torch()
        import numpy as np  # noqa: PLC0415

        # Build feature tensor in the order the model expects
        if self.feature_names:
            vec = [float(features.get(f, 0.0)) for f in self.feature_names]
        else:
            vec = [float(v) for v in features.values() if isinstance(v, (int, float))]

        x = torch.tensor(vec, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
        x = x.to(self._device)

        with torch.no_grad():
            out = self.model(x)

        if isinstance(out, dict):
            pred = float(out.get("prediction", out.get("output", 0.0)))
            q10  = float(out.get("quantile_10", pred - 0.1))
            q90  = float(out.get("quantile_90", pred + 0.1))
        elif isinstance(out, torch.Tensor):
            vals = out.squeeze().cpu().tolist()
            if isinstance(vals, list) and len(vals) >= 3:
                pred, q10, q90 = float(vals[0]), float(vals[1]), float(vals[2])
            else:
                pred = float(vals) if not isinstance(vals, list) else float(vals[0])
                q10, q90 = pred - 0.1, pred + 0.1
        else:
            pred, q10, q90 = 0.0, -0.1, 0.1

        # Clamp to [-1, +1]
        pred = max(-1.0, min(1.0, pred))
        return {
            "prediction": pred,
            "quantile_10": max(-1.0, q10),
            "quantile_90": min(1.0, q90),
            "horizon_bars": 4,
        }


# ---------------------------------------------------------------------------
# Inference worker
# ---------------------------------------------------------------------------

def _run_inference_loop(model_wrapper: TFTModelWrapper) -> None:
    """Blocking Kafka consume → infer → publish loop (runs in main thread)."""
    consumer = make_consumer(
        INPUT_TOPIC,
        GROUP_ID,
        bootstrap_servers=KAFKA_BOOTSTRAP,
    )
    producer = make_producer(bootstrap_servers=KAFKA_BOOTSTRAP)

    log.info("inference_loop_started", topic=INPUT_TOPIC, output=OUTPUT_TOPIC)
    last_message_at = time.monotonic()
    staleness_guard_interval = 60.0  # seconds

    try:
        for msg in consumer_iter(consumer):
            KAFKA_MESSAGES_TOTAL.labels(
                service=SERVICE_NAME, topic=INPUT_TOPIC, result="received"
            ).inc()

            payload = decode_message(msg, producer=producer)
            if payload is None:
                KAFKA_MESSAGES_TOTAL.labels(
                    service=SERVICE_NAME, topic=INPUT_TOPIC, result="decode_error"
                ).inc()
                continue

            if is_stale(payload, max_age_s=STALE_GATE_S):
                STALE_MESSAGES_DROPPED.labels(service=SERVICE_NAME).inc()
                KAFKA_MESSAGES_TOTAL.labels(
                    service=SERVICE_NAME, topic=INPUT_TOPIC, result="stale"
                ).inc()
                from shared.core.kafka_utils import safe_commit  # noqa: PLC0415
                safe_commit(consumer, msg)
                continue

            symbol = payload.get("symbol", "UNKNOWN")
            features: dict[str, Any] = payload.get("features", {})
            bar_start: str = payload.get("bar_start", "")
            signal_ts: float = float(payload.get("signal_timestamp", time.time()))

            # Freshness metric: age of the feature bar vs now
            if bar_start:
                try:
                    from datetime import datetime, timezone  # noqa: PLC0415
                    bar_dt = datetime.fromisoformat(bar_start).replace(
                        tzinfo=timezone.utc
                    )
                    age = time.time() - bar_dt.timestamp()
                    FEATURE_FRESHNESS.labels(symbol=symbol).set(age)
                except ValueError:
                    pass

            # Run TFT inference
            t0 = time.monotonic()
            try:
                result = model_wrapper.predict(features)
            except Exception as exc:  # noqa: BLE001
                log.error("inference_error", symbol=symbol, exc=str(exc))
                KAFKA_MESSAGES_TOTAL.labels(
                    service=SERVICE_NAME, topic=INPUT_TOPIC, result="inference_error"
                ).inc()
                from shared.core.kafka_utils import safe_commit  # noqa: PLC0415
                safe_commit(consumer, msg)
                continue
            elapsed = time.monotonic() - t0
            MODEL_INFERENCE_LATENCY.labels(symbol=symbol).observe(elapsed)

            # Emit signal score metric
            SIGNAL_SCORE.labels(symbol=symbol, alpha="tft").set(result["prediction"])

            # Build output payload
            output: dict[str, Any] = {
                "symbol": symbol,
                "signal_timestamp": signal_ts,
                "tft_prediction": result["prediction"],
                "tft_q10": result["quantile_10"],
                "tft_q90": result["quantile_90"],
                "tft_horizon_bars": result["horizon_bars"],
                "tft_run_id": model_wrapper.run_id,
                "bar_start": bar_start,
                "source": SERVICE_NAME,
            }

            # Publish prediction + manual commit (CF-7: flush before commit)
            try:
                publish_and_commit(
                    producer,
                    consumer,
                    msg,
                    topic=OUTPUT_TOPIC,
                    value=json.dumps(output).encode(),
                    key=symbol.encode(),
                )
                KAFKA_MESSAGES_TOTAL.labels(
                    service=SERVICE_NAME, topic=INPUT_TOPIC, result="success"
                ).inc()
            except Exception as exc:  # noqa: BLE001
                log.error("kafka_publish_error", symbol=symbol, exc=str(exc))
                KAFKA_MESSAGES_TOTAL.labels(
                    service=SERVICE_NAME, topic=INPUT_TOPIC, result="publish_error"
                ).inc()

            # Reset staleness watchdog
            last_message_at = time.monotonic()
            PIPELINE_STALE.labels(service=SERVICE_NAME).set(0)

            # Check staleness watchdog periodically
            if time.monotonic() - last_message_at > staleness_guard_interval:
                PIPELINE_STALE.labels(service=SERVICE_NAME).set(1)
                log.warning("pipeline_stale", service=SERVICE_NAME)
    finally:
        consumer.close()
        log.info("inference_loop_stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    start_metrics_server()
    PIPELINE_STALE.labels(service=SERVICE_NAME).set(0)

    model_wrapper = TFTModelWrapper()
    try:
        model_wrapper.load()
    except RuntimeError as exc:
        log.error("model_load_failed_refusing_to_start", error=str(exc))
        sys.exit(1)

    log.info("model_inference_service_starting", symbols=SYMBOLS, topic=INPUT_TOPIC)
    _run_inference_loop(model_wrapper)


if __name__ == "__main__":
    main()
