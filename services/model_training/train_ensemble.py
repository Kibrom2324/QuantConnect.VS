#!/usr/bin/env python3
"""
APEX Ensemble Model Trainer
============================
Combines XGB, LSTM, and TFT predictions using a meta-learner (logistic regression).
Registers ENS_v1 in Redis as the primary signal model.

Usage:
  python -m services.model_training.train_ensemble \\
      --model-id ENS_v1 \\
      --xgb-id XGB_v2 \\
      --lstm-id LSTM_v4 \\
      --tft-id TFT_v3 \\
      --n-folds 4

Environment:
  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  REDIS_HOST, REDIS_PORT
  MLFLOW_TRACKING_URI
  MODEL_DIR   (where model artifacts are saved)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
import types
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("apex.train_ensemble")

# ── config ────────────────────────────────────────────────────────────────────
DB_HOST    = os.getenv("POSTGRES_HOST",       "timescaledb")
DB_PORT    = int(os.getenv("POSTGRES_PORT",   "5432"))
DB_USER    = os.getenv("POSTGRES_USER",       "apex_user")
DB_PASS    = os.getenv("POSTGRES_PASSWORD",   "apex_pass")
DB_NAME    = os.getenv("POSTGRES_DB",         "apex")
REDIS_HOST = os.getenv("REDIS_HOST",          "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT",      "6379"))
MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXP = os.getenv("MLFLOW_EXPERIMENT_NAME", "apex_ensemble")
MODEL_DIR  = Path(os.getenv("MODEL_DIR",      "/app/models"))

FEATURE_COLS = [
    "returns_1", "returns_5", "returns_15", "returns_60",
    "rsi_14", "rsi_28", "ema_20", "ema_50", "ema_200",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_pct",
    "atr_14", "stoch_k", "stoch_d",
    "volume_ratio", "vwap_dev", "adx_14",
]
XGB_FEATURE_COLS = FEATURE_COLS  # same columns for XGB

# ── optional dependencies check ───────────────────────────────────────────────
try:
    import psycopg2
    import pandas as pd
    _HAS_PG = True
except ImportError:
    _HAS_PG = False

try:
    import xgboost as xgb
    import joblib
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

try:
    import mlflow
    _MLFLOW = True
except ImportError:
    _MLFLOW = False

try:
    import redis as _redis_lib
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


def _redis():
    return _redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# ── model definitions (must match train scripts) ──────────────────────────────

SEQ_LEN_LSTM = 32
SEQ_LEN_TFT  = 48
N_FEATURES   = len(FEATURE_COLS)


def _get_lstm_class():
    """Dynamically load ApexLSTM from train_lstm.py."""
    try:
        _dir = Path(__file__).parent
        spec = importlib.util.spec_from_file_location("_train_lstm_mod", _dir / "train_lstm.py")
        # We must inject it into __main__ for torch.load to unpickle
        import __main__
        mod = types.ModuleType("__main__")
        mod.__file__ = str(_dir / "train_lstm.py")
        spec.loader.exec_module(mod)
        return mod.ApexLSTM
    except Exception:
        # Define inline if import fails
        class ApexLSTM(nn.Module):
            def __init__(self, n_features=N_FEATURES, hidden=128, layers=2, dropout=0.25):
                super().__init__()
                self.seq_len = SEQ_LEN_LSTM
                self.lstm = nn.LSTM(n_features, hidden, layers, batch_first=True,
                                    bidirectional=True, dropout=dropout if layers > 1 else 0.0)
                self.head = nn.Sequential(
                    nn.LayerNorm(hidden * 2), nn.Dropout(dropout),
                    nn.Linear(hidden * 2, 64), nn.GELU(), nn.Linear(64, 2),
                )
            def forward(self, x):
                if x.dim() == 2:
                    x = x.unsqueeze(1).expand(-1, self.seq_len, -1)
                out, _ = self.lstm(x)
                return self.head(out[:, -1, :])
        return ApexLSTM


def _get_tft_class():
    """Dynamically load ApexTFT from train_tft.py."""
    try:
        _dir = Path(__file__).parent
        spec = importlib.util.spec_from_file_location("_train_tft_mod", _dir / "train_tft.py")
        mod = types.ModuleType("__main__")
        mod.__file__ = str(_dir / "train_tft.py")
        spec.loader.exec_module(mod)
        return mod.ApexTFT, mod.PositionalEncoding
    except Exception:
        # Inline fallback
        class PositionalEncoding(nn.Module):
            def __init__(self, d_model, max_len=512, dropout=0.1):
                super().__init__()
                self.dropout = nn.Dropout(dropout)
                pe = torch.zeros(max_len, d_model)
                pos = torch.arange(max_len).unsqueeze(1).float()
                div = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
                pe[:, 0::2] = torch.sin(pos * div)
                pe[:, 1::2] = torch.cos(pos * div[:d_model // 2])
                self.register_buffer("pe", pe.unsqueeze(0))
            def forward(self, x):
                return self.dropout(x + self.pe[:, :x.size(1)])

        class ApexTFT(nn.Module):
            def __init__(self, n_features=N_FEATURES, d_model=64, n_heads=4, n_layers=2, d_ff=128, dropout=0.15):
                super().__init__()
                self.seq_len = SEQ_LEN_TFT
                self.input_proj = nn.Linear(n_features, d_model)
                self.pos_enc = PositionalEncoding(d_model, max_len=SEQ_LEN_TFT + 8, dropout=dropout)
                self.gru = nn.GRU(d_model, d_model, num_layers=1, batch_first=True, bidirectional=False)
                encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_heads, dim_feedforward=d_ff,
                                                            dropout=dropout, activation="gelu", batch_first=True, norm_first=True)
                self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
                self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Dropout(dropout),
                                          nn.Linear(d_model, 32), nn.GELU(), nn.Linear(32, 2))
            def forward(self, x):
                if x.dim() == 2:
                    x = x.unsqueeze(1).expand(-1, self.seq_len, -1)
                h = self.input_proj(x)
                h = self.pos_enc(h)
                h, _ = self.gru(h)
                h = self.transformer(h)
                return self.head(h[:, -1])
        return ApexTFT, PositionalEncoding


# ── data loading ──────────────────────────────────────────────────────────────

def load_features() -> "pd.DataFrame":
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
    # LEFT JOIN ohlcv_bars to get raw close price for TimesFM path
    query = (
        f"SELECT f.time, f.symbol, COALESCE(o.close, 0) AS close, "
        f"{', '.join('f.' + c for c in FEATURE_COLS)} "
        f"FROM features f "
        f"LEFT JOIN ohlcv_bars o ON o.symbol = f.symbol AND o.time = f.time "
        f"ORDER BY f.symbol, f.time ASC"
    )
    df = pd.read_sql(query, conn, parse_dates=["time"])
    conn.close()
    logger.info("Loaded %d rows for %d symbols", len(df), df["symbol"].nunique())
    return df


def preprocess_symbol(g: "pd.DataFrame") -> np.ndarray:
    """Normalize a single symbol's features: fill NaN, clip, z-score, clip post."""
    arr = g[FEATURE_COLS].to_numpy(dtype=np.float32)
    arr = pd.DataFrame(arr).ffill().bfill().fillna(0.0).to_numpy(dtype=np.float32)
    arr = np.clip(arr, -100.0, 100.0)
    m, s = np.nanmean(arr, axis=0), np.nanstd(arr, axis=0) + 1e-8
    arr = (arr - m) / s
    arr = np.clip(arr, -10.0, 10.0)
    return arr


