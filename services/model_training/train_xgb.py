"""
APEX XGBoost/LightGBM Walk-Forward Trainer
services/model_training/train_xgb.py

Usage:
  python -m services.model_training.train_xgb [--fold N] [--model-id ID]
  python services/model_training/train_xgb.py

Connects to TimescaleDB, loads feature data, trains XGBoost with walk-forward
cross-validation, logs to MLflow, and registers the model in Redis.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import redis
from sklearn.metrics import accuracy_score
import xgboost as xgb

# ── optional MLflow ──────────────────────────────────────────────────────────
try:
    import mlflow
    import mlflow.xgboost
    _MLFLOW = True
except ImportError:
    _MLFLOW = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("apex.train_xgb")

# ── config from env ──────────────────────────────────────────────────────────
DB_HOST      = os.getenv("POSTGRES_HOST",        "localhost")
DB_PORT      = int(os.getenv("POSTGRES_PORT",    "15432"))
DB_USER      = os.getenv("POSTGRES_USER",        "apex_user")
DB_PASS      = os.getenv("POSTGRES_PASSWORD",    "apex_pass")
DB_NAME      = os.getenv("POSTGRES_DB",          "apex")
REDIS_HOST   = os.getenv("REDIS_HOST",           "localhost")
REDIS_PORT   = int(os.getenv("REDIS_PORT",       "16379"))
MLFLOW_URI   = os.getenv("MLFLOW_TRACKING_URI",  "http://localhost:5001")
MLFLOW_EXP   = os.getenv("MLFLOW_EXPERIMENT_NAME", "apex_xgb")
ARTIFACTS    = Path(os.getenv("MODEL_ARTIFACT_DIR", "/tmp/apex_models"))

FEATURE_COLS = [
    "returns_1", "returns_5", "returns_15", "returns_60",
    "rsi_14", "rsi_28", "ema_20", "ema_50", "ema_200",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_pct",
    "atr_14", "stoch_k", "stoch_d",
    "volume_ratio", "vwap_dev", "adx_14",
]

# ── data ─────────────────────────────────────────────────────────────────────

def load_features() -> pd.DataFrame:
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, dbname=DB_NAME,
    )
    query = """
        SELECT f.time, f.symbol,
               f.returns_1, f.returns_5, f.returns_15, f.returns_60,
               f.rsi_14, f.rsi_28, f.ema_20, f.ema_50, f.ema_200,
               f.macd, f.macd_signal, f.macd_hist,
               f.bb_upper, f.bb_lower, f.bb_pct,
               f.atr_14, f.stoch_k, f.stoch_d,
               f.volume_ratio, f.vwap_dev, f.adx_14
        FROM features f
        ORDER BY f.symbol, f.time ASC
    """
    df = pd.read_sql(query, conn, parse_dates=["time"])
    conn.close()
    logger.info("Loaded %d feature rows for %d symbols", len(df), df["symbol"].nunique())
    return df


def build_dataset(df: pd.DataFrame, horizon: int = 1) -> tuple[pd.DataFrame, pd.Series]:
    """Build feature matrix X and binary target y (1 = positive next return)."""
    frames = []
    for sym, grp in df.groupby("symbol", sort=False):
        g = grp.sort_values("time").copy()
        # Target: sign of next-bar return → 1=up, 0=down
        g["target"] = (g["returns_1"].shift(-horizon) > 0).astype(float)
        g = g.dropna(subset=FEATURE_COLS + ["target"])
        frames.append(g)

    combined = pd.concat(frames, ignore_index=True).sort_values("time")

    # Add symbol as label-encoded feature
    combined["sym_enc"] = combined["symbol"].astype("category").cat.codes

    feat_cols = FEATURE_COLS + ["sym_enc"]
    X = combined[feat_cols].astype(float)
    y = combined["target"]
    return X, y


# ── walk-forward ─────────────────────────────────────────────────────────────

def walk_forward_xgb(
    X: pd.DataFrame,
    y: pd.Series,
    n_folds: int = 5,
    test_frac: float = 0.20,
) -> tuple[xgb.XGBClassifier, list[dict]]:
    """5-fold walk-forward: train on past, test on future."""
    n      = len(X)
    fold_sz = int(n * test_frac)

    params = dict(
        n_estimators     = 400,
        max_depth        = 5,
        learning_rate    = 0.05,
        subsample        = 0.8,
        colsample_bytree = 0.8,
        use_label_encoder = False,
        eval_metric      = "logloss",
        random_state     = 42,
        tree_method      = "hist",
        n_jobs           = -1,
        verbosity        = 0,
    )

    results: list[dict] = []
    best_model: Optional[xgb.XGBClassifier] = None

    for fold in range(n_folds):
        oos_end   = n - fold * fold_sz
        oos_start = oos_end - fold_sz
        if oos_start <= fold_sz:
            logger.warning("Fold %d: not enough IS data, skipping", fold)
            continue

        X_is  = X.iloc[:oos_start]
        y_is  = y.iloc[:oos_start]
        X_oos = X.iloc[oos_start:oos_end]
        y_oos = y.iloc[oos_start:oos_end]

        model = xgb.XGBClassifier(**params)
        model.fit(
            X_is, y_is,
            eval_set=[(X_oos, y_oos)],
            verbose=False,
        )

        preds = model.predict(X_oos)
        acc   = accuracy_score(y_oos, preds)
        hit   = float(acc)

        # Pseudo-Sharpe from binary predictions
        pred_returns = np.where(preds == 1, 1.0, -1.0)
        sharpe = float(pred_returns.mean() / (pred_returns.std() + 1e-9) * np.sqrt(252 * 26))

        results.append({
            "fold":       fold,
            "oos_acc":    round(hit, 4),
            "oos_sharpe": round(sharpe, 4),
            "is_bars":    len(X_is),
            "oos_bars":   len(X_oos),
        })
        logger.info("Fold %d  acc=%.3f  sharpe=%.3f  [IS=%d OOS=%d]",
                    fold, hit, sharpe, len(X_is), len(X_oos))

        # CF-1: use last fold (most recent regime)
        if fold == 0:
            best_model = model

    if best_model is None:
        raise RuntimeError("No folds completed — dataset too small?")

    return best_model, results


# ── MLflow + Redis ────────────────────────────────────────────────────────────

def log_to_mlflow(model: xgb.XGBClassifier, folds: list[dict], model_id: str) -> str:
    if not _MLFLOW:
        return ""
    try:
        mlflow.set_tracking_uri(MLFLOW_URI)
        mlflow.set_experiment(MLFLOW_EXP)
        with mlflow.start_run(run_name=model_id) as run:
            avg_sharpe  = np.mean([f["oos_sharpe"] for f in folds])
            avg_acc     = np.mean([f["oos_acc"]    for f in folds])
            mlflow.log_params({
                "n_estimators": model.n_estimators,
                "max_depth":    model.max_depth,
                "n_folds":      len(folds),
            })
            mlflow.log_metrics({
                "val_sharpe":   round(avg_sharpe, 4),
                "val_hit_rate": round(avg_acc * 100, 2),
            })
            # Log each fold metric individually (avoids artifact store path issues)
            for f in folds:
                mlflow.log_metrics({
                    f"fold{f['fold']}_sharpe": f["oos_sharpe"],
                    f"fold{f['fold']}_acc":    f["oos_acc"],
                }, step=f["fold"])
            mlflow.set_tag("production", "true")
            mlflow.set_tag("model_id", model_id)
            return run.info.run_id
    except Exception as exc:
        logger.warning("MLflow logging failed: %s", exc)
        return ""


def register_in_redis(model_id: str, run_id: str, folds: list[dict]) -> None:
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        avg_sharpe   = np.mean([f["oos_sharpe"] for f in folds])
        avg_hit      = np.mean([f["oos_acc"]    for f in folds]) * 100
        meta = {
            "model_id":    model_id,
            "model_type":  "xgb",
            "version":     model_id.split("_v")[-1] if "_v" in model_id else "1",
            "status":      "staging",
            "val_sharpe":  round(float(avg_sharpe), 4),
            "val_hit_rate": round(float(avg_hit), 2),
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "trained_by":  "train_xgb.py",
            "mlflow_run_id": run_id,
        }
        r.set(f"apex:models:{model_id}", json.dumps(meta))
        r.sadd("apex:models:all", model_id)
        r.lpush("apex:agent_log", json.dumps({
            "id":        f"train_{model_id}",
            "timestamp": meta["created_at"],
            "type":      "TRAINING_COMPLETE",
            "details":   f"XGB {model_id} trained — sharpe={meta['val_sharpe']:.3f} hit={meta['val_hit_rate']:.1f}%",
            "source":    "train_xgb",
        }))
        logger.info("Registered %s in Redis (sharpe=%.3f hit=%.1f%%)",
                    model_id, avg_sharpe, avg_hit)
    except Exception as exc:
        logger.warning("Redis registration failed: %s", exc)


def save_artifact(model: xgb.XGBClassifier, model_id: str) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"{model_id}.pkl"
    with open(path, "wb") as f:
        pickle.dump(model, f)
    logger.info("Model saved to %s", path)
    return path


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fold",  type=int, default=int(time.time()) % 10000,
                    help="Version number for model_id")
    ap.add_argument("--model-id", type=str, default=None)
    ap.add_argument("--mlflow-experiment", type=str, default=None)
    ap.add_argument("--n-folds", type=int, default=5)
    args = ap.parse_args()

    model_id  = args.model_id or f"XGB_v{args.fold}"
    if args.mlflow_experiment:
        global MLFLOW_EXP
        MLFLOW_EXP = args.mlflow_experiment

    logger.info("=== APEX XGB Training: %s ===", model_id)

    # 1. Load data
    df = load_features()
    if df.empty:
        logger.error("No feature data found — run feature engineering first.")
        sys.exit(1)

    # 2. Build dataset
    X, y = build_dataset(df)
    logger.info("Dataset: %d rows, %d features, %.1f%% positive",
                len(X), len(X.columns), y.mean() * 100)

    # 3. Walk-forward training
    t0    = time.time()
    model, folds = walk_forward_xgb(X, y, n_folds=args.n_folds)
    elapsed = time.time() - t0
    logger.info("Training complete in %.1f seconds", elapsed)

    # 4. Save artifact
    save_artifact(model, model_id)

    # 5. Log to MLflow
    run_id = log_to_mlflow(model, folds, model_id)

    # 6. Register in Redis
    register_in_redis(model_id, run_id, folds)

    # 7. Print summary
    avg_sharpe = np.mean([f["oos_sharpe"] for f in folds])
    avg_hit    = np.mean([f["oos_acc"]    for f in folds]) * 100
    print(f"\n{'='*50}")
    print(f"Model:        {model_id}")
    print(f"Avg Sharpe:   {avg_sharpe:.4f}")
    print(f"Avg Hit Rate: {avg_hit:.2f}%")
    print(f"MLflow Run:   {run_id or 'not logged'}")
    print(f"Duration:     {elapsed:.1f}s")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
