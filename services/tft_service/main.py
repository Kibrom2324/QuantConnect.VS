"""
APEX TFT Microservice
=====================
Serves TFT model predictions via HTTP API.
Exposes Prometheus metrics for Grafana monitoring.

Endpoints:
  GET  /health        — liveness check
  GET  /ready         — readiness (model loaded)
  GET  /metrics       — Prometheus scrape endpoint
  POST /predict       — generate signal prediction
  GET  /model/info    — current model metadata
  POST /model/reload  — hot-reload model from registry
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

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

app = FastAPI(title="APEX TFT Service", version="1.0.0")

# ── Model architecture (must match train_tft.py) ──────────────────────────────

try:
    import math
    import torch
    import torch.nn as nn

    _N_FEATURES = 21
    _SEQ_LEN    = 48

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 512, dropout: float = 0.1):
            super().__init__()
            self.dropout = nn.Dropout(dropout)
            pe = torch.zeros(max_len, d_model)
            pos = torch.arange(max_len).unsqueeze(1).float()
            div = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(pos * div)
            pe[:, 1::2] = torch.cos(pos * div[:d_model // 2])
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.dropout(x + self.pe[:, :x.size(1)])

    class ApexTFT(nn.Module):
        """Temporal Attention model — must match train_tft.py exactly."""
        def __init__(
            self,
            n_features: int = _N_FEATURES,
            d_model:    int = 64,
            n_heads:    int = 4,
            n_layers:   int = 2,
            d_ff:       int = 128,
            dropout:    float = 0.15,
        ):
            super().__init__()
            self.n_features = n_features
            self.d_model    = d_model
            self.seq_len    = _SEQ_LEN
            self.input_proj = nn.Linear(n_features, d_model)
            self.pos_enc    = PositionalEncoding(d_model, max_len=_SEQ_LEN + 8, dropout=dropout)
            self.gru = nn.GRU(d_model, d_model, num_layers=1, batch_first=True, bidirectional=False)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=n_heads, dim_feedforward=d_ff,
                dropout=dropout, activation="gelu", batch_first=True, norm_first=True,
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
            self.head = nn.Sequential(
                nn.LayerNorm(d_model), nn.Dropout(dropout),
                nn.Linear(d_model, 32), nn.GELU(), nn.Linear(32, 2),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            if x.dim() == 2:
                x = x.unsqueeze(1).expand(-1, self.seq_len, -1)
            h = self.input_proj(x)
            h = self.pos_enc(h)
            h, _ = self.gru(h)
            h = self.transformer(h)
            return self.head(h[:, -1])

    _APEX_TFT_AVAILABLE = True
except ImportError:
    _APEX_TFT_AVAILABLE = False
    logger.warning("PyTorch not available — TFT model cannot be loaded")

# ── Prometheus metrics ────────────────────────────────────────────────────────

tft_predictions_total = Counter(
    "apex_tft_predictions_total",
    "Total TFT predictions made",
    ["direction", "regime"],
)
tft_prediction_latency = Histogram(
    "apex_tft_prediction_latency_seconds",
    "TFT inference latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
tft_prediction_score = Histogram(
    "apex_tft_prediction_score",
    "TFT output confidence scores",
    buckets=[0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)
tft_prediction_errors = Counter(
    "apex_tft_prediction_errors_total",
    "TFT prediction errors",
    ["error_type"],
)

tft_model_loaded = Gauge(
    "apex_tft_model_loaded",
    "1 if model is loaded, 0 if not",
)
tft_model_version = Gauge(
    "apex_tft_model_version_number",
    "Current model version number",
)
tft_model_sharpe = Gauge(
    "apex_tft_model_val_sharpe",
    "Validation Sharpe of loaded model",
)
tft_model_hit_rate = Gauge(
    "apex_tft_model_val_hit_rate",
    "Validation hit rate of loaded model",
)
tft_model_age_seconds = Gauge(
    "apex_tft_model_age_seconds",
    "Seconds since model was trained",
)

tft_service_healthy = Gauge(
    "apex_tft_service_healthy",
    "1 if healthy, 0 if degraded",
)
tft_last_prediction_timestamp = Gauge(
    "apex_tft_last_prediction_timestamp",
    "Unix timestamp of last prediction",
)
tft_gpu_memory_mb = Gauge(
    "apex_tft_gpu_memory_mb",
    "GPU memory used by TFT model (MB)",
)
tft_requests_in_flight = Gauge(
    "apex_tft_requests_in_flight",
    "Concurrent prediction requests",
)

# ── Model state ───────────────────────────────────────────────────────────────

class ModelState:
    model            = None
    model_id: str    = ""
    model_version: int = 0
    val_sharpe: float  = 0.0
    val_hit_rate: float = 0.0
    trained_at: Optional[str]  = None
    loaded_at: Optional[str]   = None
    is_loaded: bool            = False


state = ModelState()

# ── Redis helper ──────────────────────────────────────────────────────────────

def _redis() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        socket_timeout=2,
        decode_responses=True,
    )


# ── Model loader ──────────────────────────────────────────────────────────────

async def load_model_from_registry() -> None:
    """
    Find active TFT model from Redis registry and load it.
    Tries MLflow first, falls back to local /app/models/<id>/model.pt.
    """
    try:
        r       = _redis()
        all_ids = r.smembers("apex:models:all")
        live_id = None
        meta_d: dict = {}

        for mid in (all_ids or []):
            raw = r.get(f"apex:models:{mid}")
            if raw:
                m = json.loads(raw)
                if m.get("model_type") == "tft" and m.get("status") == "live":
                    live_id = mid
                    meta_d  = m
                    break

        if not live_id:
            logger.warning("No live TFT model in registry — waiting for promotion")
            tft_model_loaded.set(0)
            tft_service_healthy.set(0)
            return

        # ── Try MLflow ────────────────────────────────────────────────────
        artifact_uri = meta_d.get("mlflow_artifact_uri")
        model_loaded = False

        if artifact_uri:
            logger.info("Loading TFT %s from MLflow …", live_id)
            try:
                import mlflow
                import torch
                state.model = mlflow.pytorch.load_model(
                    artifact_uri,
                    map_location=torch.device(
                        "cuda" if torch.cuda.is_available() else "cpu"
                    ),
                )
                state.model.eval()
                model_loaded = True
            except Exception as mlf_exc:
                logger.warning("MLflow load failed (%s) — trying local", mlf_exc)

        if not model_loaded:
            local_path = os.path.join("/app/models", live_id, "model.pt")
            if os.path.exists(local_path):
                import torch
                state.model = torch.load(local_path, map_location="cpu", weights_only=False)
                state.model.eval()
                model_loaded = True
                logger.info("Loaded TFT from local path %s", local_path)
            else:
                logger.warning("No model artefact found for %s", live_id)
                tft_model_loaded.set(0)
                tft_service_healthy.set(0)
                return

        state.model_id      = live_id
        state.model_version = int(meta_d.get("version", 0))
        state.val_sharpe    = float(meta_d.get("val_sharpe", 0) or 0)
        state.val_hit_rate  = float(meta_d.get("val_hit_rate", 0) or 0)
        state.trained_at    = meta_d.get("trained_at")
        state.loaded_at     = datetime.now(timezone.utc).isoformat()
        state.is_loaded     = True

        tft_model_loaded.set(1)
        tft_model_version.set(state.model_version)
        tft_model_sharpe.set(state.val_sharpe)
        tft_model_hit_rate.set(state.val_hit_rate)
        tft_service_healthy.set(1)

        try:
            import torch
            if torch.cuda.is_available():
                tft_gpu_memory_mb.set(
                    torch.cuda.memory_allocated() / 1024**2
                )
        except Exception:
            pass

        logger.info(
            "✓ TFT model loaded: %s  (sharpe=%.3f, hit=%.3f)",
            live_id, state.val_sharpe, state.val_hit_rate,
        )

    except Exception as exc:
        logger.error("Model load failed: %s", exc)
        tft_model_loaded.set(0)
        tft_service_healthy.set(0)


async def _age_tracker() -> None:
    """Background task: refresh model-age gauge every 60 s."""
    while True:
        if state.trained_at:
            try:
                trained = datetime.fromisoformat(state.trained_at)
                tft_model_age_seconds.set(
                    (datetime.now(timezone.utc) - trained).total_seconds()
                )
            except Exception:
                pass
        try:
            import torch
            if torch.cuda.is_available():
                tft_gpu_memory_mb.set(
                    torch.cuda.memory_allocated() / 1024**2
                )
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup() -> None:
    await load_model_from_registry()
    asyncio.create_task(_age_tracker())


# ── Request / response models ─────────────────────────────────────────────────

class PredictRequest(BaseModel):
    symbol:    str
    features:  dict
    timestamp: Optional[str] = None


class PredictResponse(BaseModel):
    symbol:     str
    direction:  str
    score:      float
    confidence: float
    model_id:   str
    latency_ms: float
    timestamp:  str


# ── Predict ───────────────────────────────────────────────────────────────────

def _features_to_tensor(features: dict):
    """
    Convert a feature dict to a 2-D float tensor [1, N].
    Adapt this to your TFT's actual input schema.
    """
    import numpy as np
    import torch

    values = [float(v) for v in features.values()]
    arr    = np.array(values, dtype=np.float32)
    return torch.tensor(arr).unsqueeze(0)


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not state.is_loaded or state.model is None:
        raise HTTPException(status_code=503, detail="TFT model not loaded yet")

    tft_requests_in_flight.inc()
    start = time.time()

    try:
        import torch

        feature_tensor = _features_to_tensor(req.features)

        with torch.no_grad():
            output = state.model(feature_tensor)

        raw_out = output if not isinstance(output, (list, tuple)) else output[0]

        # raw score from first logit
        score = float(torch.sigmoid(raw_out.flatten()[0]).item())

        # confidence from second logit if present, else same as score
        if raw_out.flatten().shape[0] > 1:
            confidence = float(torch.sigmoid(raw_out.flatten()[1]).item())
        else:
            confidence = score

        direction = "UP" if score > 0.65 else ("DOWN" if score < 0.35 else "HOLD")
        elapsed   = time.time() - start

        # Prometheus
        tft_predictions_total.labels(direction=direction, regime="live").inc()
        tft_prediction_latency.observe(elapsed)
        tft_prediction_score.observe(score)
        tft_last_prediction_timestamp.set(time.time())

        # Persist to Redis for ensemble + dashboard
        try:
            r = _redis()
            r.lpush("apex:tft:predictions", json.dumps({
                "symbol":    req.symbol,
                "score":     round(score, 4),
                "direction": direction,
                "model_id":  state.model_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
            r.ltrim("apex:tft:predictions", 0, 9_999)
            r.set(f"apex:signals:{req.symbol}", json.dumps({
                "symbol":    req.symbol,
                "direction": direction,
                "score":     round(score, 4),
                "model_id":  state.model_id,
                "source":    "tft",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except Exception:
            pass  # non-fatal

        return PredictResponse(
            symbol     = req.symbol,
            direction  = direction,
            score      = round(score, 4),
            confidence = round(confidence, 4),
            model_id   = state.model_id,
            latency_ms = round(elapsed * 1000, 2),
            timestamp  = datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        tft_prediction_errors.labels(error_type=type(exc).__name__).inc()
        tft_service_healthy.set(0)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}")
    finally:
        tft_requests_in_flight.dec()


# ── Model management ──────────────────────────────────────────────────────────

@app.get("/model/info")
async def model_info():
    return {
        "model_id":     state.model_id,
        "version":      state.model_version,
        "val_sharpe":   state.val_sharpe,
        "val_hit_rate": state.val_hit_rate,
        "trained_at":   state.trained_at,
        "loaded_at":    state.loaded_at,
        "is_loaded":    state.is_loaded,
        "device": ("cuda" if _cuda_available() else "cpu"),
    }


@app.post("/model/reload")
async def reload_model(background: BackgroundTasks):
    """Hot-reload — returns immediately, reloads async."""
    background.add_task(load_model_from_registry)
    return {"status": "reload_queued", "message": "Model reload started in background"}


# ── Health / readiness / metrics ──────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":    "healthy" if state.is_loaded else "degraded",
        "service":   "tft_service",
        "model_id":  state.model_id,
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8005)),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