def get_labels(g: "pd.DataFrame") -> np.ndarray:
    """5-bar forward return direction with ±0.05% dead zone."""
    fwd = g["returns_1"].shift(-5).to_numpy()
    return np.where(fwd > 0.0005, 1.0, np.where(fwd < -0.0005, 0.0, np.nan))


# ── model loaders ─────────────────────────────────────────────────────────────

def load_xgb_model(model_id: str):
    """Load XGBoost model — supports both .pkl (XGBClassifier) and .json/.ubj (Booster)."""
    # Try pickle first (from train_xgb.py)
    pkl_path = MODEL_DIR / f"{model_id}.pkl"
    if pkl_path.exists():
        import pickle
        with open(str(pkl_path), "rb") as f:
            model = pickle.load(f)
        logger.info("Loaded XGB %s from %s (pickle)", model_id, pkl_path)
        return model, "sklearn"

    # Try directory-based Booster format
    for ext in ["model.json", "model.ubj", "booster.json"]:
        path = MODEL_DIR / model_id / ext
        if path.exists():
            booster = xgb.Booster()
            booster.load_model(str(path))
            logger.info("Loaded XGB %s from %s (Booster)", model_id, path)
            return booster, "booster"

    raise FileNotFoundError(f"No XGB model found for {model_id} in {MODEL_DIR}")


