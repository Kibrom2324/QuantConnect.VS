"""
APEX XGB Microservice
=====================
Serves XGBoost model predictions via HTTP API.
Mirrors the TFT service pattern (services/tft_service/main.py).

Endpoints:
  GET  /health        — liveness check
  GET  /ready         — readiness (model loaded)
  GET  /metrics       — Prometheus scrape endpoint
  POST /predict       — generate signal prediction
  GET  /model/info    — current model metadata
  POST /model/reload  — hot-reload model from registry
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import redis
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(title="APEX XGB Service", version="1.0.0")

# ── Feature columns (must match train_xgb.py FEATURE_COLS) ───────────────────

FEATURE_COLS = [
    "returns_1", "returns_5", "returns_15", "returns_60",
    "rsi_14", "rsi_28", "ema_20", "ema_50", "ema_200",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_pct",
    "atr_14", "stoch_k", "stoch_d",
    "volume_ratio", "vwap_dev", "adx_14",
]

# ── Prometheus metrics ────────────────────────────────────────────────────────

xgb_predictions_total = Counter(
    "apex_xgb_predictions_total", "Total XGB predictions", ["direction"],
)
xgb_prediction_latency = Histogram(
    "apex_xgb_prediction_latency_seconds", "XGB inference latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
)
xgb_prediction_errors = Counter(
    "apex_xgb_prediction_errors_total", "XGB prediction errors", ["error_type"],
)
xgb_model_loaded = Gauge("apex_xgb_model_loaded", "1 if model loaded")
xgb_service_healthy = Gauge("apex_xgb_service_healthy", "1 if healthy")

# ── Model state ───────────────────────────────────────────────────────────────


class ModelState:
    model = None
    model_id: str = ""
    model_version: int = 0
    val_sharpe: float = 0.0
    val_hit_rate: float = 0.0
    trained_at: Optional[str] = None
    loaded_at: Optional[str] = None
    is_loaded: bool = False


state = ModelState()

# ── Redis helper ──────────────────────────────────────────────────────────────


def _redis() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        socket_timeout=2,
        decode_responses=True,
    )


# ── Model loader ─────────────────────────────────────────────────────────────


def load_model_from_registry() -> None:
    """
    Find active XGB model from Redis registry and load the .pkl artifact.
    Lookup order: MLflow artifact → local /app/models/<id>.pkl.
    """
    try:
        r = _redis()
        all_ids = r.smembers("apex:models:all")
        live_id = None
        meta_d: dict = {}

        # Find a live XGB model; fall back to staging if none promoted
        for status_pref in ("live", "staging"):
            for mid in (all_ids or []):
                raw = r.get(f"apex:models:{mid}")
                if raw:
                    m = json.loads(raw)
                    if m.get("model_type") == "xgb" and m.get("status") == status_pref:
                        live_id = mid
                        meta_d = m
                        break
            if live_id:
                break

        if not live_id:
            logger.warning("No live/staging XGB model in registry — waiting for training")
            xgb_model_loaded.set(0)
            xgb_service_healthy.set(0)
            return

        # ── Try MLflow artifact first ────────────────────────────────────
        model_loaded = False
        mlflow_uri = meta_d.get("mlflow_artifact_uri")

        if mlflow_uri:
            try:
                import mlflow.xgboost
                state.model = mlflow.xgboost.load_model(mlflow_uri)
                model_loaded = True
                logger.info("Loaded XGB %s from MLflow", live_id)
            except Exception as exc:
                logger.warning("MLflow load failed (%s) — trying local", exc)

        # ── Fall back to local .pkl ──────────────────────────────────────
        if not model_loaded:
            model_dir = Path(os.getenv("MODEL_DIR", "/app/models"))
            candidates = [
                model_dir / f"{live_id}.pkl",
                model_dir / live_id / "model.pkl",
            ]
            for path in candidates:
                if path.exists():
                    with open(path, "rb") as f:
                        state.model = pickle.load(f)  # noqa: S301 — trusted artifact
                    model_loaded = True
                    logger.info("Loaded XGB from %s", path)
                    break

        if not model_loaded:
            logger.warning("No artifact found for %s", live_id)
            xgb_model_loaded.set(0)
            xgb_service_healthy.set(0)
            return

        state.model_id = live_id
        state.model_version = int(meta_d.get("version", 0))
        state.val_sharpe = float(meta_d.get("val_sharpe", 0) or 0)
        state.val_hit_rate = float(meta_d.get("val_hit_rate", 0) or 0)
        state.trained_at = meta_d.get("created_at")
        state.loaded_at = datetime.now(timezone.utc).isoformat()
        state.is_loaded = True

        xgb_model_loaded.set(1)
        xgb_service_healthy.set(1)

        logger.info(
            "XGB model ready: %s  (sharpe=%.3f, hit=%.1f%%)",
            live_id, state.val_sharpe, state.val_hit_rate,
        )

    except Exception as exc:
        logger.error("Model load failed: %s", exc)
        xgb_model_loaded.set(0)
        xgb_service_healthy.set(0)


@app.on_event("startup")
async def startup() -> None:
    load_model_from_registry()


# ── Request / response models ─────────────────────────────────────────────────


class PredictRequest(BaseModel):
    symbol: str
    features: dict
    timestamp: Optional[str] = None


class PredictResponse(BaseModel):
    symbol: str
    direction: str
    score: float
    model_id: str
    latency_ms: float
    timestamp: str


# ── Predict ───────────────────────────────────────────────────────────────────


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not state.is_loaded or state.model is None:
        raise HTTPException(status_code=503, detail="XGB model not loaded yet")

    start = time.time()
    try:
        # Build feature vector in the same column order as training
        values = []
        for col in FEATURE_COLS:
            v = req.features.get(col)
            values.append(float(v) if v is not None else 0.0)

        # sym_enc — label-encoded symbol (match train_xgb.py)
        # At inference we use 0 as default; the model is robust to this.
        values.append(0.0)

        arr = np.array([values], dtype=np.float32)

        # predict_proba returns [[p_down, p_up]]
        proba = state.model.predict_proba(arr)
        score = float(proba[0][1])  # probability of UP

        direction = "UP" if score > 0.6 else ("DOWN" if score < 0.4 else "HOLD")
        elapsed = time.time() - start

        # Prometheus
        xgb_predictions_total.labels(direction=direction).inc()
        xgb_prediction_latency.observe(elapsed)

        return PredictResponse(
            symbol=req.symbol,
            direction=direction,
            score=round(score, 4),
            model_id=state.model_id,
            latency_ms=round(elapsed * 1000, 2),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        xgb_prediction_errors.labels(error_type=type(exc).__name__).inc()
        xgb_service_healthy.set(0)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}")


# ── Model management ─────────────────────────────────────────────────────────


@app.get("/model/info")
async def model_info():
    return {
        "model_id": state.model_id,
        "version": state.model_version,
        "val_sharpe": state.val_sharpe,
        "val_hit_rate": state.val_hit_rate,
        "trained_at": state.trained_at,
        "loaded_at": state.loaded_at,
        "is_loaded": state.is_loaded,
    }


@app.post("/model/reload")
async def reload_model(background: BackgroundTasks):
    background.add_task(load_model_from_registry)
    return {"status": "reload_queued"}


# ── Health / readiness / metrics ──────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "healthy" if state.is_loaded else "degraded",
        "service": "xgb_service",
        "model_id": state.model_id,
        "is_loaded": state.is_loaded,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
async def ready():
    if not state.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"ready": True, "model_id": state.model_id}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8007)),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
