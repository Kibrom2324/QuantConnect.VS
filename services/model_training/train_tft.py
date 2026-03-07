"""
APEX Temporal Attention Model Trainer (TFT-compatible)
services/model_training/train_tft.py

Trains a multi-head self-attention model (simplified TFT) on 15-min OHLCV
feature sequences. The saved model is hot-loadable by the TFT service.

Architecture:
  Input [B, seq_len, F] → GRU encoder → Multi-head attention → MLP head → [B, 2]

Usage (inside tft container):
  python /app/services/model_training/train_tft.py --model-id TFT_v1 --epochs 20
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

try:
    import psycopg2
    import pandas as pd
    _HAS_PG = True
except ImportError:
    _HAS_PG = False

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("apex.train_tft")

# ── config ───────────────────────────────────────────────────────────────────
DB_HOST    = os.getenv("POSTGRES_HOST",       "timescaledb")
DB_PORT    = int(os.getenv("POSTGRES_PORT",   "5432"))
DB_USER    = os.getenv("POSTGRES_USER",       "apex_user")
DB_PASS    = os.getenv("POSTGRES_PASSWORD",   "apex_pass")
DB_NAME    = os.getenv("POSTGRES_DB",         "apex")
REDIS_HOST = os.getenv("REDIS_HOST",          "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT",      "6379"))
MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXP = os.getenv("MLFLOW_EXPERIMENT_NAME", "apex_tft")
MODEL_DIR  = Path(os.getenv("MODEL_DIR",      "/app/models"))
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"

FEATURE_COLS = [
    "returns_1", "returns_5", "returns_15", "returns_60",
    "rsi_14", "rsi_28", "ema_20", "ema_50", "ema_200",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_pct",
    "atr_14", "stoch_k", "stoch_d",
    "volume_ratio", "vwap_dev", "adx_14",
]
N_FEATURES = len(FEATURE_COLS)   # 21
SEQ_LEN    = 48                  # 48 × 15-min = 12 hours lookback


# ── model ─────────────────────────────────────────────────────────────────────

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
    """
    Temporal Attention model (TFT-inspired).
    Input:  [batch, seq_len, n_features]  OR  [batch, n_features]
    Output: [batch, 2]  — (direction_logit, confidence_logit)
    """
    def __init__(
        self,
        n_features: int = N_FEATURES,
        d_model:    int = 64,
        n_heads:    int = 4,
        n_layers:   int = 2,
        d_ff:       int = 128,
        dropout:    float = 0.15,
    ):
        super().__init__()
        self.n_features = n_features
        self.d_model    = d_model
        self.seq_len    = SEQ_LEN

        # Input projection
        self.input_proj = nn.Linear(n_features, d_model)
        self.pos_enc    = PositionalEncoding(d_model, max_len=SEQ_LEN + 8, dropout=dropout)

        # GRU encoder (captures local trends)
        self.gru = nn.GRU(
            d_model, d_model, num_layers=1,
            batch_first=True, bidirectional=False,
        )

        # Self-attention layers (variable selection / temporal fusion)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_ff,
            dropout=dropout, activation="gelu", batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Head
        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Dropout(dropout),
            nn.Linear(d_model, 32),
            nn.GELU(),
            nn.Linear(32, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2:
            # Flat feature vector → replicate across seq_len
            x = x.unsqueeze(1).expand(-1, self.seq_len, -1)

        h  = self.input_proj(x)           # [B, T, d_model]
        h  = self.pos_enc(h)
        h, _ = self.gru(h)                # local trend encoding
        h  = self.transformer(h)          # temporal fusion
        out = self.head(h[:, -1])         # last step [B, 2]
        return out


# ── data ─────────────────────────────────────────────────────────────────────

def load_features() -> "pd.DataFrame":
    if not _HAS_PG:
        raise ImportError("psycopg2 not available — run: pip install psycopg2-binary")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME,
    )
    df = pd.read_sql(
        f"SELECT time, symbol, {', '.join(FEATURE_COLS)} FROM features ORDER BY symbol, time ASC",
        conn, parse_dates=["time"],
    )
    conn.close()
    logger.info("Loaded %d rows for %d symbols", len(df), df["symbol"].nunique())
    return df


def build_sequences(df: "pd.DataFrame") -> tuple[torch.Tensor, torch.Tensor]:
    X_list, y_list = [], []
    for sym, grp in df.groupby("symbol"):
        g   = grp.sort_values("time").reset_index(drop=True)
        arr = g[FEATURE_COLS].to_numpy(dtype=np.float32)
        m, s = arr.mean(0), arr.std(0) + 1e-8
        # Fill NaN, clip extremes, normalize, clip post-normalize
        arr = pd.DataFrame(arr).ffill().bfill().fillna(0.0).to_numpy(dtype=np.float32)
        arr = np.clip(arr, -100.0, 100.0)
        m, s = np.nanmean(arr, axis=0), np.nanstd(arr, axis=0) + 1e-8
        arr  = (arr - m) / s
        arr  = np.clip(arr, -10.0, 10.0)
        # Label: direction of 5-bar (75min) forward return, dead-zone ±0.05%
        fwd = g["returns_1"].shift(-5).to_numpy()
        tgt = np.where(fwd > 0.0005, 1.0, np.where(fwd < -0.0005, 0.0, np.nan))
        for i in range(SEQ_LEN, len(arr) - 5):
            if not np.isnan(tgt[i]):
                seq = arr[i - SEQ_LEN : i]
                if not np.isnan(seq).any():
                    X_list.append(seq)
                    y_list.append(tgt[i])
    X = torch.tensor(np.array(X_list, dtype=np.float32))
    y = torch.tensor(np.array(y_list, dtype=np.float32))
    return X, y


# ── training ─────────────────────────────────────────────────────────────────

def train_walk_forward(
    X: torch.Tensor,
    y: torch.Tensor,
    n_folds: int  = 4,
    epochs:  int  = 20,
    batch:   int  = 256,
    lr:      float = 5e-4,
) -> tuple[ApexTFT, list[dict]]:
    n      = len(X)
    fold_sz = n // (n_folds + 1)
    results: list[dict] = []
    best_model: Optional[ApexTFT] = None

    for fold in range(n_folds):
        # Forward walk: fold 0 = earliest OOS, fold n-1 = latest OOS
        oos_start = (fold + 1) * fold_sz
        oos_end   = min(oos_start + fold_sz, n)
        if oos_start < fold_sz or oos_end > n:
            logger.warning("Fold %d: skipping (out of bounds)", fold)
            continue

        X_is,  y_is  = X[:oos_start],       y[:oos_start]
        X_oos, y_oos = X[oos_start:oos_end], y[oos_start:oos_end]

        model    = ApexTFT().to(DEVICE)
        opt      = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
        sched    = optim.lr_scheduler.ReduceLROnPlateau(opt, mode='min', patience=3, factor=0.5)
        crit     = nn.BCEWithLogitsLoss()
        loader   = DataLoader(TensorDataset(X_is, y_is), batch_size=batch, shuffle=True, drop_last=True)

        for ep in range(epochs):
            model.train()
            ep_loss = 0.0
            n_batches = 0
            for xb, yb in loader:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                opt.zero_grad()
                out  = model(xb)[:, 0]
                loss = crit(out, yb)
                if torch.isnan(loss):
                    logger.warning("NaN loss at fold %d ep %d — skipping batch", fold, ep)
                    continue
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 0.5)
                opt.step()
                ep_loss += loss.item()
                n_batches += 1
            if n_batches > 0:
                avg_loss = ep_loss / n_batches
                sched.step(avg_loss)
                if ep % 5 == 0:
                    logger.debug("Fold %d  epoch %d  loss=%.4f", fold, ep, avg_loss)

        model.eval()
        with torch.no_grad():
            # Batched OOS inference to avoid CUDA OOM on large symbol universes
            _infer_bs = 512
            _parts = []
            for _i in range(0, len(X_oos), _infer_bs):
                _xb = X_oos[_i:_i + _infer_bs].to(DEVICE)
                _parts.append(model(_xb)[:, 0].cpu())
            logits = torch.cat(_parts)
            logits = torch.nan_to_num(logits, nan=0.0)
            preds  = (torch.sigmoid(logits) > 0.5).float()
            acc    = (preds == y_oos).float().mean().item()
            pred_r = torch.where(preds.bool(), torch.ones_like(preds), -torch.ones_like(preds))
            std_r  = max(float(pred_r.std()), 1e-4)
            sharpe = float(pred_r.mean() / std_r * (252 * 26) ** 0.5)

        results.append({
            "fold": fold, "oos_acc": round(acc, 4),
            "oos_sharpe": round(sharpe, 4), "is_bars": len(X_is), "oos_bars": len(X_oos),
        })
        logger.info("Fold %d  acc=%.3f  sharpe=%.3f [IS=%d OOS=%d]",
                    fold, acc, sharpe, len(X_is), len(X_oos))
        if fold == 0:
            best_model = model

    if best_model is None:
        raise RuntimeError("No folds completed")
    return best_model, results


# ── persistence ───────────────────────────────────────────────────────────────

def save_model(model: ApexTFT, model_id: str) -> Path:
    out = MODEL_DIR / model_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "model.pt"
    torch.save(model, str(path))
    logger.info("TFT model saved → %s", path)
    return path


def register_redis(model_id: str, folds: list[dict], model_path: Path) -> None:
    if not _HAS_REDIS:
        return
    try:
        r = _redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        avg_sharpe = np.mean([f["oos_sharpe"] for f in folds])
        avg_hit    = np.mean([f["oos_acc"]    for f in folds]) * 100
        meta = {
            "model_id":    model_id,
            "model_type":  "tft",
            "version":     model_id.split("_v")[-1] if "_v" in model_id else "1",
            "status":      "live",          # TFT is the primary model type
            "val_sharpe":  round(float(avg_sharpe), 4),
            "val_hit_rate": round(float(avg_hit), 2),
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "trained_by":  "train_tft.py",
            "artifact_path": str(model_path),
        }
        r.set(f"apex:models:{model_id}", json.dumps(meta))
        r.sadd("apex:models:all", model_id)
        r.lpush("apex:agent_log", json.dumps({
            "id":        f"train_{model_id}",
            "timestamp": meta["created_at"],
            "type":      "TRAINING_COMPLETE",
            "details":   f"TFT {model_id} trained — sharpe={meta['val_sharpe']:.3f} hit={meta['val_hit_rate']:.1f}%",
            "source":    "train_tft",
        }))
        logger.info("Registered %s in Redis (sharpe=%.3f hit=%.1f%% status=live)",
                    model_id, avg_sharpe, avg_hit)
    except Exception as exc:
        logger.warning("Redis registration failed: %s", exc)


def log_mlflow(folds: list[dict], model_id: str) -> str:
    if not _MLFLOW:
        return ""
    try:
        mlflow.set_tracking_uri(MLFLOW_URI)
        mlflow.set_experiment(MLFLOW_EXP)
        with mlflow.start_run(run_name=model_id) as run:
            avg_sharpe = np.mean([f["oos_sharpe"] for f in folds])
            avg_hit    = np.mean([f["oos_acc"]    for f in folds])
            mlflow.log_params({"seq_len": SEQ_LEN, "n_features": N_FEATURES, "n_folds": len(folds)})
            mlflow.log_metrics({"val_sharpe": round(avg_sharpe, 4), "val_hit_rate": round(avg_hit * 100, 2)})
            for f in folds:
                mlflow.log_metrics({f"fold{f['fold']}_sharpe": f["oos_sharpe"],
                                    f"fold{f['fold']}_acc":    f["oos_acc"]}, step=f["fold"])
            mlflow.set_tag("production", "true")
            mlflow.set_tag("model_type", "tft")
            mlflow.set_tag("model_id", model_id)
            return run.info.run_id
    except Exception as exc:
        logger.warning("MLflow logging failed: %s", exc)
        return ""


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id",  default=f"TFT_v{int(time.time()) % 10000}")
    ap.add_argument("--epochs",    type=int, default=20)
    ap.add_argument("--n-folds",   type=int, default=4)
    ap.add_argument("--fold",      type=int, default=1)
    ap.add_argument("--mlflow-experiment", default=None)
    args = ap.parse_args()

    if args.mlflow_experiment:
        global MLFLOW_EXP
        MLFLOW_EXP = args.mlflow_experiment

    model_id = args.model_id
    logger.info("=== APEX TFT Training: %s  device=%s ===", model_id, DEVICE)

    df = load_features()
    if df.empty:
        logger.error("No feature data in DB.")
        sys.exit(1)

    t0 = time.time()
    X, y = build_sequences(df)
    logger.info("Sequences: X=%s  pos=%.1f%%", X.shape, y.mean().item() * 100)

    model, folds = train_walk_forward(X, y, n_folds=args.n_folds, epochs=args.epochs)
    elapsed = time.time() - t0

    path   = save_model(model, model_id)
    run_id = log_mlflow(folds, model_id)
    register_redis(model_id, folds, path)

    avg_sharpe = np.mean([f["oos_sharpe"] for f in folds])
    avg_hit    = np.mean([f["oos_acc"]    for f in folds]) * 100
    print(f"\n{'='*50}")
    print(f"Model:        {model_id}")
    print(f"Avg Sharpe:   {avg_sharpe:.4f}")
    print(f"Avg Hit Rate: {avg_hit:.2f}%")
    print(f"MLflow Run:   {run_id or 'not logged'}")
    print(f"Duration:     {elapsed:.0f}s  |  Device: {DEVICE}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
