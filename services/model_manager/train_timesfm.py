#!/usr/bin/env python3
"""
APEX TimesFM Validation & Registration Script
==============================================
TimesFM is a pre-trained foundation model — no gradient training required.
This script:
  1. Connects to the running TimesFM service (http://timesfm-service:8010)
  2. Loads the last 30 days of OHLCV data for all symbols from TimescaleDB
  3. Runs 100 validation predictions against held-out data
  4. Computes sharpe_ratio, hit_rate, avg_confidence, val_loss (MAE)
  5. Registers the model in the Redis model registry
  6. Optionally promotes to 'live' if Sharpe exceeds threshold

Usage:
  python train_timesfm.py
  python train_timesfm.py --promote-if-sharpe-above 1.2
  python train_timesfm.py --timesfm-url http://localhost:8010 --model-id timesfm_v1
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import numpy as np

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("apex.train_timesfm")

# ── Config ────────────────────────────────────────────────────────────────────

TIMESFM_URL = os.getenv("TIMESFM_SERVICE_URL", "http://timesfm-service:8010")
REDIS_HOST  = os.getenv("REDIS_HOST",  "redis")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
DB_HOST     = os.getenv("POSTGRES_HOST",     "timescaledb")
DB_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
DB_USER     = os.getenv("POSTGRES_USER",     "apex_user")
DB_PASS     = os.getenv("POSTGRES_PASSWORD", "apex_pass")
DB_NAME     = os.getenv("POSTGRES_DB",       "apex")

# Validation parameters
N_VAL_PREDICTIONS  = 100   # number of validation samples
LOOKBACK_DAYS      = 30    # days of history for validation
CONTEXT_BARS       = 256   # bars fed to TimesFM per prediction
HTTP_TIMEOUT       = 60.0  # seconds


# ── Database helpers ──────────────────────────────────────────────────────────


def _pg_connect():
    """Return a psycopg2 connection to TimescaleDB."""
    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not installed — please pip install psycopg2-binary")
        sys.exit(1)

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
    )


def _fetch_ohlcv(conn, symbol: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """
    Fetch OHLCV bars for a symbol from the last *lookback_days* days.

    Args:
        conn:          Active psycopg2 connection.
        symbol:        Ticker symbol.
        lookback_days: Number of calendar days to look back.

    Returns:
        List of dicts with keys: time, open, high, low, close, volume.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv_bars
            WHERE symbol = %s AND time >= %s
            ORDER BY time ASC
            """,
            (symbol, cutoff),
        )
        rows = cur.fetchall()

    return [
        {
            "time":   str(r[0]),
            "open":   float(r[1]),
            "high":   float(r[2]),
            "low":    float(r[3]),
            "close":  float(r[4]),
            "volume": float(r[5]) if r[5] is not None else 0.0,
        }
        for r in rows
    ]


def _get_symbols(conn) -> list[str]:
    """Return list of distinct symbols available in ohlcv_bars."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM ohlcv_bars ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


# ── TimesFM service helpers ───────────────────────────────────────────────────


def _wait_for_service(url: str, max_wait_secs: int = 120) -> None:
    """
    Poll GET /ready until the TimesFM service responds 200.

    Args:
        url:           Base URL of the TimesFM service.
        max_wait_secs: Timeout in seconds before raising RuntimeError.

    Raises:
        RuntimeError: If the service does not become ready in time.
    """
    deadline = time.time() + max_wait_secs
    logger.info("Waiting for TimesFM service at %s …", url)
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{url}/ready", timeout=5.0)
            if resp.status_code == 200:
                logger.info("TimesFM service ready")
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(5)
    raise RuntimeError(
        f"TimesFM service at {url} did not become ready within {max_wait_secs}s"
    )


