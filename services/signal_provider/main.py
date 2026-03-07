"""
APEX Signal Provider Microservice  v2.1.0
==========================================
Crash-proof design: Redis connection is attempted in a
background thread — the service starts healthy even when
Redis / the ensemble engine are not yet up.

Endpoints:
  GET  /health          — liveness check (always 200)
  GET  /ready           — readiness (200 once Redis is alive)
  GET  /metrics         — Prometheus scrape endpoint
  POST /signal          — generate / retrieve latest signal
  GET  /signals/latest  — last N signals from Redis list
  GET  /status          — pipeline status summary
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

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
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(title="APEX Signal Provider", version="2.1.0")

# ── Module-level state ────────────────────────────────────────────────────────
_last_signal_at: float = 0.0
_redis_client          = None          # set by background thread; None until connected
_redis_lock            = threading.Lock()

# ── Prometheus metrics ────────────────────────────────────────────────────────

signals_generated = Counter(
    "apex_signals_generated_total",
    "Total signals generated",
    ["symbol", "direction"],
)
signal_score_histogram = Histogram(
    "apex_signal_score",
    "Signal confidence scores",
    buckets=[0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)
signal_latency = Histogram(
    "apex_signal_latency_seconds",
    "Time to generate a signal",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
active_symbols = Gauge(
    "apex_signal_active_symbols",
    "Number of symbols being monitored",
)
last_signal_age_gauge = Gauge(
    "apex_signal_last_age_seconds",
    "Seconds since last signal was generated",
)
provider_healthy = Gauge(
    "apex_signal_provider_healthy",
    "1 if healthy, 0 if not",
)
last_signal_timestamp = Gauge(
    "apex_last_signal_timestamp",
    "Unix timestamp of last signal generated",
)
orders_total = Counter(
    "apex_orders_total",
    "Total orders submitted",
    ["symbol", "side"],
)

# ── Redis helpers ─────────────────────────────────────────────────────────────

def _get_redis():
    """Return the cached Redis client, or None if not yet connected."""
    return _redis_client


def _make_redis():
    import redis as _r
    client = _r.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )
    client.ping()
    return client


# ── Background: Redis connector (retries every 5 s, never crashes startup) ────

def _redis_connect_loop() -> None:
    global _redis_client
    while True:
        try:
            r = _make_redis()
            with _redis_lock:
                _redis_client = r
            provider_healthy.set(1)
            logger.info("Redis connected ✓")
            # Stay connected; detect drops via periodic ping
            while True:
                time.sleep(30)
                try:
                    r.ping()
                except Exception:
                    logger.warning("Redis ping failed — reconnecting...")
                    with _redis_lock:
                        _redis_client = None
                    provider_healthy.set(0)
                    break
        except Exception as exc:
            logger.warning("Redis unavailable: %s — retry in 5 s", exc)
            provider_healthy.set(0)
            time.sleep(5)


# ── Background: age-gauge updater ────────────────────────────────────────────

def _run_age_gauge() -> None:
    """
    Daemon thread — updates last_signal_age_gauge every 5 s so Grafana always
    has a fresh value between Prometheus scrapes.  Falls back to Redis key if
    no local signal has been generated since startup.
    """
    global _last_signal_at
    while True:
        try:
            ref_ts = _last_signal_at
            if ref_ts == 0.0:
                r = _get_redis()
                if r:
                    raw = r.get("apex:signal_engine:last_signal_ts")
                    if raw:
                        ref_ts = float(raw)
                        _last_signal_at = ref_ts
            if ref_ts > 0:
                last_signal_age_gauge.set(time.time() - ref_ts)
        except Exception:
            pass
        time.sleep(5)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    threading.Thread(target=_redis_connect_loop, daemon=True, name="redis-connector").start()
    threading.Thread(target=_run_age_gauge,       daemon=True, name="age-gauge").start()
    logger.info("Signal Provider v2.1.0 started ✓  (Redis connecting in background)")


# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness probe — always 200 while the process is alive."""
    return {
        "status":    "healthy",
        "service":   "signal_provider",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version":   "2.1.0",
        "redis":     "ok" if _get_redis() else "connecting",
    }


