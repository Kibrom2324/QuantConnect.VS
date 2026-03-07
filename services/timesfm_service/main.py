"""
APEX TimesFM Microservice
==========================
Serves Google TimesFM foundation-model predictions via HTTP API.
Exposes Prometheus metrics for Grafana monitoring.
Port: 8010

Endpoints:
  GET  /health        — liveness check
  GET  /ready         — readiness (model loaded, 503 if not)
  GET  /metrics       — Prometheus scrape endpoint
  POST /predict       — generate price forecast from OHLCV bars
  GET  /model/info    — current model metadata
  POST /model/reload  — hot-reload / re-validate model
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import redis
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field, field_validator

# ── Structured logging ────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger(__name__)

# Bootstrap stdlib logging so uvicorn / libraries still work
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="APEX TimesFM Service",
    version="1.0.0",
    description="Google TimesFM-1.0-200M inference endpoint for APEX trading platform",
)

# ── Prometheus metrics ────────────────────────────────────────────────────────

timesfm_predictions_total = Counter(
    "timesfm_predictions_total",
    "Total TimesFM predictions made",
    ["symbol", "horizon"],
)
timesfm_prediction_latency_seconds = Histogram(
    "timesfm_prediction_latency_seconds",
    "TimesFM end-to-end inference latency",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
timesfm_model_loaded = Gauge(
    "timesfm_model_loaded",
    "1 if TimesFM model is loaded and ready, 0 otherwise",
)
timesfm_confidence_score = Histogram(
    "timesfm_confidence_score",
    "Distribution of confidence scores returned by TimesFM",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
timesfm_prediction_errors_total = Counter(
    "timesfm_prediction_errors_total",
    "Prediction errors by type",
    ["error_type"],
)
timesfm_service_healthy = Gauge(
    "timesfm_service_healthy",
    "1 if service is healthy, 0 if degraded",
)
timesfm_requests_in_flight = Gauge(
    "timesfm_requests_in_flight",
    "Currently active prediction requests",
)
timesfm_model_load_attempts = Counter(
    "timesfm_model_load_attempts_total",
    "Number of model load attempts (including retries)",
)
timesfm_last_prediction_timestamp = Gauge(
    "timesfm_last_prediction_timestamp",
    "Unix timestamp of most recent successful prediction",
)

# ── Global model state ────────────────────────────────────────────────────────


class _ModelState:
    """Holds the loaded TimesFM model and metadata."""

    model: Any = None
    model_id: str = "timesfm_v1"
    is_loaded: bool = False
    loaded_at: Optional[str] = None
    backend: str = "unknown"


_state = _ModelState()

# ── Redis helper ──────────────────────────────────────────────────────────────

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
_REDIS_HOST = os.getenv("REDIS_HOST", "redis")
_REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))


def _get_redis() -> redis.Redis:
    """Return a short-lived Redis client. Caller must not hold it long."""
    return redis.Redis(
        host=_REDIS_HOST,
        port=_REDIS_PORT,
        socket_timeout=2,
        decode_responses=True,
    )


# ── Model loader ──────────────────────────────────────────────────────────────

_MAX_LOAD_RETRIES = 3
_RETRY_DELAY_SECS = 10


def _select_backend() -> str:
    """Detect GPU availability and return backend string."""
    try:
        import torch

        if torch.cuda.is_available():
            log.info("GPU detected — using cuda backend for TimesFM")
            return "gpu"
    except ImportError:
        pass
    log.info("No GPU detected — falling back to cpu backend for TimesFM")
    return "cpu"


def _load_timesfm_model() -> Any:
    """
    Instantiate and return a TimesFM model object.
    Tries GPU first; falls back to CPU on RuntimeError.
    """
    import timesfm

    backend = _select_backend()
    timesfm_model_load_attempts.inc()

    try:
        model = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(
                backend=backend,
                per_core_batch_size=32,
                horizon_len=1,
            ),
            checkpoint=timesfm.TimesFmCheckpoint(
                huggingface_repo_id="google/timesfm-1.0-200m-pytorch"
            ),
        )
        _state.backend = backend
        log.info("TimesFM model loaded", backend=backend)
        return model
    except (RuntimeError, Exception) as exc:
        if backend == "gpu":
            log.warning(
                "GPU load failed — retrying on cpu",
                error=str(exc),
            )
            timesfm_model_load_attempts.inc()
            model = timesfm.TimesFm(
                hparams=timesfm.TimesFmHparams(
                    backend="cpu",
                    per_core_batch_size=32,
                    horizon_len=1,
                ),
                checkpoint=timesfm.TimesFmCheckpoint(
                    huggingface_repo_id="google/timesfm-1.0-200m-pytorch"
                ),
            )
            _state.backend = "cpu"
            log.info("TimesFM model loaded on CPU fallback")
            return model
        raise


async def _init_model_with_retries() -> None:
    """
    Attempt to load TimesFM model up to _MAX_LOAD_RETRIES times.
    Sets Prometheus gauges and logs each attempt.
    """
    for attempt in range(1, _MAX_LOAD_RETRIES + 1):
        try:
            log.info("Loading TimesFM model", attempt=attempt, max=_MAX_LOAD_RETRIES)
            model = await asyncio.get_event_loop().run_in_executor(
                None, _load_timesfm_model
            )
            _state.model = model
            _state.is_loaded = True
            _state.loaded_at = datetime.now(timezone.utc).isoformat()
            timesfm_model_loaded.set(1)
            timesfm_service_healthy.set(1)
            log.info("TimesFM model ready", backend=_state.backend)
            return

        except Exception as exc:
            log.error(
                "TimesFM model load failed",
                attempt=attempt,
                error=str(exc),
            )
            timesfm_model_loaded.set(0)
            timesfm_service_healthy.set(0)
            if attempt < _MAX_LOAD_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SECS * attempt)

    log.critical(
        "TimesFM model failed to load after all retries — service degraded",
        retries=_MAX_LOAD_RETRIES,
    )


@app.on_event("startup")
async def _startup() -> None:
    """FastAPI startup: load model in background so /health responds immediately."""
    asyncio.create_task(_init_model_with_retries())


# ── Request / Response schemas ────────────────────────────────────────────────


class OHLCVBar(BaseModel):
    """A single OHLCV candle."""

    time: Optional[str] = None
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PredictRequest(BaseModel):
    """
    Request body for POST /predict.
    Provide at least 32 bars; up to 512 closing prices are used.
    """

    symbol: str = Field(..., description="Ticker symbol, e.g. 'NVDA'")
    horizon: str = Field("next_1h", description="Prediction horizon label")
    bars: list[OHLCVBar] = Field(..., min_length=1, description="OHLCV bars array")
    model_id: str = Field("timesfm_v1", description="Model identifier")

    @field_validator("bars")
    @classmethod
    def _bars_not_empty(cls, v: list[OHLCVBar]) -> list[OHLCVBar]:
        """Ensure at least one bar is provided."""
        if not v:
            raise ValueError("bars must contain at least one element")
        return v


class PredictResponse(BaseModel):
    """Response body for POST /predict."""

    symbol: str
    horizon: str
    predicted_value: float
    confidence: float
    timestamp: str
    model_id: str
    latency_ms: float


# ── Inference helpers ─────────────────────────────────────────────────────────


def _extract_close_prices(bars: list[OHLCVBar], max_len: int = 512) -> np.ndarray:
    """
    Extract closing prices from bars, take last max_len, return as float32 array.

    Args:
        bars:    OHLCV bar list (most-recent last expected).
        max_len: Maximum context length for TimesFM (default 512).

    Returns:
        1-D float32 numpy array of closing prices.
    """
    prices = np.array([b.close for b in bars], dtype=np.float32)
    if len(prices) > max_len:
        prices = prices[-max_len:]
    return prices


def _compute_confidence(
    quantile_forecast: np.ndarray,
    predicted_price: float,
) -> float:
    """
    Derive a [0, 1] confidence score from TimesFM quantile forecasts.

    TimesFM returns quantile levels [0.1, 0.2, ..., 0.9] by default.
    We use the q90–q10 spread relative to the predicted price as uncertainty:
        uncertainty = (q90 - q10) / |predicted_price|
        confidence  = 1 - clip(uncertainty, 0, 1)

    Args:
        quantile_forecast: Shape (n_quantiles,) array of forecasted quantiles.
        predicted_price:   The point-forecast price.

    Returns:
        Confidence score in [0.0, 1.0].
    """
    if quantile_forecast is None or len(quantile_forecast) < 2:
        return 0.5  # neutral default

    q_lo = float(quantile_forecast[0])   # first quantile (e.g. q10)
    q_hi = float(quantile_forecast[-1])  # last  quantile (e.g. q90)
    spread = abs(q_hi - q_lo)
    denom = max(abs(predicted_price), 1e-9)
    uncertainty = min(spread / denom, 1.0)
    return round(float(1.0 - uncertainty), 4)


def _run_inference(close_prices: np.ndarray) -> tuple[float, float]:
    """
    Call TimesFM model synchronously.

    Args:
        close_prices: 1-D array of closing prices.

    Returns:
        (predicted_close, confidence) tuple.
    """
    forecast_input = [close_prices]
    frequency_input = [0]  # 0 = high-frequency (intraday)

    point_forecast, quantile_forecast = _state.model.forecast(
        forecast_input,
        freq=frequency_input,
    )

    # point_forecast shape: (1, horizon_len=1)
    predicted_value = float(point_forecast[0, 0])

    # quantile_forecast shape: (1, horizon_len, n_quantiles)
    q_row: np.ndarray = quantile_forecast[0, 0]  # shape (n_quantiles,)
    confidence = _compute_confidence(q_row, predicted_value)

    return predicted_value, confidence


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest) -> PredictResponse:
    """
    Run TimesFM inference on the provided OHLCV bars.

    Extracts closing prices (up to last 512), feeds them to the
    TimesFM model, and returns the predicted next close price with
    a derived confidence score.

    Raises:
        HTTPException 503: If the model is not yet loaded.
        HTTPException 500: If inference fails for any other reason.
    """
    if not _state.is_loaded or _state.model is None:
        raise HTTPException(
            status_code=503,
            detail="TimesFM model not loaded — service is starting up",
        )

    timesfm_requests_in_flight.inc()
    t_start = time.perf_counter()

    try:
        close_prices = _extract_close_prices(req.bars)

        predicted_value, confidence = await asyncio.get_event_loop().run_in_executor(
            None, _run_inference, close_prices
        )

        elapsed = time.perf_counter() - t_start
        latency_ms = round(elapsed * 1_000, 2)

        # ── Prometheus ──────────────────────────────────────────────────────
        timesfm_predictions_total.labels(
            symbol=req.symbol, horizon=req.horizon
        ).inc()
        timesfm_prediction_latency_seconds.observe(elapsed)
        timesfm_confidence_score.observe(confidence)
        timesfm_last_prediction_timestamp.set(time.time())

        # ── Persist to Redis (non-fatal) ────────────────────────────────────
        _persist_to_redis(req.symbol, predicted_value, confidence, req.model_id)

        log.info(
            "TimesFM prediction",
            symbol=req.symbol,
            predicted=round(predicted_value, 4),
            confidence=confidence,
            latency_ms=latency_ms,
        )

        return PredictResponse(
            symbol=req.symbol,
            horizon=req.horizon,
            predicted_value=round(predicted_value, 6),
            confidence=confidence,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_id=req.model_id,
            latency_ms=latency_ms,
        )

    except HTTPException:
        raise
    except Exception as exc:
        timesfm_prediction_errors_total.labels(error_type=type(exc).__name__).inc()
        timesfm_service_healthy.set(0)
        log.error("TimesFM inference failed", error=str(exc), symbol=req.symbol)
        raise HTTPException(
            status_code=500,
            detail=f"Inference failed: {exc}",
        )
    finally:
        timesfm_requests_in_flight.dec()


def _persist_to_redis(
    symbol: str,
    predicted_value: float,
    confidence: float,
    model_id: str,
) -> None:
    """
    Write prediction to Redis for ensemble consumption and dashboard display.
    Non-fatal: logs a warning on failure but does not raise.
    """
    try:
        r = _get_redis()
        payload = json.dumps(
            {
                "symbol": symbol,
                "predicted_value": round(predicted_value, 6),
                "confidence": confidence,
                "model_id": model_id,
                "source": "timesfm",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        r.lpush("apex:timesfm:predictions", payload)
        r.ltrim("apex:timesfm:predictions", 0, 9_999)
        r.set(
            f"apex:signals:timesfm:{symbol}",
            payload,
            ex=3600,  # expire after 1 hour
        )
    except redis.RedisError as exc:
        log.warning("Redis persist failed (non-fatal)", error=str(exc))


# ── Batch predict endpoint (used by ensemble training, avoids per-sample HTTP) ──


class BatchPredictRequest(BaseModel):
    sequences: list[list[float]] = Field(
        ...,
        description="List of close-price sequences; each sequence is a 1-D list of floats",
    )


class BatchPredictResponse(BaseModel):
    prob_up: list[float] = Field(
        ...,
        description="P(UP) for each input sequence in [0, 1]",
    )
    count: int


@app.post("/predict/batch", response_model=BatchPredictResponse)
async def predict_batch(req: BatchPredictRequest) -> BatchPredictResponse:
    """
    Batch inference endpoint for ensemble training.

    Accepts a list of close-price sequences and returns P(UP) for each,
    derived from TimesFM's 1-step-ahead price forecast.
    Much faster than calling /predict in a loop.
    """
    if not _state.is_loaded or _state.model is None:
        raise HTTPException(status_code=503, detail="TimesFM model not loaded")

    def _run_batch() -> list[float]:
        seqs = [np.array(s, dtype=np.float32) for s in req.sequences]
        results: list[float] = []
        # Process in internal chunks of 2000 so one large call doesn't OOM
        _CHUNK = 2000
        for i in range(0, len(seqs), _CHUNK):
            chunk = seqs[i : i + _CHUNK]
            point_forecasts, _ = _state.model.forecast(
                inputs=chunk,
                freq=[0] * len(chunk),
            )
            for j, seq in enumerate(chunk):
                last_close = float(seq[-1]) if len(seq) > 0 else 1.0
                predicted = float(point_forecasts[j][0])
                delta_pct = (predicted - last_close) / (abs(last_close) + 1e-8)
                # Sigmoid mapping: ±1% delta ≈ 0.73/0.27 probability
                prob_up = float(1.0 / (1.0 + np.exp(-delta_pct * 100.0)))
                results.append(prob_up)
        return results

    try:
        probs = await asyncio.get_event_loop().run_in_executor(None, _run_batch)
        log.info("Batch predict", count=len(probs))
        return BatchPredictResponse(prob_up=probs, count=len(probs))
    except Exception as exc:
        log.error("Batch predict failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Batch inference failed: {exc}")


# ── Health / readiness / metrics ──────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    """
    Liveness check. Always returns 200 while the process is alive.
    Reports 'degraded' status when model is not yet loaded.
    """
    return {
        "status": "ok" if _state.is_loaded else "degraded",
        "service": "timesfm_service",
        "model_loaded": _state.is_loaded,
        "model_id": _state.model_id,
        "backend": _state.backend,
        "loaded_at": _state.loaded_at,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
async def ready() -> dict:
    """
    Readiness check. Returns 200 only when model is loaded; 503 otherwise.
    Used by Docker healthcheck and load balancers.

    Raises:
        HTTPException 503: If model is not yet loaded.
    """
    if not _state.is_loaded:
        raise HTTPException(
            status_code=503,
            detail="TimesFM model not yet loaded",
        )
    return {"ready": True, "model_id": _state.model_id}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint — exposes all timesfm_* metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/model/info")
async def model_info() -> dict:
    """Return metadata about the currently loaded model."""
    return {
        "model_id": _state.model_id,
        "model_type": "timesfm",
        "is_loaded": _state.is_loaded,
        "backend": _state.backend,
        "loaded_at": _state.loaded_at,
        "huggingface_repo": "google/timesfm-1.0-200m-pytorch",
    }


@app.post("/model/reload")
async def reload_model() -> dict:
    """
    Re-initialise the TimesFM model.
    Returns immediately; reload happens asynchronously.
    """
    _state.is_loaded = False
    _state.model = None
    timesfm_model_loaded.set(0)
    asyncio.create_task(_init_model_with_retries())
    return {"status": "reload_queued", "message": "TimesFM model reload started"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8010")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