def _predict_one(
    client: httpx.Client,
    url: str,
    symbol: str,
    bars: list[dict],
    model_id: str,
) -> dict:
    """
    Send a single prediction request to the TimesFM service.

    Args:
        client:   Shared httpx client.
        url:      Base URL.
        symbol:   Ticker symbol.
        bars:     OHLCV bars list.
        model_id: Model ID to tag the request with.

    Returns:
        JSON response dict from /predict.

    Raises:
        httpx.HTTPStatusError: On non-2xx response.
        httpx.TimeoutException:  On request timeout.
    """
    payload = {
        "symbol":   symbol,
        "horizon":  "next_1h",
        "bars":     bars,
        "model_id": model_id,
    }
    resp = client.post(
        f"{url}/predict",
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Metrics computation ───────────────────────────────────────────────────────


def _annualised_sharpe(returns: np.ndarray, periods_per_year: int = 252 * 390) -> float:
    """
    Compute annualised Sharpe ratio.

    Args:
        returns:           1-D array of per-period returns (not in %).
        periods_per_year:  Periods in a trading year. Default = 252 days × 390 min.

    Returns:
        Annualised Sharpe ratio, or 0.0 if std is near zero.
    """
    if len(returns) < 2:
        return 0.0
    mu  = returns.mean()
    std = returns.std(ddof=1)
    if std < 1e-9:
        return 0.0
    return float((mu / std) * math.sqrt(periods_per_year))


def _compute_metrics(
    predictions: list[float],
    actuals: list[float],
    prev_closes: list[float],
    confidences: list[float],
) -> dict:
    """
    Compute validation metrics from prediction vs actual arrays.

    Args:
        predictions:  Predicted close prices.
        actuals:      Actual realised close prices.
        prev_closes:  Previous close (context last bar) for direction check.
        confidences:  Confidence scores from the service.

    Returns:
        Dict with keys: sharpe, hit_rate, avg_confidence, val_loss (MAE).
    """
    preds  = np.array(predictions, dtype=np.float64)
    acts   = np.array(actuals,     dtype=np.float64)
    prevs  = np.array(prev_closes, dtype=np.float64)

    # MAE
    mae = float(np.mean(np.abs(preds - acts)))

    # Direction accuracy (hit rate)
    pred_dir   = np.sign(preds  - prevs)
    actual_dir = np.sign(acts   - prevs)
    hit_rate   = float(np.mean(pred_dir == actual_dir))

    # Sharpe on prediction-driven returns:
    #   trade return = actual_ret * sign(predicted_direction)
    actual_rets  = (acts - prevs) / (np.abs(prevs) + 1e-9)
    strat_rets   = actual_rets * pred_dir
    sharpe       = _annualised_sharpe(strat_rets)

    avg_conf = float(np.mean(confidences)) if confidences else 0.5

    return {
        "sharpe":         round(sharpe,   4),
        "hit_rate":       round(hit_rate, 4),
        "avg_confidence": round(avg_conf, 4),
        "val_loss":       round(mae,      6),
    }


# ── Redis registration ────────────────────────────────────────────────────────


def _register_in_redis(
    model_id: str,
    metrics:  dict,
    status:   str = "staging",
) -> None:
    """
    Upsert model metadata in the Redis model registry.

    Args:
        model_id: e.g. 'timesfm_v1'
        metrics:  Dict from _compute_metrics().
        status:   'staging' or 'live'.
    """
    try:
        import redis as redis_lib
    except ImportError:
        logger.error("redis package not installed")
        sys.exit(1)

    r = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_timeout=5,
        decode_responses=True,
    )

    version_str = "".join(filter(str.isdigit, model_id)) or "1"

    record = {
        "model_id":       model_id,
        "model_type":     "timesfm",
        "status":         status,
        "version":        int(version_str),
        "val_sharpe":     metrics["sharpe"],
        "val_hit_rate":   metrics["hit_rate"],
        "avg_confidence": metrics["avg_confidence"],
        "val_loss":       metrics["val_loss"],
        "created_at":     datetime.now(timezone.utc).isoformat(),
        "trained_by":     "train_timesfm",
        "component":      "timesfm",
        "huggingface_repo": "google/timesfm-1.0-200m-pytorch",
    }

    r.set(f"apex:models:{model_id}", json.dumps(record))
    r.sadd("apex:models:all", model_id)

    logger.info(
        "Registered %s in Redis | status=%s sharpe=%.4f hit=%.4f",
        model_id, status, metrics["sharpe"], metrics["hit_rate"],
    )


# ── Summary table ─────────────────────────────────────────────────────────────