def load_neural_model(model_id: str, model_class, extra_classes: list | None = None):
    """Load a PyTorch model, injecting classes into __main__ for unpickling."""
    path = MODEL_DIR / model_id / "model.pt"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    import __main__
    __main__.__dict__[model_class.__name__] = model_class
    if extra_classes:
        for cls in extra_classes:
            __main__.__dict__[cls.__name__] = cls
    model = torch.load(str(path), weights_only=False, map_location=device)
    model.eval()
    logger.info("Loaded %s from %s (device=%s)", model_id, path, device)
    return model


# ── prediction helpers ────────────────────────────────────────────────────────

def predict_xgb(xgb_model_tuple: tuple, X_flat: np.ndarray) -> np.ndarray:
    """Returns probability of UP class [0, 1]. Supports both XGBClassifier and Booster."""
    model, model_type = xgb_model_tuple
    if model_type == "sklearn":
        # XGBClassifier — has predict_proba
        proba = model.predict_proba(X_flat)
        return proba[:, 1] if proba.shape[1] > 1 else proba[:, 0]
    else:
        # Booster — outputs raw probabilities for binary
        dmat = xgb.DMatrix(X_flat)
        raw = model.predict(dmat)
        return raw.flatten()


def load_timesfm_model() -> str:
    """
    Verify the TimesFM HTTP service is reachable and returns its URL.
    In-process loading is not possible on Python 3.12+ (timesfm requires <3.12);
    instead we call the running Docker service at TIMESFM_SERVICE_URL.
    """
    import httpx
    url = os.getenv("TIMESFM_SERVICE_URL", "http://localhost:8010")
    try:
        resp = httpx.get(f"{url}/health", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("model_loaded"):
            raise RuntimeError("TimesFM service is up but model not yet loaded — wait for /ready")
        logger.info("TimesFM service confirmed at %s (backend=%s)", url, data.get("backend"))
        return url
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Cannot reach TimesFM service at {url}. "
            f"Start it first: cd infra && docker compose up -d timesfm-service. Error: {exc}"
        ) from exc


def predict_timesfm_batch(
    service_url: str,             # URL of TimesFM HTTP service
    close_sequences: list,        # list of 1-D numpy arrays / lists of close prices
    batch_size: int = 50_000,     # large batches = fewer HTTP round-trips
) -> np.ndarray:
    """
    Call POST /predict/batch on the TimesFM service.
    Sends close-price sequences in large batches to minimise HTTP overhead;
    the service processes them internally in its own chunked loop.
    """
    import httpx
    results: list[float] = []
    n = len(close_sequences)
    for start in range(0, n, batch_size):
        batch = close_sequences[start : start + batch_size]
        payload = {
            "sequences": [
                seq.tolist() if hasattr(seq, "tolist") else list(seq)
                for seq in batch
            ]
        }
        try:
            resp = httpx.post(
                f"{service_url}/predict/batch",
                json=payload,
                timeout=3600.0,   # 1-hour ceiling; large CPU batches take time
            )
            resp.raise_for_status()
            results.extend(resp.json()["prob_up"])
            logger.info("TimesFM batch %d-%d / %d done", start, min(start+batch_size, n), n)
        except Exception as exc:
            logger.warning("TimesFM HTTP batch [%d:%d] failed: %s — using 0.5 fallback",
                           start, start + batch_size, exc)
            results.extend([0.5] * len(batch))
    return np.array(results, dtype=np.float32)


def predict_lstm(model, X_seq: torch.Tensor) -> np.ndarray:
    """X_seq: [N, seq_len, features]. Returns P(UP)."""
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        out = model(X_seq.to(device))[:, 0]
        out = torch.nan_to_num(out, nan=0.0)
        probs = torch.sigmoid(out).cpu().numpy()
    return probs


def predict_tft(model, X_seq: torch.Tensor) -> np.ndarray:
    """X_seq: [N, seq_len, features]. Returns P(UP)."""
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        out = model(X_seq.to(device))[:, 0]
        out = torch.nan_to_num(out, nan=0.0)
        probs = torch.sigmoid(out).cpu().numpy()
    return probs


# ── ensemble training ─────────────────────────────────────────────────────────