@app.get("/ready")
async def ready():
    """Readiness probe — 200 once the cached Redis client is alive."""
    if _get_redis() is not None:
        return {"ready": True, "redis": "ok"}
    raise HTTPException(status_code=503, detail="Redis not yet connected")


# ── Metrics endpoint ──────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint — never returns 5xx."""
    try:
        r = _get_redis()
        if r:
            raw = r.get("apex:signal_engine:last_signal_ts")
            if raw:
                last_ts = float(raw)
                last_signal_age_gauge.set(time.time() - last_ts)
                last_signal_timestamp.set(last_ts)
            symbols_raw = r.smembers("apex:signal_engine:symbols")
            if symbols_raw:
                active_symbols.set(len(symbols_raw))
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Status endpoint ───────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    """Pipeline status summary — used by dashboard."""
    r = _get_redis()
    if not r:
        return {
            "pipeline_active":   False,
            "kill_switch":       False,
            "active_model":      "unknown",
            "active_symbols":    [],
            "last_signal_age_s": None,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "redis":             "connecting",
        }
    try:
        raw   = r.get("apex:signal_engine:last_signal_ts")
        syms  = r.smembers("apex:signal_engine:symbols") or set()
        model = r.get("apex:signal_engine:active_model") or "unknown"
        ks    = r.get("apex:kill_switch") or "0"

        last_ts = float(raw) if raw else 0.0
        age_s   = round(time.time() - last_ts, 1) if last_ts else None

        return {
            "pipeline_active":   ks != "1",
            "kill_switch":       ks == "1",
            "active_model":      model,
            "active_symbols":    list(syms),
            "last_signal_age_s": age_s,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Latest signals (list endpoint) ───────────────────────────────────────────

@app.get("/signals/latest")
async def signals_latest(limit: int = 10):
    """Return last N signals from Redis list."""
    r = _get_redis()
    if r:
        try:
            raw = r.lrange("apex:signals:latest", 0, min(limit, 50) - 1)
            return {"signals": [json.loads(s) for s in raw], "source": "redis"}
        except Exception:
            pass
    return {"signals": [], "source": "mock"}


# ── Signal request/response models ───────────────────────────────────────────

class SignalRequest(BaseModel):
    symbol: str
    overrides: Optional[dict] = None


class SignalResponse(BaseModel):
    symbol:    str
    direction: str
    score:     float
    model_id:  str
    source:    str
    timestamp: str


@app.post("/signal", response_model=SignalResponse)
async def generate_signal(req: SignalRequest):
    """
    Return the latest cached signal for a symbol.
    The signal_engine publishes to Redis; this endpoint exposes it over HTTP.
    Falls back to HOLD if Redis is unavailable or has no entry for the symbol.
    """
    start = time.time()
    data: dict = {
        "symbol":    req.symbol,
        "direction": "HOLD",
        "score":     0.0,
        "model_id":  "none",
        "source":    "no_redis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    r = _get_redis()
    if r:
        try:
            raw = r.get(f"apex:signals:{req.symbol}")
            if raw:
                data = json.loads(raw)
                data["source"] = "cache"
            else:
                data["source"] = "cache_miss"
        except Exception:
            data["source"] = "redis_error"

    # Record metrics
    signal_latency.observe(time.time() - start)
    signals_generated.labels(
        symbol=req.symbol,
        direction=data.get("direction", "HOLD"),
    ).inc()
    score = float(data.get("score", 0.0))
    if score > 0:
        signal_score_histogram.observe(score)

    # Update last-signal timestamp (only for real cached signals)
    if data.get("source") == "cache" and r:
        try:
            global _last_signal_at
            ts = time.time()
            _last_signal_at = ts
            r.set("apex:signal_engine:last_signal_ts", ts)
            last_signal_timestamp.set(ts)
            last_signal_age_gauge.set(0)
        except Exception:
            pass

    return SignalResponse(**data)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8002)),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