def _print_summary(model_id: str, metrics: dict, n_samples: int, status: str) -> None:
    """Print a formatted validation summary table to stdout."""
    line = "=" * 56
    print(f"\n{line}")
    print(f"  TimesFM Validation Summary")
    print(line)
    print(f"  Model ID:         {model_id}")
    print(f"  Status:           {status}")
    print(f"  Validation set:   {n_samples} predictions")
    print(f"  Hit Rate:         {metrics['hit_rate']:.2%}")
    print(f"  Annualised Sharpe:{metrics['sharpe']:.4f}")
    print(f"  Avg Confidence:   {metrics['avg_confidence']:.4f}")
    print(f"  Val Loss (MAE):   {metrics['val_loss']:.6f}")
    print(line + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────


def validate_and_register(
    model_id:              str   = "timesfm_v1",
    timesfm_url:           str   = TIMESFM_URL,
    promote_if_sharpe_above: Optional[float] = None,
    n_predictions:         int   = N_VAL_PREDICTIONS,
    lookback_days:         int   = LOOKBACK_DAYS,
) -> dict:
    """
    Full validation-and-registration pipeline.

    Args:
        model_id:                Redis model ID, e.g. 'timesfm_v1'.
        timesfm_url:             Base URL of the TimesFM microservice.
        promote_if_sharpe_above: If set and Sharpe exceeds this value,
                                 register with status='live'.
        n_predictions:           Number of held-out predictions to make.
        lookback_days:           History window to pull from TimescaleDB.

    Returns:
        Metrics dict: sharpe, hit_rate, avg_confidence, val_loss.
    """
    # ── 1. Wait for service ─────────────────────────────────────────────────
    _wait_for_service(timesfm_url)

    # ── 2. Connect to DB and load held-out data ─────────────────────────────
    logger.info("Connecting to TimescaleDB for held-out validation data …")
    conn   = _pg_connect()
    symbols = _get_symbols(conn)

    if not symbols:
        conn.close()
        raise RuntimeError("No symbols found in ohlcv_bars — cannot validate")

    logger.info("Found %d symbols — loading last %d days of bars", len(symbols), lookback_days)

    # Build a big pool of (context_bars, next_close) pairs
    validation_pool: list[tuple[str, list[dict], float]] = []

    for sym in symbols:
        bars = _fetch_ohlcv(conn, sym, lookback_days)
        if len(bars) < CONTEXT_BARS + 1:
            continue  # not enough data for this symbol — skip

        # Slide window: context=[0:CONTEXT_BARS], target=bar[CONTEXT_BARS]
        max_start = len(bars) - CONTEXT_BARS - 1
        # Sample up to 5 windows per symbol to stay within budget
        step = max(1, max_start // 5)
        for start in range(0, max_start, step):
            context  = bars[start: start + CONTEXT_BARS]
            target   = bars[start + CONTEXT_BARS]
            validation_pool.append((sym, context, target["close"]))

    conn.close()

    if not validation_pool:
        raise RuntimeError(
            "Insufficient historical data — need at least "
            f"{CONTEXT_BARS + 1} bars per symbol"
        )

    # Randomly sample n_predictions from the pool
    rng = np.random.default_rng(seed=42)
    sample_idx = rng.choice(
        len(validation_pool),
        size=min(n_predictions, len(validation_pool)),
        replace=False,
    )
    samples = [validation_pool[i] for i in sample_idx]

    logger.info(
        "Running %d validation predictions against TimesFM service …",
        len(samples),
    )

    # ── 3. Run batch validation predictions ─────────────────────────────────
    predictions:   list[float] = []
    actuals:       list[float] = []
    prev_closes:   list[float] = []
    confidences:   list[float] = []
    errors:        int         = 0

    with httpx.Client() as client:
        for i, (sym, context_bars, actual_close) in enumerate(samples):
            try:
                resp = _predict_one(client, timesfm_url, sym, context_bars, model_id)
                predictions.append(resp["predicted_value"])
                actuals.append(actual_close)
                prev_closes.append(context_bars[-1]["close"])
                confidences.append(resp.get("confidence", 0.5))

                if (i + 1) % 20 == 0:
                    logger.info("Completed %d / %d predictions", i + 1, len(samples))

            except (httpx.HTTPStatusError, httpx.TimeoutException, KeyError) as exc:
                errors += 1
                logger.warning("Prediction %d failed: %s", i, exc)
                if errors > len(samples) * 0.2:
                    raise RuntimeError(
                        f"Too many prediction failures ({errors}) — aborting validation"
                    )

    if not predictions:
        raise RuntimeError("No successful predictions — validation failed")

    logger.info(
        "Validation complete: %d successful, %d errors",
        len(predictions), errors,
    )

    # ── 4. Compute metrics ──────────────────────────────────────────────────
    metrics = _compute_metrics(predictions, actuals, prev_closes, confidences)
    logger.info("Metrics: %s", metrics)

    # ── 5. Decide status ────────────────────────────────────────────────────
    status = "staging"
    if promote_if_sharpe_above is not None and metrics["sharpe"] >= promote_if_sharpe_above:
        status = "live"
        logger.info(
            "Sharpe %.4f >= threshold %.4f — promoting to live",
            metrics["sharpe"], promote_if_sharpe_above,
        )

    # ── 6. Register in Redis ────────────────────────────────────────────────
    _register_in_redis(model_id, metrics, status)

    # ── 7. Print summary ────────────────────────────────────────────────────
    _print_summary(model_id, metrics, len(predictions), status)

    return metrics


# ── CLI entry point ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate TimesFM against held-out data and register in Redis"
    )
    parser.add_argument(
        "--model-id",
        default="timesfm_v1",
        help="Redis model ID (default: timesfm_v1)",
    )
    parser.add_argument(
        "--timesfm-url",
        default=TIMESFM_URL,
        help=f"TimesFM service base URL (default: {TIMESFM_URL})",
    )
    parser.add_argument(
        "--promote-if-sharpe-above",
        type=float,
        default=None,
        metavar="SHARPE",
        help="Auto-promote to live if annualised Sharpe exceeds this value",
    )
    parser.add_argument(
        "--n-predictions",
        type=int,
        default=N_VAL_PREDICTIONS,
        help=f"Number of validation predictions (default: {N_VAL_PREDICTIONS})",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=LOOKBACK_DAYS,
        help=f"Days of history to load from TimescaleDB (default: {LOOKBACK_DAYS})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args   = _parse_args()
    result = validate_and_register(
        model_id               = args.model_id,
        timesfm_url            = args.timesfm_url,
        promote_if_sharpe_above= args.promote_if_sharpe_above,
        n_predictions          = args.n_predictions,
        lookback_days          = args.lookback_days,
    )
    sys.exit(0)
