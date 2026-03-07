# APEX Algorithmic Trading System — Complete Project Overview
> Generated: 2026-02-28 | Status: Paper Trading Active | Tests: 175 passing

---

## Table of Contents
1. [What is APEX?](#1-what-is-apex)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Data Flow (End-to-End)](#3-data-flow-end-to-end)
4. [All Source Files](#4-all-source-files)
5. [Microservices Detail](#5-microservices-detail)
6. [Shared Library](#6-shared-library)
7. [Safety Architecture](#7-safety-architecture)
8. [ML / Model Pipeline](#8-ml--model-pipeline)
9. [Twitter / TFT Sentiment Pipeline](#9-twitter--tft-sentiment-pipeline)
10. [Infrastructure & Kubernetes](#10-infrastructure--kubernetes)
11. [Next.js Dashboard (BFF)](#11-nextjs-dashboard-bff)
12. [Test Suite](#12-test-suite)
13. [Configuration Files](#13-configuration-files)
14. [Operational Scripts](#14-operational-scripts)
15. [Bug Fixes & Critical Changes](#15-bug-fixes--critical-changes)
16. [Phase-by-Phase Build Log](#16-phase-by-phase-build-log)
17. [Go-Live Checklist](#17-go-live-checklist)

---

## 1. What is APEX?

APEX is a **production-grade, event-driven algorithmic trading system** built on:
- Python 3.12 + asyncio for all microservices
- Apache Kafka 3.7 as the central message bus
- QuantConnect LEAN engine for backtesting and live execution
- Temporal Fusion Transformer (TFT) neural network for price prediction
- Black-Litterman portfolio construction for weight allocation
- Redis + file dual-layer kill switch for emergency halt
- TimescaleDB for tick/OHLCV storage
- Kubernetes (Kustomize) for deployment
- Next.js 14 BFF dashboard for real-time monitoring

**Universe:** US equities (Alpaca broker), configurable symbols via `configs/limits.yaml`

**Target metrics for Go-Live:**
- Win rate ≥ 52%
- Sharpe ratio ≥ 1.2
- Zero daily loss limit breaches in 30-day paper window
- Max position weight ±15%, gross exposure ≤ 100%

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        APEX MICROSERVICES                           │
│                                                                     │
│  ┌──────────────┐    ┌───────────────────┐    ┌─────────────────┐  │
│  │ Data         │───▶│ Feature           │───▶│ TFT Model       │  │
│  │ Ingestion    │    │ Engineering       │    │ Inference       │  │
│  │ (port 8001)  │    │ (port 8002)       │    │ (port 8003)     │  │
│  └──────────────┘    └───────────────────┘    └─────────────────┘  │
│        │                      │                       │            │
│        ▼                      ▼                       ▼            │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                   APACHE KAFKA (port 9092)                   │  │
│  │  market.raw │ market.engineered │ predictions.tft │ signals  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│        │                      │                       │            │
│        ▼                      ▼                       ▼            │
│  ┌──────────────┐    ┌───────────────────┐    ┌─────────────────┐  │
│  │ LEAN Alpha   │    │ Signal Engine     │    │ Risk Engine     │  │
│  │ (6 alphas)   │───▶│ + Black-Litterman │───▶│ + Kill Switch   │  │
│  │ (port 8004)  │    │ (port 8005)       │    │ (port 8006)     │  │
│  └──────────────┘    └───────────────────┘    └─────────────────┘  │
│                                                        │            │
│                                                        ▼            │
│  ┌──────────────┐    ┌───────────────────┐    ┌─────────────────┐  │
│  │ Exit Monitor │◀───│ Execution Agent   │◀───│ (approved sigs) │  │
│  │ (port 8008)  │    │ + Reconciliation  │    └─────────────────┘  │
│  └──────────────┘    │ (port 8007)       │                         │
│                      └───────────────────┘                         │
│                                                                     │
│  ┌──────────────┐    ┌───────────────────┐    ┌─────────────────┐  │
│  │ Model        │    │ Model Training    │    │ Attribution     │  │
│  │ Monitor      │    │ (walk-forward)    │    │ Tracker         │  │
│  └──────────────┘    └───────────────────┘    └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
         │                      │                       │
         ▼                      ▼                       ▼
  ┌────────────┐        ┌──────────────┐        ┌──────────────┐
  │TimescaleDB │        │   Redis 7    │        │  MLflow 2.x  │
  │(port 5432) │        │  (port 6379) │        │  (port 5000) │
  └────────────┘        └──────────────┘        └──────────────┘
         │
         ▼
  ┌────────────────────────────────────────┐
  │  Next.js 14 Dashboard (port 3000)      │
  │  BFF pattern · SSE · Tailwind CSS      │
  └────────────────────────────────────────┘
```

---

## 3. Data Flow (End-to-End)

```
Step 1 — Data Ingestion
  Alpaca WebSocket ──▶ services/data_ingestion/main.py
                    ──▶ Kafka topic: market.raw (JSON)
                    ──▶ TimescaleDB: OHLCV + tick storage

Step 2 — Feature Engineering
  Kafka: market.raw ──▶ services/feature_engineering/main.py
                     ──▶ RSI, EMA, MACD, Volume z-score, VWAP
                     ──▶ FoldScaler (IS-only fit, JSON sidecar)
                     ──▶ Kafka topic: market.engineered

Step 3 — TFT Inference
  Kafka: market.engineered ──▶ services/model_inference/main.py
                            ──▶ 30-second stale gate (fail-closed)
                            ──▶ TFT model (MLflow run_id validated)
                            ──▶ Kafka topic: predictions.tft

Step 4 — LEAN Alpha Signals
  Kafka: market.engineered ──▶ services/lean_alpha/main.py
                            ──▶ RSI alpha, EMA cross, MACD
                            ──▶ Kafka topic: signals.lean

Step 5 — Signal Ensemble + Black-Litterman
  Kafka: predictions.tft + signals.lean ──▶ services/signal_engine/main.py
                                         ──▶ Weighted ensemble
                                         ──▶ Black-Litterman weights (±15% clip)
                                         ──▶ Kafka topic: signals.approved

Step 6 — Risk Engine Gate
  Kafka: signals.approved ──▶ services/risk_engine/engine.py
                           ──▶ Check dual kill switch (Redis + /tmp/apex_kill.flag)
                           ──▶ Check daily loss limit
                           ──▶ Check position limits
                           ──▶ PASS ──▶ Kafka: orders.pending
                           ──▶ BLOCK ──▶ DLQ, alert fired

Step 7 — Execution
  Kafka: orders.pending ──▶ services/execution/main.py
                         ──▶ Alpaca REST API (httpx, Timeout=30s)
                         ──▶ PositionReconciler (every 60s)
                         ──▶ Circuit breaker (3 fails → 5 min pause)
                         ──▶ Kafka: fill.events

Step 8 — Exit Monitor
  Kafka: fill.events ──▶ services/exit_monitor/main.py
                      ──▶ Stop-loss / take-profit checks
                      ──▶ Inject exit orders → orders.pending
```

---

## 4. All Source Files

### services/ — 8 Microservices

| File | Purpose |
|------|---------|
| `services/data_ingestion/main.py` | Alpaca WebSocket → Kafka `market.raw` + TimescaleDB |
| `services/data_ingestion/__init__.py` | Package init |
| `services/feature_engineering/main.py` | Feature calc, FoldScaler, → `market.engineered` |
| `services/feature_engineering/__init__.py` | Package init |
| `services/model_inference/main.py` | TFT inference, MLflow run_id gate, → `predictions.tft` |
| `services/model_inference/__init__.py` | Package init |
| `services/lean_alpha/main.py` | 6 alpha signal dispatcher |
| `services/lean_alpha/rsi_alpha.py` | RSI mean-reversion alpha |
| `services/lean_alpha/ema_cross_alpha.py` | EMA crossover alpha |
| `services/lean_alpha/macd_alpha.py` | MACD momentum alpha |
| `services/lean_alpha/__init__.py` | Package init |
| `services/signal_engine/main.py` | Ensemble + BL orchestrator |
| `services/signal_engine/ensemble.py` | Weighted signal combination |
| `services/signal_engine/filters.py` | Signal quality filters |
| `services/signal_engine/portfolio.py` | Black-Litterman weight construction |
| `services/signal_engine/__init__.py` | Package init |
| `services/risk_engine/main.py` | Risk engine entry point |
| `services/risk_engine/engine.py` | Core risk checks + kill switch wiring |
| `services/risk_engine/__init__.py` | Package init |
| `services/execution/main.py` | Order execution + PositionReconciler |
| `services/execution/dead_letter_queue.py` | DLQ handler for failed orders |
| `services/execution/__init__.py` | Package init |
| `services/exit_monitor/main.py` | Stop-loss/take-profit monitor |
| `services/exit_monitor/__init__.py` | Package init |
| `services/model_training/walk_forward.py` | Walk-forward cross-validation + MLflow logging |
| `services/model_training/dataset.py` | Dataset builder for TFT training |
| `services/model_training/__init__.py` | Package init |
| `services/model_monitor/main.py` | Model PSI + accuracy drift detection |
| `services/model_monitor/__init__.py` | Package init |
| `services/attribution/tracker.py` | Per-signal P&L attribution |
| `services/attribution/__init__.py` | Package init |
| `services/graceful_shutdown.py` | SIGTERM handler (shared by all services) |
| `services/__init__.py` | Package init |

### shared/core/ — Shared Library

| File | Purpose |
|------|---------|
| `shared/core/kafka_utils.py` | Kafka consumer/producer factory, stale gate, DLQ, commit ordering |
| `shared/core/metrics.py` | All Prometheus metrics (single source of truth) |
| `shared/core/trading_safety.py` | TradingLimits dataclass + dual-layer kill switch |
| `shared/core/circuit_breaker.py` | Async circuit breaker (3 fails → 5 min pause) |
| `shared/core/env.py` | Env var helpers with validation |
| `shared/core/__init__.py` | Package init |
| `shared/__init__.py` | Package init |

### tests/ — Test Suite (175 tests, 0 failures)

| File | Tests | What It Covers |
|------|-------|----------------|
| `tests/test_kill_switch.py` | 16 | Dual-layer kill switch (Redis + file) |
| `tests/test_position_reconciliation.py` | 11 | PositionReconciler tolerance, ghost positions |
| `tests/test_bl_weights.py` | 22 | Black-Litterman ±15% clip, gross ≤ 1.0 |
| `tests/test_signal_staleness.py` | 25 | 30-second stale gate, fail-closed edge cases |
| `tests/test_services.py` | ~50 | Per-service unit tests |
| `tests/test_smoke.py` | ~30 | Import + startup smoke tests |
| `tests/test_integration.py` | ~21 | End-to-end walk-forward + MLflow |
| `tests/__init__.py` | — | Package init |
| `conftest.py` | — | Shared pytest fixtures |

### twitter_tft/ — Sentiment Pipeline

| File | Purpose |
|------|---------|
| `twitter_tft/collectors/base.py` | Abstract tweet collector base |
| `twitter_tft/collectors/scrape.py` | Twitter/X scraper (async httpx) |
| `twitter_tft/collectors/transformer.py` | Raw tweet → feature vector |
| `twitter_tft/collectors/__init__.py` | Package init |
| `twitter_tft/jobs/ingest.py` | Ingest job (scheduled) |
| `twitter_tft/jobs/sentiment.py` | FinBERT sentiment scoring |
| `twitter_tft/jobs/feature_extract.py` | Feature extraction pipeline |
| `twitter_tft/jobs/__init__.py` | Package init |
| `twitter_tft/storage/db.py` | AsyncPG database client |
| `twitter_tft/storage/repositories.py` | Data access layer |
| `twitter_tft/storage/migrations/001_initial.sql` | Schema: weekly-partitioned tweets table |
| `twitter_tft/storage/migrations/002_fix_author_metrics.sql` | Index fix migration |
| `twitter_tft/storage/__init__.py` | Package init |
| `twitter_tft/dataset/build_one_day.py` | Day-level TFT dataset builder |
| `twitter_tft/dataset/__init__.py` | Package init |
| `twitter_tft/config/settings.py` | Config (pydantic-settings) |
| `twitter_tft/config/__init__.py` | Package init |
| `twitter_tft/utils/logging_setup.py` | structlog JSON logging setup |
| `twitter_tft/utils/__init__.py` | Package init |
| `twitter_tft/docker-compose.yml` | Twitter pipeline compose stack |
| `twitter_tft/RUNBOOK.md` | Operational runbook |

### apex-dashboard/ — Next.js 14 BFF Dashboard

| File | Purpose |
|------|---------|
| `apex-dashboard/src/app/page.tsx` | Root page (redirects to dashboard) |
| `apex-dashboard/src/app/layout.tsx` | Root layout with Sidebar |
| `apex-dashboard/src/app/dashboard/page.tsx` | Main trading dashboard |
| `apex-dashboard/src/app/signals/page.tsx` | Signal history page |
| `apex-dashboard/src/app/models/page.tsx` | MLflow model status page |
| `apex-dashboard/src/app/backtest/page.tsx` | Backtest results page |
| `apex-dashboard/src/app/api/signals/route.ts` | BFF: GET signals from TimescaleDB |
| `apex-dashboard/src/app/api/positions/route.ts` | BFF: GET positions from Alpaca |
| `apex-dashboard/src/app/api/pnl/route.ts` | BFF: GET P&L from TimescaleDB |
| `apex-dashboard/src/app/api/weights/route.ts` | BFF: GET BL weights from signal engine |
| `apex-dashboard/src/app/api/models/route.ts` | BFF: GET model metadata from MLflow |
| `apex-dashboard/src/app/api/health/route.ts` | BFF: GET health of all services |
| `apex-dashboard/src/app/api/kill-switch/route.ts` | BFF: GET kill switch state |
| `apex-dashboard/src/app/api/kill-switch/enable/route.ts` | BFF: POST enable kill switch |
| `apex-dashboard/src/app/api/kill-switch/disable/route.ts` | BFF: POST disable kill switch |
| `apex-dashboard/src/app/api/backtests/route.ts` | BFF: GET backtest list |
| `apex-dashboard/src/app/api/backtests/[filename]/route.ts` | BFF: GET individual backtest |
| `apex-dashboard/src/components/KillSwitch.tsx` | Kill switch toggle component |
| `apex-dashboard/src/components/PnLTicker.tsx` | Real-time P&L via SSE |
| `apex-dashboard/src/components/PositionsTable.tsx` | Live positions table |
| `apex-dashboard/src/components/RecentSignals.tsx` | Signal feed component |
| `apex-dashboard/src/components/EnsembleWeightsPie.tsx` | BL weights pie chart |
| `apex-dashboard/src/components/ServiceStatusGrid.tsx` | Service health grid |
| `apex-dashboard/src/components/Sidebar.tsx` | Navigation sidebar |
| `apex-dashboard/src/lib/api.ts` | Client-side API helpers |
| `apex-dashboard/src/lib/types.ts` | TypeScript type definitions |
| `apex-dashboard/Dockerfile` | Dashboard container |
| `apex-dashboard/package.json` | Node dependencies |
| `apex-dashboard/next.config.ts` | Next.js config |
| `apex-dashboard/tailwind.config.ts` | Tailwind CSS config |
| `apex-dashboard/tsconfig.json` | TypeScript config |

### deploy/k8s/ — Kubernetes Manifests

| File | Purpose |
|------|---------|
| `deploy/k8s/base/namespace.yaml` | `apex` namespace |
| `deploy/k8s/base/configmap.yaml` | Shared env vars (non-secret) |
| `deploy/k8s/base/sealed-secret-apex.yaml` | Bitnami SealedSecret (all API keys) |
| `deploy/k8s/base/sealed-secrets-controller.yaml` | SealedSecrets controller deployment |
| `deploy/k8s/base/kafka-statefulset.yaml` | Kafka StatefulSet |
| `deploy/k8s/base/timescaledb-statefulset.yaml` | TimescaleDB StatefulSet (10Gi PVC) |
| `deploy/k8s/base/redis-deployment.yaml` | Redis deployment (AOF enabled) |
| `deploy/k8s/base/mlflow-deployment.yaml` | MLflow tracking server |
| `deploy/k8s/base/data-ingestion-deployment.yaml` | Data ingestion service |
| `deploy/k8s/base/feature-engineering-deployment.yaml` | Feature engineering service |
| `deploy/k8s/base/lean-alpha-deployment.yaml` | LEAN alpha service |
| `deploy/k8s/base/signal-engine-deployment.yaml` | Signal engine service |
| `deploy/k8s/base/risk-engine-deployment.yaml` | Risk engine service |
| `deploy/k8s/base/execution-deployment.yaml` | Execution agent service |
| `deploy/k8s/base/exit-monitor-deployment.yaml` | Exit monitor service |
| `deploy/k8s/base/kustomization.yaml` | Base kustomization |
| `deploy/k8s/overlays/dev/kustomization.yaml` | Dev overlay (reduced replicas, debug logs) |
| `deploy/k8s/overlays/prod/kustomization.yaml` | Prod overlay (HPA, resource limits) |

### infra/ — Infrastructure

| File | Purpose |
|------|---------|
| `infra/docker-compose.yml` | Full local dev stack (all services) |
| `infra/db/init.sql` | TimescaleDB schema (OHLCV, continuous aggregates, retention) |
| `infra/db/signal_attribution_migration.sql` | Signal attribution table migration |
| `infra/prometheus/alerts.yml` | Prometheus alerting rules (market-hours gated) |
| `infra/prometheus/model_alerts.yml` | Model drift alerting rules |

### configs/ — Runtime Configuration

| File | Purpose |
|------|---------|
| `configs/app.yaml` | Service URLs, Kafka topics, feature list |
| `configs/limits.yaml` | Trading limits (max position, daily loss, universe) |
| `configs/paper_trading.yaml` | Paper trading specific overrides |
| `configs/live_trading.yaml` | Live trading specific overrides |

### scripts/ — Operational Scripts

| File | Purpose |
|------|---------|
| `scripts/health_check.sh` | Curl all service health endpoints |
| `scripts/seal_secret.sh` | kubeseal wrapper to create SealedSecrets |
| `scripts/verify_first_trade.sh` | Pre-live smoke check (positions, kill switch, balance) |
| `scripts/go_live_validator.py` | 10-point go-live validation checklist |
| `scripts/paper_trading_monitor.py` | Daily stats: Sharpe, win rate, loss breaches |
| `scripts/retrain_scheduler.py` | Weekly model retrain trigger |
| `scripts/circuit_breaker.py` | Manual circuit breaker control |
| `scripts/signal_attribution_report.py` | CSV report: per-signal P&L attribution |
| `scripts/run_and_report.sh` | Run backtest + generate report |

### docs/

| File | Purpose |
|------|---------|
| `docs/GO_LIVE_RUNBOOK.md` | Step-by-step go-live procedure |
| `docs/PAPER_TRADING_RUNBOOK.md` | Paper trading setup & monitoring |
| `docs/README_report.md` | Backtest report format guide |
| `APEX_README.md` | Project overview (this project) |
| `README.md` | Workspace README |

### MyProject/ — QuantConnect LEAN Algorithm

| File | Purpose |
|------|---------|
| `MyProject/main.py` | APEX Ensemble Algorithm (LEAN Python) |
| `MyProject/signal_provider_api.py` | REST bridge: APEX signals → LEAN |
| `MyProject/apex_cli.py` | CLI for triggering backtests |
| `MyProject/backtest_reporter.py` | Parse + format backtest JSON |
| `MyProject/lean.json` | LEAN project config |
| `MyProject/requirements.txt` | LEAN project Python deps |

---

## 5. Microservices Detail

### 5.1 Data Ingestion (`services/data_ingestion/main.py`)
- Connects to Alpaca WebSocket for real-time bars and trades
- Publishes to Kafka topic `market.raw` (JSON: symbol, price, volume, timestamp)
- Persists to TimescaleDB via bulk INSERT with conflict handling
- Metrics: `KAFKA_MESSAGES_TOTAL`, `FEATURE_FRESHNESS`

### 5.2 Feature Engineering (`services/feature_engineering/main.py`)
- Consumes `market.raw`, computes: RSI(14), EMA(20), EMA(50), MACD(12,26,9), Volume z-score, VWAP
- Uses `FoldScaler` — fit only on in-sample data, serializes scaler params to JSON sidecar to prevent lookahead
- Publishes to `market.engineered`
- Metrics: `FEATURE_FRESHNESS`, `PIPELINE_STALE`

### 5.3 TFT Model Inference (`services/model_inference/main.py`)
- At startup: loads model from local `.pt` file or `mlflow:///model/<version>` URI
- Validates `run_id` against `MLFLOW_RUN_ID` env var — **refuses to start on mismatch**
- Consumes `market.engineered`, applies 30-second stale gate (fail-closed)
- Runs PyTorch inference, publishes prediction + confidence to `predictions.tft`
- Manual commit only after successful publish
- Metrics: `MODEL_INFERENCE_LATENCY`, `PIPELINE_STALE`, `STALE_MESSAGES_DROPPED`

### 5.4 LEAN Alpha Service (`services/lean_alpha/main.py`)
- Runs 6 alpha signals in parallel: RSI, EMA cross, MACD, Stochastic, SMA, Sentiment (from Twitter TFT)
- Each alpha independently consumes `market.engineered`
- Publishes individual signal scores to `signals.lean`

### 5.5 Signal Engine (`services/signal_engine/main.py`)
- Merges `predictions.tft` + `signals.lean` via weighted ensemble (`ensemble.py`)
- Applies regime and liquidity filters (`filters.py`)
- Runs Black-Litterman optimization (`portfolio.py`)
- Publishes target weights to `signals.approved`

### 5.6 Risk Engine (`services/risk_engine/engine.py`)
- Checks dual kill switch before processing any signal
- Checks daily loss limit vs `configs/limits.yaml`
- Checks per-symbol position size limits
- On PASS: publishes to `orders.pending`
- On BLOCK: sends to DLQ, increments `DAILY_LOSS_PCT`, fires Prometheus alert

### 5.7 Execution Agent (`services/execution/main.py`)
- Consumes `orders.pending`, submits market/limit orders via Alpaca REST
- `httpx.AsyncClient` with `Timeout(30.0)` on all calls
- Circuit breaker: 3 consecutive failures → 5-minute pause → auto-retry
- `PositionReconciler` runs every 60 seconds:
  - Fetches live positions from Alpaca
  - Compares against internal position dict
  - Tolerance: ±1 share or ±$50 value
  - On mismatch: increments `POSITION_MISMATCH`, halts trading, fires alert

### 5.8 Exit Monitor (`services/exit_monitor/main.py`)
- Monitors all open positions against stop-loss and take-profit levels
- Subscribes to `fill.events` to track entries
- Injects exit orders directly into `orders.pending`

---

## 6. Shared Library

### `shared/core/kafka_utils.py`
```python
# Key functions:
make_consumer(topics, group_id, **extra_config)
  # Enforces enable.auto.commit=False — pops it from extra_config if user tries to set True

make_producer(bootstrap_servers)

is_stale(payload, max_age_s=30, ts_key="signal_timestamp") -> bool
  # Returns True (STALE) if:
  #   - ts_key missing from payload
  #   - timestamp is None, empty, garbage, wrong type
  #   - age > max_age_s seconds
  # Always fails CLOSED (blocks trading when uncertain)

publish_and_commit(producer, topic, payload, consumer, partition, offset)
  # Enforces: produce → flush() → commit() ORDER (never commit before flush)

decode_message(msg) -> dict | None
  # JSON decode with DLQ fallback on error

consumer_iter(consumer)
  # Async generator, silently skips PARTITION_EOF
```

### `shared/core/metrics.py`
```python
# All Prometheus metrics (Counter, Gauge, Histogram):
PIPELINE_STALE          # Gauge: 1 = pipeline stalled
SIGNAL_SCORE            # Gauge: latest ensemble score per symbol
KILL_SWITCH_STATE       # Gauge: 1 = kill switch active
POSITION_MISMATCH       # Counter: position reconciliation failures
DAILY_LOSS_PCT          # Gauge: today's P&L as % of capital
ORDER_LATENCY           # Histogram: order round-trip (10 buckets, 0.1s→30s)
CIRCUIT_BREAKER_OPEN    # Gauge: 1 = circuit breaker tripped
ORDERS_TOTAL            # Counter: orders by symbol + side + status
KAFKA_MESSAGES_TOTAL    # Counter: messages by topic + direction
STALE_MESSAGES_DROPPED  # Counter: messages dropped by stale gate
MODEL_INFERENCE_LATENCY # Histogram: TFT inference time
FEATURE_FRESHNESS       # Gauge: seconds since last feature update

start_metrics_server()   # idempotent, reads METRICS_PORT (default 9100)
```

### `shared/core/trading_safety.py`
```python
@dataclass
class TradingLimits:
    max_position_pct: float      # max single position as % of capital
    daily_loss_limit_pct: float  # halt if daily loss exceeds this
    max_gross_exposure: float    # max sum(|weights|)
    kill_switch_active: bool

    def is_safe_to_trade(self, current_loss_pct, gross_exposure) -> bool
    def activate_kill_switch(self) -> None

# Dual-layer kill switch:
KILL_FLAG_PATH = "/tmp/apex_kill.flag"

is_file_kill_active() -> bool           # Layer 2: check file flag
set_file_kill() -> None                 # Write flag file
clear_file_kill() -> None               # Remove flag file

is_redis_kill_active(redis_client) -> bool
  # Returns True (HALTED) if:
  #   - apex:kill_switch key == "true"
  #   - Redis is unreachable (fail-closed)
  #   - redis_client is None

check_dual_kill_switch(redis_client) -> bool
  # Returns True (HALTED) if EITHER layer is active
  # OR if Redis is unreachable
```

---

## 7. Safety Architecture

```
                    DUAL-LAYER KILL SWITCH
                    
Layer 1: Redis Key
  Key:   apex:kill_switch
  Value: "true" = HALTED, "false" / missing = OK
  Fail:  If Redis is DOWN → assume HALTED (fail-closed)
  Set:   Via dashboard "Emergency Stop" button
         Via scripts/circuit_breaker.py
         
Layer 2: File Flag
  Path:  /tmp/apex_kill.flag
  Exist: file present = HALTED
  Fail:  IOError reading = assume HALTED (fail-closed)
  Set:   Automatically when PositionReconciler detects mismatch
         Automatically when daily loss limit breached
         Manually via CLI

OR Logic: HALT if Layer1 OR Layer2 active
```

```
            POSITION RECONCILIATION (every 60s)
            
Alpaca positions ◀──── PositionReconciler ────▶ Internal dict
                              │
                              ▼
                    Compare each symbol:
                    |alpaca_qty - internal_qty| > 1 share?
                    OR
                    |alpaca_value - internal_value| > $50?
                              │
                    YES ──────▼──────
                    │  POSITION_MISMATCH++  │
                    │  Set _halted = True   │
                    │  Set file kill flag   │
                    │  Fire Prometheus alert│
                    └───────────────────────┘
```

---

## 8. ML / Model Pipeline

### Architecture: Temporal Fusion Transformer (TFT)

```
Input features (market.engineered):
  - Price OHLCV (normalized by FoldScaler)
  - RSI(14), EMA(20/50), MACD histogram
  - Volume z-score, VWAP deviation
  - Twitter sentiment score (from twitter_tft pipeline)
  - Time features: day-of-week, hour-of-day

Model: PyTorch TFT (pytorch-forecasting)
  - Encoder: LSTM + Variable Selection Network
  - Attention: Multi-head self-attention
  - Decoder: Quantile regression (p10, p50, p90)
  - Output: Next-bar return prediction + confidence

Training: Walk-Forward Cross-Validation
  - Expanding window (not rolling)
  - Minimum 252 trading days in-sample
  - 63-day out-of-sample test per fold
  - FoldScaler: fit only on in-sample window → NO lookahead
  - Every fold logged to MLflow with:
      metrics: val_loss, val_mae, val_sharpe, val_hit_rate
      params: fold_id, train_start, train_end, test_start, test_end
      artifacts: model.pt, scaler.json

Model Validation at Deployment:
  - MLFLOW_RUN_ID env var set in K8s SealedSecret
  - model_inference/main.py reads run_id from loaded model
  - If run_id != MLFLOW_RUN_ID → RuntimeError → service exits
  - Prevents accidental deployment of wrong model version
```

### Model Monitoring (`services/model_monitor/main.py`)
- Runs every 15 minutes
- Computes PSI (Population Stability Index) on feature distributions
- Computes rolling accuracy vs. baseline
- If PSI > 0.2 → fires `model_alerts.yml` alert → triggers retrain
- Weekly retrain scheduled via `scripts/retrain_scheduler.py`

---

## 9. Twitter / TFT Sentiment Pipeline

### Overview
Collects Twitter/X posts mentioning tracked symbols, scores sentiment with FinBERT, feeds scores as features into TFT model.

### Pipeline:
```
Twitter/X API ──▶ collectors/scrape.py (async httpx)
                ──▶ collectors/transformer.py (clean, extract entities)
                ──▶ jobs/ingest.py (schedule: every 5 min)
                ──▶ storage/db.py (AsyncPG)
                ──▶ PostgreSQL: twitter_tweets (weekly partitioned)
                
Batch job (every 15 min):
  jobs/sentiment.py ──▶ FinBERT model
                    ──▶ Score: bullish/bearish/neutral + confidence
                    ──▶ UPDATE twitter_tweets SET sentiment_score
                    
Feature extraction (every 15 min):
  jobs/feature_extract.py ──▶ Aggregate sentiment per symbol per 15m window
                           ──▶ Publish to Kafka: market.sentiment
                           ──▶ Consumed by feature_engineering service
```

### Database Schema (PostgreSQL 16):
```sql
-- Weekly range-partitioned for performance
CREATE TABLE twitter_tweets (
    id              BIGSERIAL,
    tweet_id        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    author_id       TEXT,
    content         TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    sentiment_score FLOAT,
    confidence      FLOAT,
    followers_count INT,
    is_verified     BOOLEAN
) PARTITION BY RANGE (created_at);

-- Pre-created partitions: 2024-W01 through 2027-W52
-- Indexes on (symbol, created_at) for fast aggregation
```

---

## 10. Infrastructure & Kubernetes

### docker-compose (local dev): `infra/docker-compose.yml`
```
Services:
  kafka         → bitnami/kafka:3.7    port 9092
  zookeeper     → bitnami/zookeeper    port 2181
  timescaledb   → timescale/timescaledb:pg16  port 5432
  redis         → redis:7-alpine       port 6379  (AOF enabled)
  mlflow        → custom               port 5000
  prometheus    → prom/prometheus      port 9090
  grafana       → grafana/grafana      port 3001
  apex-dashboard → custom Next.js      port 3000
```

### Kubernetes (K8s): `deploy/k8s/`
```
All deployments include:
  - Resource limits: cpu/memory requests AND limits
  - Liveness probe: HTTP GET /health every 15s
  - Readiness probe: HTTP GET /ready every 10s
  - Graceful shutdown: terminationGracePeriodSeconds: 30
  - All secrets via Bitnami SealedSecrets (never plain K8s secrets)

Overlays:
  dev/   → 1 replica per service, DEBUG log level
  prod/  → 2+ replicas, HPA (CPU > 70%), INFO log level
```

### Secrets Management:
```bash
# To seal a new secret:
bash scripts/seal_secret.sh ALPACA_API_KEY "your-key-here"
# → appends to deploy/k8s/base/sealed-secret-apex.yaml

# Secrets stored as SealedSecrets (encrypted with cluster cert):
ALPACA_API_KEY
ALPACA_SECRET_KEY
REDIS_PASSWORD
TIMESCALEDB_PASSWORD
MLFLOW_TRACKING_URI
TWITTER_BEARER_TOKEN
```

### CI/CD: `.github/workflows/ci.yml`
```
On push/PR to main:
  1. Lint (ruff)
  2. Type check (mypy)
  3. Unit tests (pytest, 175 tests)
  4. Build Docker images
  5. Push to registry (prod only, on main branch)
  6. Apply kustomize (prod only, on main branch)
```

### Prometheus / Grafana Alerts: `infra/prometheus/alerts.yml`
```yaml
Alerts (all gated to market hours UTC 14:00–21:00, Mon–Fri):
  - KillSwitchActive           → page on-call immediately
  - DailyLossLimitBreached     → page on-call immediately
  - PositionMismatch           → page on-call immediately
  - PipelineStale              → warn after 2 min
  - CircuitBreakerOpen         → warn after 5 min
  - ModelInferenceP99High      → warn if p99 > 500ms
  - StaleMessagesDroppedHigh   → warn if > 100/min
```

---

## 11. Next.js 14 Dashboard (BFF)

### BFF Pattern (Backend-For-Frontend)
```
Browser ──HTTP──▶ Next.js API Routes (server-side)
                          │
                          ├──▶ TimescaleDB (signals, P&L, positions)
                          ├──▶ Alpaca REST API (live positions, orders)
                          ├──▶ MLflow REST API (model metadata)
                          ├──▶ Redis (kill switch state)
                          └──▶ Service health endpoints
                          
RULE: NO secret or API key ever reaches the browser bundle
All DB/API calls happen server-side in /app/api/* route handlers
```

### Real-Time Updates (SSE)
- `PnLTicker.tsx` → subscribes to `/api/pnl` SSE stream → live P&L
- `RecentSignals.tsx` → polls `/api/signals` every 5s
- `ServiceStatusGrid.tsx` → polls `/api/health` every 10s

### Kill Switch UI
```
KillSwitch.tsx → red btn "EMERGENCY STOP" → POST /api/kill-switch/enable
                                           → sets Redis apex:kill_switch = "true"
               → green btn "RESUME"        → POST /api/kill-switch/disable
                                           → clears Redis key + removes file flag
```

---

## 12. Test Suite

### Run All Tests
```bash
cd /home/kironix/workspace/QuantConnect.VS
source lean_venv/bin/activate
pytest tests/ -v
# Expected: 175 passed, 4 warnings in ~1.75s
# The 4 warnings are mlflow FutureWarning (non-blocking)
```

### Test File Details

#### `tests/test_kill_switch.py` (16 tests)
```
test_redis_down_returns_halted          Redis ConnectionError → HALTED
test_redis_key_true_returns_halted      apex:kill_switch="true" → HALTED
test_redis_key_false_returns_ok         apex:kill_switch="false" → OK
test_no_redis_client_returns_halted     None client → HALTED
test_file_kill_returns_halted           /tmp/apex_kill.flag present → HALTED
test_no_file_kill_returns_ok            no flag file → OK
test_dual_switch_or_logic               Redis OK + file HALTED → HALTED
test_dual_switch_both_ok                Redis OK + no file → OK
test_set_clear_file_kill               set_file_kill() / clear_file_kill()
test_trading_limits_safe               TradingLimits.is_safe_to_trade()
test_trading_limits_loss_exceeded      daily loss > limit → not safe
test_trading_limits_exposure_exceeded  gross exposure > max → not safe
test_activate_kill_switch              activate_kill_switch() sets flag
... (4 more edge cases)
```

#### `tests/test_position_reconciliation.py` (11 tests)
```
test_exact_match                        Zero mismatch, no halt
test_within_share_tolerance             0.5 share diff → OK
test_exceeds_share_tolerance            1.5 share diff → MISMATCH++, halt
test_within_value_tolerance             $30 diff → OK
test_exceeds_value_tolerance            $75 diff → MISMATCH++, halt
test_ghost_position_in_alpaca           Symbol in Alpaca, not internal → MISMATCH
test_ghost_position_in_internal         Symbol internal, not Alpaca → MISMATCH
test_multiple_symbols_all_match         5 symbols, all match → OK
test_multiple_symbols_one_mismatch      5 symbols, one bad → MISMATCH
test_alpaca_fetch_failure               httpx raises → logged, no crash
test_reconciler_update_internal         update_internal() updates dict
```

#### `tests/test_bl_weights.py` (22 tests)
```
test_weights_clipped_to_15pct           |w| ≤ 0.15 for all assets
test_gross_exposure_leq_1               sum(|w|) ≤ 1.0
test_strong_buy_signal_positive_weight  +1.0 signal → positive weight
test_strong_sell_signal_negative_weight -1.0 signal → negative weight
test_zero_signals_near_zero_weights     all-zero signals → ~zero weights
test_high_confidence_larger_weight      conf=0.9 > conf=0.1 magnitude
test_fuzz_50_random_trials              50 random inputs, all pass constraints
test_single_asset                       1 asset edge case
test_all_positive_signals               long-only scenario
test_all_negative_signals               short-only scenario
... (12 more)
```

#### `tests/test_signal_staleness.py` (25 tests)
```
test_fresh_float_epoch                  now() epoch → NOT stale
test_fresh_iso_string                   now() ISO-8601 → NOT stale
test_stale_float_epoch                  (now - 60s) epoch → STALE
test_stale_iso_string                   (now - 60s) ISO-8601 → STALE
test_missing_timestamp_key              payload without ts_key → STALE
test_none_timestamp                     ts=None → STALE
test_empty_string_timestamp             ts="" → STALE
test_garbage_string_timestamp           ts="foobar" → STALE
test_dict_timestamp                     ts={} → STALE
test_list_timestamp                     ts=[] → STALE
test_custom_max_age_5s                  5s window, 3s old → NOT stale
test_custom_ts_key                      custom key name works
test_exactly_at_boundary               age == max_age_s → STALE (boundary)
test_model_inference_uses_is_stale      integration: model_inference source check
... (11 more edge cases)
```

---

## 13. Configuration Files

### `configs/limits.yaml`
```yaml
trading:
  max_position_pct: 0.15        # max 15% in any single position
  daily_loss_limit_pct: 0.02    # halt if down 2% in one day
  max_gross_exposure: 1.0       # fully invested max
  universe:                     # tradeable symbols
    - AAPL
    - MSFT
    - GOOGL
    - AMZN
    - NVDA
    - META
    - TSLA
    - SPY
```

### `configs/app.yaml`
```yaml
kafka:
  bootstrap_servers: "kafka:9092"
  topics:
    raw:         market.raw
    engineered:  market.engineered
    predictions: predictions.tft
    signals_lean: signals.lean
    approved:    signals.approved
    orders:      orders.pending
    fills:       fill.events

services:
  data_ingestion:    http://data-ingestion:8001
  feature_eng:       http://feature-engineering:8002
  model_inference:   http://model-inference:8003
  lean_alpha:        http://lean-alpha:8004
  signal_engine:     http://signal-engine:8005
  risk_engine:       http://risk-engine:8006
  execution:         http://execution:8007
  exit_monitor:      http://exit-monitor:8008
```

---

## 14. Operational Scripts

### Health Check
```bash
bash scripts/health_check.sh
# Curls /health on all 8 service ports
# Exits 1 if any service unhealthy
```

### Paper Trading Monitor
```bash
python scripts/paper_trading_monitor.py
# Prints daily stats:
#   Win rate: 54.2%
#   Sharpe:   1.35
#   Max DD:   -1.2%
#   Loss breaches: 0
# Target: win_rate >= 52%, sharpe >= 1.2, breaches == 0
```

### Go-Live Validator
```bash
python scripts/go_live_validator.py
# 10-point checklist:
# [✓] Kill switch responsive
# [✓] Position reconciler active
# [✓] Daily loss limit configured
# [✓] MLflow run_id matches deployment
# [✓] All services healthy
# [✓] Paper trading stats pass targets
# [✓] Alpaca account has sufficient capital
# [✓] SealedSecrets all present
# [✓] TimescaleDB retention policies active
# [✓] Grafana alerts firing correctly (test mode)
```

### Verify First Trade
```bash
bash scripts/verify_first_trade.sh
# Checks Alpaca paper positions are zero before start
# Checks kill switch is OFF
# Checks account balance > $10,000
```

---

## 15. Bug Fixes & Critical Changes

| ID | Component | Issue | Fix |
|----|-----------|-------|-----|
| CF-1 | Risk Engine | Kill switch not fail-closed | `is_redis_kill_active()` returns `True` on any Redis exception |
| CF-2 | Kafka | `enable.auto.commit=True` could leak | `make_consumer()` pops any `enable.auto.commit` from extra_config |
| CF-3 | Kafka | Commit before flush race condition | `publish_and_commit()` enforces `produce → flush() → commit()` |
| CF-4 | Feature Eng | FoldScaler fit on full dataset | FoldScaler now fit only on in-sample window, sidecar JSON |
| CF-5 | Model Inference | Wrong model could be loaded | `run_id` validation against `MLFLOW_RUN_ID` env var at startup |
| CF-6 | Signal Engine | Missing stale signal gate | 30-second gate in `kafka_utils.is_stale()`, fail-closed |
| CF-7 | Execution | No position reconciliation | `PositionReconciler` class, runs every 60s |
| CF-8 | Execution | httpx no timeout → hung orders | `httpx.AsyncClient(timeout=Timeout(30.0))` on all calls |
| CF-9 | Risk Engine | Single kill switch (Redis only) | Added file-based kill switch Layer 2 (`/tmp/apex_kill.flag`) |
| CF-10 | BL Portfolio | Weights could exceed 15% | `_clip_and_normalise()`: `np.clip(w, -0.15, 0.15)` then scale |
| TFT-01 | Model Inference | No stale gate on inference | Added `is_stale()` check before `predict()` call |
| TFT-02 | Walk-forward | MLflow not logging per fold | All metrics/params/artifacts now logged per fold |
| TFT-03 | Model Monitor | No PSI check | PSI computed on feature distributions, alert if > 0.2 |
| TFT-04 | Feature Eng | Sentiment feature missing | `market.sentiment` Kafka topic wired into feature pipeline |
| TFT-05 | Twitter TFT | No weekly partitioning | `twitter_tweets` table partitioned by `RANGE (created_at)` |
| TFT-06 | Twitter TFT | FinBERT no confidence | `confidence` column added, stored with each sentiment score |
| TFT-07 | Twitter TFT | Author metrics index missing | `002_fix_author_metrics.sql` migration adds composite index |
| Bug-A | Tests | `asyncio.get_event_loop()` Python 3.12 | Replaced with `asyncio.run()` in all test files |
| Bug-B | Tests | `mlflow` not in venv | Installed in `QuantConnect.AG/lean_venv` (active env path) |
| HI-4 | K8s | Plain K8s Secrets | All secrets migrated to Bitnami SealedSecrets |
| HI-6 | K8s | No resource limits | CPU/memory limits added to all deployments |
| HI-8 | Prometheus | Alerts fire on weekends | All alerts gated UTC 14:00–21:00 Mon–Fri |
| MD-1 | Dashboard | API keys in browser | BFF pattern: all secrets server-side in Next.js route handlers |
| MD-2 | Dashboard | No real-time update | SSE (`/api/pnl`) + polling (`/api/signals`, `/api/health`) |

---

## 16. Phase-by-Phase Build Log

### Phase 01 — QuantConnect LEAN Setup
- Installed QuantConnect LEAN engine in `Lean/` directory
- Created `MyProject/` with LEAN Python algorithm base
- Configured `MyProject/lean.json` with local data paths
- First backtest: `SimplePythonTest` — confirmed LEAN engine works

### Phase 02 — APEX Ensemble Algorithm (LEAN)
- Built `MyProject/main.py`: multi-factor ensemble using LEAN framework Alpha/Portfolio/Risk/Execution models
- Alpha signals: Momentum, Mean-Reversion, ML Score (via external API)
- Backtests: `APEXEnsembleAlgorithm_20260227_164932.txt` through `_20260228_043940.txt`
- `MyProject/signal_provider_api.py`: REST bridge to pull live APEX signals into LEAN

### Phase 03 — Microservices Architecture
- Created 8 core microservice directories: data_ingestion, feature_engineering, model_inference, lean_alpha, signal_engine, risk_engine, execution, exit_monitor
- Created `shared/core/` library
- Created `infra/docker-compose.yml` with full stack
- Created `deploy/k8s/` with base + dev/prod overlays

### Phase 04 — Twitter / TFT Sentiment Pipeline
- Created `twitter_tft/` package (16 files)
- Async httpx scraper with rate limiting
- FinBERT sentiment scoring job
- PostgreSQL 16 weekly-partitioned storage
- Feature extraction → Kafka pipeline

### Phase 05 — Model Training & MLflow
- Created `services/model_training/walk_forward.py`: expanding-window walk-forward CV
- Per-fold MLflow logging (metrics, params, artifacts)
- `services/model_training/dataset.py`: TFT dataset builder with FoldScaler
- `services/model_monitor/main.py`: PSI drift detection

### Phase 06 — Signal Engine & Portfolio Construction
- Created `services/signal_engine/ensemble.py`: weighted signal combination
- Created `services/signal_engine/filters.py`: regime + liquidity filters
- Created `services/signal_engine/portfolio.py`: closed-form Black-Litterman
  - P = identity, Q = signal scores, Ω = diag(uncertainty)
  - τ = 0.05 (prior weight)
  - Post-processor: clip to ±15%, scale if gross > 1.0

### Phase 07 — Safety Systems (Critical Fixes)
- Created `shared/core/kafka_utils.py`: enforced commit ordering, stale gate, DLQ
- Created `shared/core/metrics.py`: all Prometheus metrics
- Extended `shared/core/trading_safety.py`: dual-layer kill switch
- Extended `services/execution/main.py`: PositionReconciler class
- Applied all CF-1 through CF-10 fixes

### Phase 07b — Test Suite
- Created `tests/test_kill_switch.py` (16 tests)
- Created `tests/test_position_reconciliation.py` (11 tests)
- Created `tests/test_bl_weights.py` (22 tests)
- Created `tests/test_signal_staleness.py` (25 tests)
- Fixed Python 3.12 asyncio event loop issue (Bug-A)
- Fixed missing mlflow dependency (Bug-B)
- **Final result: 175 tests, 0 failures**

### Phase 08 — Paper Trading (Active)
- Deployed full stack to paper trading environment
- `configs/paper_trading.yaml` with reduced position limits
- Monitoring via `scripts/paper_trading_monitor.py`
- 30-day validation window in progress
- Target: win ≥ 52%, Sharpe ≥ 1.2, zero breach

### Phase 09 — Dashboard & Observability
- Built Next.js 14 BFF dashboard (`apex-dashboard/`)
- 8 API routes (all server-side, BFF pattern)
- 7 React components (SSE + polling)
- Kill switch UI with emergency stop
- Grafana alerts via `infra/prometheus/alerts.yml`

---

## 17. Go-Live Checklist

```
□ Paper trading 30-day window complete
□ Win rate ≥ 52% confirmed
□ Sharpe ratio ≥ 1.2 confirmed
□ Zero daily loss limit breaches
□ go_live_validator.py all 10 checks pass
□ verify_first_trade.sh passes
□ Kill switch test: enable → all orders blocked, disable → resumes
□ Position reconciler test: inject 1-share mismatch → verify halt
□ Model run_id validated in prod SealedSecret
□ SealedSecrets all present in cluster
□ Grafana alerts confirm firing (test mode)
□ On-call rotation setup
□ switch configs/paper_trading.yaml → configs/live_trading.yaml
□ kubectl apply -k deploy/k8s/overlays/prod/
```

---

*Document auto-generated from conversation history and file inventory.*
*For questions: see docs/GO_LIVE_RUNBOOK.md and docs/PAPER_TRADING_RUNBOOK.md*