def build_meta_features(
    df: "pd.DataFrame",
    xgb_tuple: tuple,
    lstm_model,
    third_model,                    # TFT model or TimesFM model (loaded in-process)
    third_model_type: str = "tft",  # "tft" or "timesfm"
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build meta-features: [P_xgb, P_lstm, P_third] for each sample.
    third_model_type="tft"     → local PyTorch TFT model
    third_model_type="timesfm" → in-process TimesFM library model, uses close prices
    Returns (meta_X, meta_y) aligned arrays.
    """
    all_x_xgb   = []   # 22 features (with sym_enc)
    all_x_lstm  = []
    all_x_third = []   # TFT feature seqs OR close-price arrays for TimesFM
    all_labels  = []

    SEQ_LEN_THIRD = SEQ_LEN_TFT if third_model_type == "tft" else 128
    has_close = "close" in df.columns

    # Build a global symbol-to-int mapping consistent across all symbols
    symbols_sorted = sorted(df["symbol"].unique())
    sym2enc = {s: i for i, s in enumerate(symbols_sorted)}

    logger.info("Collecting sequences per symbol (third_model=%s)...", third_model_type)
    for sym, grp in df.groupby("symbol"):
        g = grp.sort_values("time").reset_index(drop=True)
        arr = preprocess_symbol(g)   # shape [n, 21]
        labels = get_labels(g)
        close_arr = g["close"].to_numpy(dtype=np.float32) if has_close else None
        n = len(arr)
        sym_code = float(sym2enc[sym])
        min_seq = max(SEQ_LEN_LSTM, SEQ_LEN_THIRD)

        for i in range(min_seq, n - 5):
            lbl = labels[i]
            if np.isnan(lbl):
                continue
            seq = arr[i - min_seq : i]
            if np.isnan(seq).any():
                continue
            # XGB needs 22 features: 21 + sym_enc
            xgb_row = np.append(arr[i], sym_code).astype(np.float32)
            all_x_xgb.append(xgb_row)
            all_x_lstm.append(arr[i - SEQ_LEN_LSTM : i])
            if third_model_type == "timesfm":
                # TimesFM uses raw close prices
                if close_arr is not None:
                    close_seq = close_arr[i - SEQ_LEN_THIRD : i]
                    if np.isnan(close_seq).any() or close_seq[-1] <= 0:
                        close_seq = np.ones(SEQ_LEN_THIRD, dtype=np.float32)
                    all_x_third.append(close_seq)
                else:
                    all_x_third.append(np.ones(SEQ_LEN_THIRD, dtype=np.float32))
            else:
                all_x_third.append(arr[i - SEQ_LEN_THIRD : i])
            all_labels.append(lbl)

    logger.info("Running batch inference on %d samples...", len(all_labels))

    # XGB batch (22 features)
    X_xgb = np.array(all_x_xgb, dtype=np.float32)
    p_xgb = predict_xgb(xgb_tuple, X_xgb)

    # LSTM batch
    X_lstm_t = torch.tensor(np.array(all_x_lstm, dtype=np.float32))
    BATCH = 2048
    p_lstm_list = []
    for start in range(0, len(X_lstm_t), BATCH):
        chunk = X_lstm_t[start:start+BATCH]
        p_lstm_list.append(predict_lstm(lstm_model, chunk))
    p_lstm = np.concatenate(p_lstm_list)

    # Third model batch
    if third_model_type == "timesfm":
        logger.info("Running TimesFM batch inference via HTTP service...")
        p_third = predict_timesfm_batch(third_model, all_x_third)
    else:
        # TFT PyTorch batch
        X_tft_t = torch.tensor(np.array(all_x_third, dtype=np.float32))
        p_tft_list = []
        for start in range(0, len(X_tft_t), BATCH):
            chunk = X_tft_t[start:start+BATCH]
            p_tft_list.append(predict_tft(third_model, chunk))
        p_third = np.concatenate(p_tft_list)

    meta_X = np.stack([p_xgb, p_lstm, p_third], axis=1).astype(np.float32)
    meta_y = np.array(all_labels, dtype=np.float32)

    logger.info("Meta features built: X=%s  pos=%.1f%%", meta_X.shape, 100.0 * meta_y.mean())
    return meta_X, meta_y


def train_meta_learner(
    df: "pd.DataFrame",
    xgb_tuple: tuple,
    lstm_model,
    third_model,
    n_folds: int = 4,
    third_model_type: str = "tft",
) -> tuple["LogisticRegression", "StandardScaler", list[dict]]:
    """Walk-forward train a meta logistic regression ensemble."""
    logger.info("Building meta features (may take a few minutes)...")
    meta_X, meta_y = build_meta_features(df, xgb_tuple, lstm_model, third_model, third_model_type)
    logger.info("Meta features: X=%s  pos=%.1f%%", meta_X.shape, 100.0 * meta_y.mean())
    
    n = len(meta_X)
    fold_sz = n // (n_folds + 1)
    results = []
    best_lr_model = None
    best_scaler   = None
    
    for fold in range(n_folds):
        oos_start = (fold + 1) * fold_sz
        oos_end   = min(oos_start + fold_sz, n)
        if oos_start < fold_sz or oos_end > n:
            continue
        
        X_is, y_is   = meta_X[:oos_start],       meta_y[:oos_start]
        X_oos, y_oos = meta_X[oos_start:oos_end], meta_y[oos_start:oos_end]
        
        scaler = StandardScaler()
        X_is_s  = scaler.fit_transform(X_is)
        X_oos_s = scaler.transform(X_oos)
        
        lr_model = LogisticRegression(max_iter=1000, class_weight="balanced")
        lr_model.fit(X_is_s, y_is)
        
        preds  = lr_model.predict(X_oos_s)
        acc    = float((preds == y_oos).mean())
        pred_r = np.where(preds == 1, 1.0, -1.0)
        std_r  = max(float(pred_r.std()), 1e-4)
        sharpe = float(pred_r.mean() / std_r * (252 * 26) ** 0.5)
        
        results.append({
            "fold": fold, "oos_acc": round(acc, 4),
            "oos_sharpe": round(sharpe, 4),
            "is_bars": len(X_is), "oos_bars": len(X_oos),
        })
        logger.info("Fold %d  acc=%.3f  sharpe=%.3f [IS=%d OOS=%d]",
                    fold, acc, sharpe, len(X_is), len(X_oos))
        
        if fold == n_folds - 1:  # save the last fold's model (most IS data)
            best_lr_model = lr_model
            best_scaler   = scaler
    
    if best_lr_model is None:
        raise RuntimeError("No folds completed")
    
    return best_lr_model, best_scaler, results


# ── persistence ───────────────────────────────────────────────────────────────

def save_ensemble(
    lr_model: "LogisticRegression",
    scaler: "StandardScaler",
    model_id: str,
    xgb_id: str,
    lstm_id: str,
    third_id: str,
    third_key: str = "tft",
) -> Path:
    out = MODEL_DIR / model_id
    out.mkdir(parents=True, exist_ok=True)

    joblib.dump(lr_model, str(out / "meta_lr.joblib"))
    joblib.dump(scaler,   str(out / "meta_scaler.joblib"))

    meta = {
        "model_id":   model_id,
        "model_type": "ensemble",
        "components": {"xgb": xgb_id, "lstm": lstm_id, third_key: third_id},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    logger.info("Ensemble saved → %s", out)
    return out


def register_redis(
    model_id: str,
    folds: list[dict],
    components: dict,
    output_path: Path,
) -> None:
    if not _HAS_REDIS:
        return
    try:
        r = _redis()
        avg_sharpe = float(np.mean([f["oos_sharpe"] for f in folds]))
        avg_hit    = float(np.mean([f["oos_acc"]    for f in folds])) * 100
        
        payload = {
            "model_id":       model_id,
            "model_type":     "ensemble",
            "status":         "live",
            "val_sharpe":     round(avg_sharpe, 4),
            "val_hit_rate":   round(avg_hit, 2),
            "components":     components,
            "model_path":     str(output_path / "meta_lr.joblib"),
            "trained_at":     datetime.now(timezone.utc).isoformat(),
            "version":        1,
        }
        r.set(f"apex:models:{model_id}", json.dumps(payload))
        r.sadd("apex:models:all", model_id)
        logger.info("Registered %s in Redis (sharpe=%.3f hit=%.2f%%)", model_id, avg_sharpe, avg_hit)
    except Exception as e:
        logger.warning("Redis registration failed: %s", e)


# ── MLflow ────────────────────────────────────────────────────────────────────

def log_mlflow(
    model_id: str,
    folds: list[dict],
    components: dict,
    output_path: Path,
    duration: float,
) -> Optional[str]:
    if not _MLFLOW:
        return None
    try:
        mlflow.set_tracking_uri(MLFLOW_URI)
        mlflow.set_experiment(MLFLOW_EXP)
        with mlflow.start_run(run_name=model_id):
            avg_sharpe = float(np.mean([f["oos_sharpe"] for f in folds]))
            avg_hit    = float(np.mean([f["oos_acc"]    for f in folds]))
            mlflow.log_params({
                "model_id":   model_id,
                "n_folds":    len(folds),
                "components": json.dumps(components),
            })
            mlflow.log_metrics({
                "avg_sharpe":   round(avg_sharpe, 4),
                "avg_hit_rate": round(avg_hit, 4),
                "duration_s":   round(duration, 1),
            })
            for f in folds:
                pfx = f"fold_{f['fold']}"
                mlflow.log_metrics({
                    f"{pfx}_sharpe":   f["oos_sharpe"],
                    f"{pfx}_hit":      f["oos_acc"],
                })
            run_id = mlflow.active_run().info.run_id
        return run_id
    except Exception as e:
        logger.warning("MLflow logging failed: %s", e)
        return None


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="APEX Ensemble Model Trainer")
    parser.add_argument("--model-id",    default="ENS_v1")
    parser.add_argument("--xgb-id",     default="XGB_v2")
    parser.add_argument("--lstm-id",    default="LSTM_v4")
    parser.add_argument("--tft-id",     default=None)
    parser.add_argument("--timesfm-id", default=None,
                        help="Use TimesFM (in-process) instead of TFT as third ensemble component")
    parser.add_argument("--n-folds",    type=int, default=4)
    args = parser.parse_args()

    # Determine which third-component to use
    use_timesfm = args.timesfm_id is not None
    if use_timesfm:
        third_id  = args.timesfm_id
        third_key = "timesfm"
    else:
        third_id  = args.tft_id or "TFT_v3"   # backward-compat default
        third_key = "tft"

    logger.info("=== APEX Ensemble Training: %s ===", args.model_id)
    logger.info("Components: XGB=%s  LSTM=%s  %s=%s",
                args.xgb_id, args.lstm_id, third_key.upper(), third_id)

    if not _HAS_PG:
        raise ImportError("psycopg2 required")
    if not _HAS_XGB:
        raise ImportError("xgboost required")
    if not _HAS_TORCH:
        raise ImportError("torch required")
    if not _HAS_SKLEARN:
        raise ImportError("scikit-learn required")

    # Load base models
    logger.info("Loading base models...")
    xgb_tuple = load_xgb_model(args.xgb_id)

    ApexLSTM = _get_lstm_class()
    import __main__
    __main__.ApexLSTM = ApexLSTM
    lstm_model = load_neural_model(args.lstm_id, ApexLSTM)

    if use_timesfm:
        logger.info("Validating TimesFM HTTP service...")
        third_model = load_timesfm_model()   # returns service URL string
    else:
        ApexTFT, PositionalEncoding = _get_tft_class()
        __main__.ApexTFT = ApexTFT
        __main__.PositionalEncoding = PositionalEncoding
        third_model = load_neural_model(third_id, ApexTFT, extra_classes=[PositionalEncoding])

    # Load features (includes close column for TimesFM path)
    df = load_features()

    # Train ensemble
    t0 = time.time()
    lr_model, scaler, folds = train_meta_learner(
        df, xgb_tuple, lstm_model, third_model,
        n_folds=args.n_folds, third_model_type=third_key,
    )
    duration = time.time() - t0

    avg_sharpe = float(np.mean([f["oos_sharpe"] for f in folds]))
    avg_hit    = float(np.mean([f["oos_acc"]    for f in folds])) * 100

    # Save
    components  = {"xgb": args.xgb_id, "lstm": args.lstm_id, third_key: third_id}
    output_path = save_ensemble(lr_model, scaler, args.model_id,
                                args.xgb_id, args.lstm_id, third_id, third_key)

    # Register
    register_redis(args.model_id, folds, components, output_path)

    # MLflow
    run_id = log_mlflow(args.model_id, folds, components, output_path, duration)
    if run_id:
        print(f"View run {args.model_id} at: {MLFLOW_URI}/#/experiments/*/runs/{run_id}")

    print("\n" + "=" * 50)
    print(f"Model:        {args.model_id}")
    print(f"Components:   XGB={args.xgb_id}  LSTM={args.lstm_id}  {third_key.upper()}={third_id}")
    print(f"Avg Sharpe:   {avg_sharpe:.4f}")
    print(f"Avg Hit Rate: {avg_hit:.2f}%")
    print(f"MLflow Run:   {run_id or 'N/A'}")
    print(f"Duration:     {duration:.0f}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
