"""
APEX Dynamic Model Loader
Hot-reloads the active model whenever Redis key `apex:signal_engine:active_model` changes.
Supports A/B test traffic splitting configured in `apex:signal_engine:ab_test`.

Usage:
    loader = DynamicModelLoader(redis_client)
    await loader.start()
    ...
    model = await loader.get_model()   # returns current live model
    result = await loader.predict(features)
"""

import asyncio
import importlib
import json
import logging
import random
from pathlib import Path
from typing import Any

import redis.asyncio as aredis

logger = logging.getLogger(__name__)

# Poll interval for active model key changes (seconds)
POLL_INTERVAL = 5


class DynamicModelLoader:
    """
    Subscribes to Redis key changes and hot-reloads the active model.
    Thread-safe prediction routing with A/B test support.

    Supported model types: tft, xgb, lstm, ensemble
    Model artifacts are loaded via MLflow or local pickle/PyTorch.
    """

    REDIS_ACTIVE  = "apex:signal_engine:active_model"
    REDIS_AB_TEST = "apex:signal_engine:ab_test"
    REDIS_MODELS  = "apex:models:{model_id}"

    def __init__(self, redis_client: aredis.Redis | None = None):
        self._redis         = redis_client
        self._current_id:   str | None  = None
        self._current_model: Any        = None
        self._ab_config:    dict | None = None
        self._ab_model:     Any         = None
        self._ab_model_id:  str | None  = None
        self._lock          = asyncio.Lock()
        self._running       = False

    async def _get_redis(self) -> aredis.Redis:
        if self._redis is None:
            import os
            host = os.environ.get("REDIS_HOST", "redis")
            self._redis = aredis.Redis(host=host, port=6379, decode_responses=True)
        return self._redis

    # ─────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background polling loop."""
        self._running = True
        asyncio.create_task(self._poll_loop())
        logger.info("DynamicModelLoader started — polling Redis every %ds", POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._refresh()
            except Exception as e:
                logger.warning("ModelLoader poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    # ─────────────────────────────────────────────
    # Core refresh
    # ─────────────────────────────────────────────

    async def _refresh(self) -> None:
        r   = await self._get_redis()
        new_id = await r.get(self.REDIS_ACTIVE)

        async with self._lock:
            # Reload primary model if changed
            if new_id and new_id != self._current_id:
                model = await self._load_model(new_id)
                if model is not None:
                    self._current_id    = new_id
                    self._current_model = model
                    logger.info("Hot-reloaded active model: %s", new_id)

            # Load A/B test config
            ab_raw = await r.get(self.REDIS_AB_TEST)
            if ab_raw:
                try:
                    self._ab_config = json.loads(ab_raw)
                    ab_id = self._ab_config.get("model_b_id")
                    if ab_id and ab_id != self._ab_model_id:
                        ab_model = await self._load_model(ab_id)
                        if ab_model is not None:
                            self._ab_model    = ab_model
                            self._ab_model_id = ab_id
                            logger.info("Hot-reloaded A/B challenger: %s", ab_id)
                except Exception as e:
                    logger.warning("Failed to parse A/B config: %s", e)
            else:
                self._ab_config  = None
                self._ab_model   = None
                self._ab_model_id = None

    # ─────────────────────────────────────────────
    # Model loading
    # ─────────────────────────────────────────────

    async def _load_model(self, model_id: str) -> Any | None:
        """
        Load model artifact from MLflow or local path.
        Falls back to a stub if artifact not found.
        """
        r        = await self._get_redis()
        meta_raw = await r.get(self.REDIS_MODELS.format(model_id=model_id))
        if not meta_raw:
            logger.warning("No metadata for model %s — skipping load", model_id)
            return None

        try:
            meta      = json.loads(meta_raw)
            model_type = meta.get("model_type", "ensemble")
            run_id     = meta.get("mlflow_run_id")
            artifact   = meta.get("artifact_path")
        except Exception as e:
            logger.error("Failed to parse metadata for %s: %s", model_id, e)
            return None

        # Try MLflow first
        if run_id:
            try:
                import mlflow
                mlflow_uri = "http://mlflow:5000"
                mlflow.set_tracking_uri(mlflow_uri)

                if model_type == "xgb":
                    model = mlflow.xgboost.load_model(f"runs:/{run_id}/model")
                elif model_type in ("lstm", "tft"):
                    model = mlflow.pytorch.load_model(f"runs:/{run_id}/model")
                else:
                    model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")

                logger.info("Loaded %s from MLflow run %s", model_id, run_id)
                return model
            except Exception as e:
                logger.warning("MLflow load failed for %s: %s — falling back to stub", model_id, e)

        # Try local artifact path
        if artifact and Path(artifact).exists():
            try:
                import pickle
                with open(artifact, "rb") as f:
                    model = pickle.load(f)
                logger.info("Loaded %s from local artifact %s", model_id, artifact)
                return model
            except Exception as e:
                logger.warning("Local load failed for %s: %s", model_id, e)

        # Stub
        logger.warning("Using stub model for %s", model_id)
        return _StubModel(model_id)

    # ─────────────────────────────────────────────
    # Prediction
    # ─────────────────────────────────────────────

    async def get_model(self) -> Any | None:
        """Returns the current active model (thread-safe)."""
        async with self._lock:
            return self._current_model

    async def predict(self, features: Any) -> dict:
        """
        Route prediction with A/B test support.
        Returns: {signal, model_id, ab_test_variant, ...}
        """
        async with self._lock:
            # A/B routing
            if self._ab_config and self._ab_model and self._current_model:
                weight_b = self._ab_config.get("weight_b", 0.2)
                if random.random() < weight_b:
                    model    = self._ab_model
                    model_id = self._ab_model_id
                    variant  = "B"
                else:
                    model    = self._current_model
                    model_id = self._current_id
                    variant  = "A"
            else:
                model    = self._current_model
                model_id = self._current_id
                variant  = None

        if model is None:
            return {"signal": 0.0, "model_id": None, "error": "no_model_loaded"}

        try:
            if hasattr(model, "predict"):
                raw = model.predict(features)
            elif hasattr(model, "forward"):
                import torch
                with torch.no_grad():
                    raw = model(features)
            else:
                raw = model(features)

            if hasattr(raw, "__len__"):
                signal = float(raw[0])
            else:
                signal = float(raw)

            return {
                "signal":           signal,
                "model_id":         model_id,
                "ab_test_variant":  variant,
                "error":            None,
            }
        except Exception as e:
            logger.error("Prediction failed for model %s: %s", model_id, e)
            return {"signal": 0.0, "model_id": model_id, "error": str(e)}


# ─── Stub ─────────────────────────────────────────────────────────────────────

class _StubModel:
    """Returns zero signal — used when artifact loading fails."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def predict(self, _features: Any) -> list:
        logger.debug("Stub model %s returning zero signal", self.model_id)
        return [0.0]
