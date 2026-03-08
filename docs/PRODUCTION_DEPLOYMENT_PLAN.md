# APEX Production Deployment Plan

**Repository:** `https://github.com/Kibrom2324/QuantConnect.VS.git`
**Branch:** `main` | **Tag:** `v0.5.0` | **Commit:** `d7dec7f`
**Prepared:** 2026-03-08 | **Classification:** Internal / Engineering

---

## Section 1 — CURRENT STATUS

### 1.1 What Is Already Implemented and Production-Relevant

| Component | Evidence | Label |
|-----------|----------|-------|
| **Isotonic calibration cutover** | `shared/core/calibrator.py` loads from Redis key `apex:calibration:curve`, `services/signal_engine/main.py` uses `active_prob = iso_prob` when `ENABLE_ISOTONIC_CALIBRATION=true` | VERIFIED FROM CODE |
| **Platt scaler fallback** | `configs/models/platt_scaler.json` (coef=1.0, intercept=0.0 — identity transform), signal-engine falls back when isotonic disabled | VERIFIED FROM CODE |
| **Kill switch (dual-layer)** | Redis key `apex:kill_switch` + env var fallback, checked every 5s in `services/risk_engine/main.py`, fail-closed design in `scripts/circuit_breaker.py` | VERIFIED FROM CODE |
| **Graceful shutdown** | `services/graceful_shutdown.py` — LIFO handler execution, 30s per-handler timeout, SIGTERM/SIGINT trapped | VERIFIED FROM CODE |
| **Core pipeline Kafka wiring** | `apex.signals.raw` → `apex.signals.scored` → `apex.risk.approved` → `apex.orders.results`, consumer groups: `apex-signal-engine-v1`, `apex-risk-engine-v1`, `apex-execution-v1` | VERIFIED FROM CODE |
| **Decision records** | `ENABLE_DECISION_RECORDS=true` in execution-engine, writes to `decision_records` table (8 rows exist) | VERIFIED FROM CODE |
| **Prediction lineage** | `ENABLE_PREDICTION_LINEAGE=true` in signal-engine, scored messages include `ensemble_method`, `model_weights`, `platt_prob`, `isotonic_prob` | VERIFIED FROM CODE |
| **TimescaleDB schema** | 11 tables across 4 migration files, hypertables with compression and retention policies | VERIFIED FROM CODE |
| **Prometheus alert rules** | `infra/prometheus/alerts.yml` (9 pipeline alerts), `model_alerts.yml` (4 model alerts), `feedback_alerts.yml` (8 calibration alerts) = 21 total | VERIFIED FROM CODE |
| **Grafana dashboards** | `infra/grafana/dashboards/apex-trading.json` (portfolio metrics), `tft_service.json` (TFT health) | VERIFIED FROM CODE |
| **K8s manifests** | `deploy/k8s/base/` — 17 manifests covering all infra + pipeline services, overlays for dev and prod | VERIFIED FROM CODE |
| **Data pipeline** | `data_ingestion` (591K bars in DB), `feature_engineering` (569K feature rows), both healthy 24h+ | VERIFIED FROM CODE |
| **Test suite** | 21 test files, 100+ test functions, all mock Kafka/Redis, cover kill switch, calibration, ensemble, staleness, position sizing | VERIFIED FROM CODE |
| **TFT models** | `models/TFT_v1/model.pt` and `models/TFT_v3/model.pt` exist on disk | VERIFIED FROM CODE |

### 1.2 What Is Only Locally Validated (Not Production-Proven)

| Component | Current State | Gap |
|-----------|---------------|-----|
| **Isotonic cutover** | Verified locally via Docker Compose: scored messages show `isotonic_prob ≠ platt_prob`, rollback proven | Never tested on k8s or under production load |
| **Consumer lag** | Currently 0 on all groups | Only proven at low throughput (~33K lifetime messages on `apex.signals.raw`); no stress test |
| **Health endpoints** | All 3 core services return HTTP 200 | No load-balanced health check behind ingress/ALB |
| **Calibrator fit** | `calibration_snapshots` has 5 rows, calibrator is 664 bytes in Redis | Fitted from synthetic/limited data — not from real paper trading positions |
| **DB migrations** | All 4 SQL files applied, 11 tables present | No idempotency guards (`CREATE TABLE IF NOT EXISTS` varies by file) |
| **Paper trading flow** | `TRADING_ENABLED=false`, no orders ever submitted to Alpaca | Execution path is completely untested end-to-end |
| **Docker images** | Built locally by `docker compose build` | Not pushed to any container registry; k8s cannot pull them |

### 1.3 What Is Still Missing Before Production

| Gap | Severity | Details |
|-----|----------|---------|
| **K8s ConfigMap missing feature flags** | CRITICAL | `deploy/k8s/base/configmap.yaml` does not include `ENABLE_ISOTONIC_CALIBRATION`, `ENABLE_PREDICTION_LINEAGE`, `ENABLE_DECISION_RECORDS`, `TRADING_ENABLED`. All default to `false` on k8s deploy. | 
| **K8s SealedSecrets have PLACEHOLDER values** | CRITICAL | `deploy/k8s/base/sealed-secret-apex.yaml` — all 4 secrets (`apex-alpaca-secret`, `apex-db-secret`, `apex-api-secret`, `apex-redis-secret`) contain literal `PLACEHOLDER` | 
| **Topic mismatch: execution default** | HIGH | `services/execution/main.py` defaults to `apex.signals.approved` but risk-engine writes to `apex.risk.approved`. Docker-compose overrides this correctly (`EXECUTION_SIGNAL_TOPIC: apex.risk.approved`), but k8s configmap does not set it — it sets `EXECUTION_SIGNAL_TOPIC: apex.risk.approved` only in base configmap as just `EXECUTION_SIGNAL_TOPIC`. Must verify. | 
| **5 services have no Dockerfile** | HIGH | `signal_generator`, `exit_monitor`, `execution_process`, `llm_agent`, `attribution` — cannot be containerized. Of these, `signal_generator` is the *only raw signal producer* for the pipeline. | 
| **5 services not in docker-compose** | HIGH | Same 5 services above: `signal_generator`, `exit_monitor`, `execution_process`, `llm_agent`, `attribution` — zero mentions in `infra/docker-compose.yml` | 
| **No scheduled `daily_feedback.py`** | HIGH | Neither CronJob in k8s nor crontab entry exists. The calibrator will never refit autonomously. |
| **No scheduled `circuit_breaker.py`** | HIGH | No systemd unit, no docker-compose service, no CronJob. Drawdown-triggered kill switch is not automated. |
| **Signal-engine Prometheus metrics empty** | MEDIUM | `/metrics` endpoint on signal-engine returns empty body. No `prometheus_client` exporter wired. Grafana will not receive `apex_calibration_brier`, `apex_last_signal_timestamp`, etc. |
| **`signals` table has 0 rows** | MEDIUM | Feature-engineering and data-ingestion are working (569K+ feature rows), but no signals have been persisted to the `signals` table — only to Kafka. |
| **`orders` and `positions` tables have 0 rows** | EXPECTED | No paper or live orders have ever been submitted. |
| **Dashboard not deployed** | LOW (not blocking) | `apex-dashboard/` exists but is not in docker-compose. Runs standalone on port 3001. |
| **No container registry** | MEDIUM | K8s manifests reference `apex/signal-engine:latest` etc., but no registry has been configured. |

---

## Section 2 — EXISTING SERVICES AND RESPONSIBILITIES

### 2.1 Core Pipeline Services

| # | Service Path | Role | Input | Output | Dependencies | Dockerfile | In docker-compose | Required for First Cut |
|---|-------------|------|-------|--------|--------------|-----------|-------------------|----------------------|
| 1 | `services/data_ingestion/` | Ingests OHLCV bars from Alpaca WebSocket, writes to TimescaleDB, publishes Redis pub/sub | Alpaca WS `wss://stream.data.alpaca.markets/v2/iex` | DB: `ohlcv_bars`, Redis pub/sub: `apex:bars:{SYMBOL}` | TimescaleDB, Redis, Alpaca creds | ✅ | ✅ (`apex-data-ingestion`) | **YES** |
| 2 | `services/feature_engineering/` | Computes 21 technical indicators per bar, writes to `features` table | Redis sub: `apex:bars:*` (triggered by data_ingestion) | DB: `features` | TimescaleDB, Redis | ✅ | ✅ (`apex-feature-engineering`) | **YES** |
| 3 | `services/signal_generator/` | Reads latest features, scores factor signals, produces raw signals to Kafka | DB: `features` | Kafka: `apex.signals.raw` | TimescaleDB, Kafka | ❌ | ❌ | **YES** — this is the only service producing `apex.signals.raw` in the normal flow |
| 4 | `services/signal_engine/` | Consumes raw signals, runs ensemble scoring (TFT/XGB/LSTM weights), applies isotonic calibration, produces scored signals | Kafka: `apex.signals.raw`, Redis: `apex:llm:sentiment:*`, `apex:calibration:curve` | Kafka: `apex.signals.scored` | Redis, Kafka, MLflow | ✅ | ✅ (`infra-signal-engine-1`) | **YES** |
| 5 | `services/risk_engine/` | Applies position limits, daily loss limits, drawdown limits, kill switch. Approves or rejects scored signals | Kafka: `apex.signals.scored`, Redis: `apex:kill_switch`, `apex:portfolio:state` | Kafka: `apex.risk.approved` | Redis, Kafka | ✅ | ✅ (`infra-risk-engine-1`) | **YES** |
| 6 | `services/execution/` | Submits approved signals as orders to Alpaca, tracks fills via WebSocket, writes decision records | Kafka: `apex.risk.approved` (overridden from default `apex.signals.approved`), Alpaca HTTP+WS | Kafka: `apex.orders.results`, DB: `decision_records` | Kafka, Alpaca API, TimescaleDB | ✅ | ✅ (`infra-execution-engine-1`) | **YES** |

### 2.2 Model Services

| # | Service Path | Role | Input | Output | Dependencies | Dockerfile | In docker-compose | Required for First Cut |
|---|-------------|------|-------|--------|--------------|-----------|-------------------|----------------------|
| 7 | `services/tft_service/` | FastAPI wrapper for TFT model inference. `POST /predict`, model reload endpoint | HTTP requests, Redis: `apex:models:{id}` | Redis: `apex:signals:{SYMBOL}`, HTTP response | Redis, MLflow (artifact loading) | ✅ | ✅ (`tft-service`, port 8009) | NO — signal-engine has built-in ensemble scoring |
| 8 | `services/timesfm_service/` | FastAPI wrapper for Google TimesFM foundation model | HTTP requests | Redis: `apex:predictions:timesfm:{SYMBOL}`, HTTP response | Redis | ✅ | ✅ (`timesfm-service`, port 8010) | NO |
| 9 | `services/model_inference/` | Kafka-based inference: consumes engineered features, produces TFT predictions | Kafka: `market.engineered` | Kafka: `predictions.tft` | Kafka, MLflow | ✅ | ❌ | NO |
| 10 | `services/model_training/` | Batch training scripts: `train_tft.py`, `train_lstm.py`, `train_xgb.py`, `train_ensemble.py`, `walk_forward.py` | DB: `features` | Model artifacts (`.pt`, `.pkl`, `.joblib`), MLflow metrics | TimescaleDB, MLflow, Redis | ❌ | ❌ | NO — existing models are sufficient for paper trading |
| 11 | `services/model_manager/` | Library module: `ModelRegistry` class stores model metadata in Redis under `apex:models:*` | N/A (imported) | Redis: `apex:models:*` | Redis | ❌ | ❌ | NO — used as library by other services |
| 12 | `services/model_monitor/` | Compares live Sharpe vs backtest Sharpe, writes drift metrics to Redis | Alpaca API, MLflow: `apex-walk-forward` experiment | Redis: `apex:model:live_sharpe_14d` | Alpaca, MLflow, Redis | ✅ | ✅ (`infra-model-monitor-1`, port 8020) | NO — useful but not blocking |

### 2.3 Supporting Services

| # | Service Path | Role | Input | Output | Dependencies | Dockerfile | In docker-compose | Required for First Cut |
|---|-------------|------|-------|--------|--------------|-----------|-------------------|----------------------|
| 13 | `services/exit_monitor/` | Stop-loss and take-profit enforcer. Watches live bars, sends exit orders | Kafka: `market.raw`, Redis: `apex:positions` | Kafka: `apex.risk.approved` (exit orders) | Kafka, Redis, `configs/app.yaml` | ❌ | ❌ | **HIGHLY RECOMMENDED** for live trading |
| 14 | `services/attribution/` | Post-trade signal attribution: joins scored signals with order results | Kafka: `apex.signals.scored`, `apex.orders.results` | DB: `signal_attribution` | Kafka, TimescaleDB | ❌ | ❌ | NO |
| 15 | `services/llm_agent/` | LLM-powered sentiment analysis via Ollama. Writes sentiment to Redis (TTL 600s) for signal-engine ensemble | Ollama HTTP API | Kafka: `apex.signals.sentiment`, Redis: `apex:llm:sentiment:{SYMBOL}` | Ollama, Kafka, Redis | ❌ | ❌ | NO — ensemble works without it (LLM weight treated as 0) |
| 16 | `services/signal_provider/` | FastAPI read-only API for dashboard: exposes signals, status, kill switch state | Redis: `apex:signals:*`, `apex:kill_switch` | HTTP responses | Redis | ✅ | ✅ (`signal-provider-svc`, port 8007) | NO (dashboard convenience) |

### 2.4 Experimental / Phase-6 Services

| # | Service Path | Role | Notes |
|---|-------------|------|-------|
| 17 | `services/execution_process/` | Alternative execution path using `PositionSizer` + `CostEstimator`. Reads `signals.scored`, writes `fills.realized` | Different topic names from main pipeline. No Dockerfile. Experimental. |
| 18 | `services/signal_process/` | Alternative signal scoring with `AdaptiveCombiner`. Reads `market.raw`, writes `signals.scored` | Different topic names from main pipeline. No Dockerfile. Experimental. |
| 19 | `services/lean_alpha/` | LEAN QuantConnect integration: listens for triggers, runs LEAN backtest, produces raw signals | Kafka: `apex.lean.triggers` → `apex.signals.raw`. No Dockerfile. |

### 2.5 Social Pipeline (twitter_tft/)

| Service | Docker Compose | Role |
|---------|---------------|------|
| `social-ingest` | ✅ | Scrapes StockTwits/Reddit, stores raw posts |
| `social-sentiment` | ✅ | FinBERT sentiment scoring |
| `social-features` | ✅ | Feature extraction from scored posts |
| `social-kafka-publish` | ✅ | Publishes sentiment features to Kafka |

These are all deployed but are **enhancement-only** — the core pipeline does not depend on them.

### 2.6 Critical Observation: The signal_generator Problem

`services/signal_generator/` is the **only service** that reads features from TimescaleDB and produces `apex.signals.raw` in the normal pipeline flow. Without it:
- signal-engine has no input
- The entire pipeline stalls

**Current status**: `signal_generator` has NO Dockerfile, is NOT in docker-compose, and is NOT in any k8s manifest. It can only be run manually:
```bash
python -m services.signal_generator.main
```

This is the single biggest deployment gap. Either:
1. Add a Dockerfile and docker-compose entry for `signal_generator`, OR
2. Use `services/lean_alpha/` as the signal source (requires LEAN triggers), OR
3. Run it outside Docker as a host process (fragile)

---

## Section 3 — WHAT NEEDS TRAINING VS WHAT DOES NOT

### 3.1 Model Artifacts Currently in the Repository

| Artifact | Path | Format | Status | Label |
|----------|------|--------|--------|-------|
| TFT v1 | `models/TFT_v1/model.pt` | PyTorch | Exists on disk, committed to git | VERIFIED FROM CODE |
| TFT v3 | `models/TFT_v3/model.pt` | PyTorch | Exists on disk, committed to git | VERIFIED FROM CODE |
| Platt scaler | `configs/models/platt_scaler.json` | JSON (`coef: 1.0, intercept: 0.0`) | Identity transform — effectively a no-op | VERIFIED FROM CODE |
| Isotonic calibrator | Redis key `apex:calibration:curve` | Pickle (664 bytes) | In Redis, loaded by signal-engine at startup | VERIFIED FROM CODE |
| Indicator composite | `models/indicator_composite.py` | Python class (LightGBM-based) | Code exists but **no trained model file** (`.pkl`) found on disk | INFERRED — needs training |
| XGBoost model | None on disk | `.pkl` | Training outputs to `/tmp/apex_models/` — ephemeral | MISSING |
| LSTM model | None on disk | `.pt` | No saved artifact found outside models/TFT_* | MISSING |
| Ensemble meta-learner | None on disk | `.joblib` | `meta_lr.joblib` + `meta_scaler.joblib` not found | MISSING |

### 3.2 Does the Isotonic Calibrator Need Refit Now?

**No, not immediately.** Here's why:

- The calibrator is already loaded in Redis and functioning (signal-engine logs `isotonic_calibrator_loaded_from_redis`)
- It was fitted using `scripts/fit_isotonic_from_snapshots.py` from 5 rows of `calibration_snapshots` data
- For paper trading, this is sufficient — the calibrator maps raw probabilities to calibrated probabilities, and even a rough calibration is better than no calibration
- **But**: Once paper trading generates real closed positions (≥50 recommended), `scripts/daily_feedback.py` should be run to refit from real outcome data. The current calibrator is fitted from synthetic/limited data.

**Refit path:**
```bash
# After enough paper trading data exists:
.venv/bin/python scripts/daily_feedback.py --redis-port 16379
# This labels closed positions, refits calibrator, keeps old if new Brier is worse
```

### 3.3 What Must Be Loaded Into Redis Before Startup

| Redis Key | Contents | Must Exist | How to Load |
|-----------|----------|-----------|-------------|
| `apex:calibration:curve` | Pickled `IsotonicRegression` | YES if `ENABLE_ISOTONIC_CALIBRATION=true` | `python scripts/fit_isotonic_from_snapshots.py --redis-port 16379` |
| `apex:kill_switch` | `"true"` / `"false"` / nil | NO — nil defaults to OFF | Set manually if needed |
| `apex:portfolio:state` | Position count JSON | NO — risk-engine initializes from Alpaca | Auto-populated |
| `apex:llm:sentiment:*` | LLM sentiment scores | NO — optional ensemble input, TTL 600s | Populated by `llm_agent` if running |
| `apex:models:*` | Model metadata | NO for first cut — tft_service uses local fallback | Populated by model_manager/training |

**Bottom line**: The only required pre-load is `apex:calibration:curve` — and only if isotonic calibration is enabled.

### 3.4 What Belongs Where

| Data | Storage | Rationale |
|------|---------|-----------|
| OHLCV bars, features, signals | TimescaleDB | Time-series, queryable, retention policies |
| Decision records, trade feedback, signal attribution | TimescaleDB | Audit trail, queryable history |
| Calibration snapshots (histogram bins) | TimescaleDB | Source data for calibrator refit |
| Isotonic calibrator (fitted model) | Redis | Fast loading at startup, <1KB, no persistence needed beyond Redis AOF |
| Kill switch state | Redis | Low-latency read (every 5s), perdurable via AOF |
| Model artifacts (.pt, .pkl, .joblib) | Disk / MLflow artifact store | Large files, versioned, not suitable for Redis |
| Platt scaler coefficients | JSON file on disk | 2 numbers, loaded once at startup |
| Model metadata (registry state) | Redis | Fast lookup by model_manager, model_monitor |
| Training metrics (Sharpe, loss, etc.) | MLflow | Experiment tracking, comparison, promotion logic |

### 3.5 Do You Need to Retrain Before Production?

**No.** You can deploy current artifacts for paper trading:

- TFT v1 and v3 exist as `.pt` files
- The signal-engine ensemble scorer does not require all 3 models (TFT/XGB/LSTM) to function — it scores with whatever weights/models are available
- The isotonic calibrator is already in Redis
- XGBoost and LSTM models are missing from disk, but the ensemble scoring in `services/signal_engine/ensemble.py` handles missing components gracefully (zero weight)

**Retrain after paper trading validates the pipeline**, not before. Retraining now without validating the pipeline first wastes effort.

---

## Section 4 — DEPLOYMENT PATH

### 4.1 Docker Compose Path (Recommended First)

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
```

**Phase 1 — Infrastructure**
```bash
docker compose up -d redis kafka timescaledb mlflow
# Wait for all to report healthy:
docker compose ps
```

**Phase 2 — Database Schema**
```bash
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/init.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/lineage_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/feedback_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/signal_attribution_migration.sql

# Verify:
bash db/verify.sh
```

**Phase 3 — Calibration Artifact**
```bash
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python scripts/fit_isotonic_from_snapshots.py --redis-port 16379
# Verify:
docker exec infra-redis-1 redis-cli EXISTS apex:calibration:curve
# → (integer) 1
```

**Phase 4 — Data Pipeline**
```bash
cd infra
docker compose up -d signal-provider signal-provider-svc
docker compose up -d signal-engine
# Verify calibrator loaded:
docker logs infra-signal-engine-1 2>&1 | grep isotonic_calibrator_loaded
```

**Phase 5 — Risk + Execution**
```bash
docker compose up -d risk-engine
docker compose up -d execution-engine
```

**Phase 6 — Observability**
```bash
docker compose up -d prometheus redis-exporter grafana
```

**Phase 7 — Data Ingestion (last, starts the flow)**
```bash
docker compose up -d signal-provider  # if not already up
# data_ingestion and feature_engineering should already be running
```

**Phase 8 — Signal Generator (manual, until Dockerized)**
```bash
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python -m services.signal_generator.main &
# This starts producing apex.signals.raw from features table
```

### 4.2 Kubernetes Path

**Prerequisites** (in order):
1. Container registry configured and accessible from cluster
2. All SealedSecrets regenerated with real values (not PLACEHOLDER)
3. ConfigMap updated with feature flags
4. PVCs provisioned (redis: 2Gi, kafka: 5Gi, timescaledb: 10Gi, mlflow: 5Gi)

**ConfigMap additions needed** in `deploy/k8s/base/configmap.yaml`:
```yaml
ENABLE_ISOTONIC_CALIBRATION: "true"
ENABLE_PREDICTION_LINEAGE: "true"
ENABLE_DECISION_RECORDS: "true"
TRADING_ENABLED: "false"
LOG_LEVEL: "INFO"
EXECUTION_SIGNAL_TOPIC: "apex.risk.approved"
```

**SealedSecret regeneration** (per-cluster, must use kubeseal with target cluster's cert):
```bash
scripts/seal_secret.sh  # Helper script exists in repo
```

**Image build and push:**
```bash
for svc in signal_engine risk_engine execution data_ingestion feature_engineering model_monitor tft_service timesfm_service signal_provider; do
  docker build -t your-registry.io/apex/$svc:v0.5.0 -f services/$svc/Dockerfile .
  docker push your-registry.io/apex/$svc:v0.5.0
done
```

**Deploy sequence:**
```bash
# Step 1 — Namespace + Secrets
kubectl apply -f deploy/k8s/base/namespace.yaml
kubectl apply -f deploy/k8s/base/sealed-secrets-controller.yaml
kubectl apply -f deploy/k8s/base/sealed-secret-apex.yaml

# Step 2 — ConfigMap
kubectl apply -f deploy/k8s/base/configmap.yaml

# Step 3 — Infrastructure
kubectl apply -f deploy/k8s/base/redis-deployment.yaml
kubectl apply -f deploy/k8s/base/kafka-statefulset.yaml
kubectl apply -f deploy/k8s/base/timescaledb-statefulset.yaml
kubectl apply -f deploy/k8s/base/mlflow-deployment.yaml
# Wait: kubectl -n apex get pods -w

# Step 4 — Run DB migrations (kubectl exec into timescaledb pod)

# Step 5 — Pipeline
kubectl apply -f deploy/k8s/base/signal-engine-deployment.yaml
kubectl apply -f deploy/k8s/base/risk-engine-deployment.yaml
kubectl apply -f deploy/k8s/base/execution-deployment.yaml
kubectl apply -f deploy/k8s/base/data-ingestion-deployment.yaml
kubectl apply -f deploy/k8s/base/feature-engineering-deployment.yaml

# Or full overlay:
kubectl apply -k deploy/k8s/overlays/prod/
```

### 4.3 Required Environment Variables

**In `infra/.env` (Docker Compose):**
```
POSTGRES_PASSWORD=<secure-password>
ALPACA_API_KEY=<paper-key>
ALPACA_SECRET_KEY=<paper-secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
KILL_SWITCH=false
TRADING_ENABLED=false          # ← Set true ONLY when ready for paper orders
ENABLE_DECISION_RECORDS=true
ENABLE_PREDICTION_LINEAGE=true
ENABLE_ISOTONIC_CALIBRATION=true
MLFLOW_EXPERIMENT_NAME=apex-walk-forward
```

### 4.4 Paper-Trading-Safe Deployment

The key safety guards:

1. **`TRADING_ENABLED=false`** — execution-engine will NOT submit orders to Alpaca
2. **`ALPACA_BASE_URL=https://paper-api.alpaca.markets`** — even if orders are submitted, they go to paper. `configs/paper_trading.yaml` and `configs/live_trading.yaml` both have URL guards that abort on mismatch.
3. **`KILL_SWITCH=false`** + Redis nil — fail-open for paper, but `scripts/circuit_breaker.py` can latch it
4. **Position limits** in `configs/limits.yaml`: max 2% per position, 5% daily loss, 10% drawdown

Deploy everything with `TRADING_ENABLED=false` first. Validate the pipeline end-to-end (signals flowing, risk approving, execution receiving but NOT ordering). Then set `TRADING_ENABLED=true` and restart only execution-engine.

---

## Section 5 — TESTING AND VALIDATION

### 5.1 Unit Tests

```bash
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python -m pytest tests/ -v --tb=short
```

| Test File | What It Tests | Infrastructure |
|-----------|-------------|----------------|
| `tests/test_calibrator.py` | IsotonicCalibrator fit/calibrate/Redis round-trip | Mocked Redis |
| `tests/test_kill_switch.py` | Dual-layer kill switch (Redis + env), fail-closed | Mocked async Redis |
| `tests/test_integration.py` | Full Kafka flow: lean_alpha → signal → risk → execution | Mocked Kafka+Redis |
| `tests/test_ensemble_lineage.py` | EnsembleScorer returns (score, detail) tuple | Mocked |
| `tests/test_position_sizer.py` | Half-Kelly sizing, boundary cases | Pure unit |
| `tests/test_cost_estimator.py` | Net edge veto calculation | Pure unit |
| `tests/test_regime.py` | RegimeClassifier BULL/BEAR/SIDEWAYS | Mocked |
| `tests/test_schemas.py` | DecisionRecord Pydantic schemas, UUID generation | Pure unit |
| `tests/test_staleness.py` | StalenessPolicy time decay | Mocked |
| `tests/test_signal_staleness.py` | 30-second stale gate (Kafka) | Pure unit (time mocking) |
| `tests/test_services.py` | Bug fix regressions (Bug-A/B, CF-1 through CF-8) | Mocked |
| `tests/test_smoke.py` | CF-3 annualization + TradingLimits sanity | Mocked |
| `tests/test_adaptive_combiner.py` | Regime-weighted ensemble combiner | Mocked |
| `tests/test_disagreement.py` | DisagreementModifier penalty logic | Mocked |
| `tests/test_ood_detector.py` | Out-of-distribution detection | Mocked |
| `tests/test_counterfactual.py` | CounterfactualTracker analysis | Mocked |
| `tests/test_indicator_composite.py` | LightGBM composite model | Mocked |
| `tests/test_bl_weights.py` | Black-Litterman weight constraints | Pure unit |
| `tests/test_metrics_phase0.py` | Prometheus metric definitions | Pure unit |
| `tests/test_position_reconciliation.py` | Alpaca position mismatch detection | Mocked async httpx |

**All tests are mockable** — no live infrastructure required.

### 5.2 Integration Tests (Live Infrastructure)

These require the Docker Compose stack running.

**a) End-to-end signal injection:**
```bash
# Inject raw signal into Kafka
echo '{"symbol":"NVDA","direction":"long","score":0.72,"confidence":0.65,"model_id":"smoke-test","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' | \
  docker exec -i infra-kafka-1 /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server localhost:9092 --topic apex.signals.raw

# Read scored output
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic apex.signals.scored \
  --group "test-$(date +%s)" --max-messages 1 --timeout-ms 30000
```

**When to use**: After every deploy. This is the single most important integration test.

**b) Risk→Execution flow:**
```bash
# Consume from risk.approved
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic apex.risk.approved \
  --group "test-risk-$(date +%s)" --max-messages 1 --timeout-ms 30000
```

### 5.3 Service Health Tests

```bash
# Use curl — Postman is unnecessary for health checks
curl -sf http://localhost:8014/health | python3 -m json.tool  # signal-engine
curl -sf http://localhost:8008/health | python3 -m json.tool  # risk-engine
curl -sf http://localhost:8015/health | python3 -m json.tool  # execution
curl -sf http://localhost:8020/health | python3 -m json.tool  # model-monitor
curl -sf http://localhost:8009/health | python3 -m json.tool  # tft-service
curl -sf http://localhost:8010/health | python3 -m json.tool  # timesfm-service

# Or use the automated script:
bash scripts/health_check.sh
```

### 5.4 Kafka Flow Tests

**Use Kafka CLI, NOT Postman.** Kafka is a binary protocol — HTTP tools like Postman or curl cannot interact with it.

```bash
# List topics
docker exec infra-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list

# Check consumer lag
for g in apex-signal-engine-v1 apex-risk-engine-v1 apex-execution-v1; do
  docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --describe --group "$g"
done

# Consume latest message from a topic
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic apex.signals.scored \
  --group "debug-$(date +%s)" --max-messages 1 --timeout-ms 15000
```

### 5.5 Database Verification

```bash
# Run the DB verification script
bash infra/db/verify.sh

# Manual checks
docker exec apex-timescaledb psql -U apex_user -d apex -c "\dt"
docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT count(*) FROM ohlcv_bars;"
docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT count(*) FROM features;"
docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT count(*) FROM decision_records;"
```

### 5.6 Paper Trading Verification

```bash
# Step 1 — Enable paper orders
# Edit infra/.env: TRADING_ENABLED=true
# Then: docker compose up -d execution-engine

# Step 2 — Verify first trade
bash scripts/verify_first_trade.sh

# Step 3 — Monitor P&L
python scripts/paper_trading_monitor.py

# Step 4 — Check Alpaca paper account
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
     -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/account | python3 -m json.tool
```

### 5.7 Dashboard / API Verification

**When Postman is useful:**
- Testing the `signal_provider` API endpoints: `GET /signals/latest`, `GET /status`, `POST /signal`
- Testing dashboard API routes in `apex-dashboard/src/app/api/`
- Testing TFT service: `POST /predict`, `POST /model/reload`, `GET /model/info`

**When curl is enough:**
- Health checks (`/health`, `/ready`)
- Single-call API verification
- Anything you run in CI

**When Kafka console tools are the correct tool:**
- Anything involving topic consumption or production
- Consumer lag monitoring
- Schema verification of Kafka messages

```bash
# Dashboard (if running):
curl -s http://localhost:3001/api/bars?symbol=NVDA&timeframe=15Min&limit=5

# Signal provider API:
curl -s http://localhost:8007/health
curl -s http://localhost:8007/signals/latest

# TFT service:
curl -s http://localhost:8009/health
curl -s http://localhost:8009/model/info
```

---

## Section 6 — PRODUCTION GO-LIVE CHECKLIST

### 6.1 Pre-Deploy

| # | Step | Command / Action | Pass Criteria |
|---|------|-----------------|---------------|
| 1 | Run full test suite | `.venv/bin/python -m pytest tests/ -v` | All tests pass |
| 2 | Verify `infra/.env` flags | `grep -E "TRADING_ENABLED\|KILL_SWITCH\|ENABLE_" infra/.env` | `TRADING_ENABLED=false`, `KILL_SWITCH=false`, all `ENABLE_*=true` |
| 3 | Verify Alpaca URL is paper | `grep ALPACA_BASE_URL infra/.env` | `https://paper-api.alpaca.markets` |
| 4 | Verify `configs/paper_trading.yaml` guards | `grep "environment\|base_url" configs/paper_trading.yaml` | `environment: paper`, `base_url: https://paper-api.alpaca.markets` |
| 5 | Check git status | `git status && git log --oneline -1` | Clean working tree, correct tag |

### 6.2 Deploy

| # | Step | Command | Wait For |
|---|------|---------|----------|
| 1 | Start infrastructure | `cd infra && docker compose up -d redis kafka timescaledb mlflow` | All containers `(healthy)` |
| 2 | Apply DB migrations | Run 4 SQL files in order | `bash infra/db/verify.sh` passes |
| 3 | Load calibrator | `.venv/bin/python scripts/fit_isotonic_from_snapshots.py --redis-port 16379` | `EXISTS apex:calibration:curve` → 1 |
| 4 | Start model services | `cd infra && docker compose up -d tft-service timesfm-service model-monitor` | Health endpoints return 200 |
| 5 | Start signal-engine | `cd infra && docker compose up -d signal-engine` | Log shows `isotonic_calibrator_loaded_from_redis` |
| 6 | Start risk-engine | `cd infra && docker compose up -d risk-engine` | `/health` returns 200, `/status` shows `kill_switch: false` |
| 7 | Start execution-engine | `cd infra && docker compose up -d execution-engine` | `/health` returns 200 |
| 8 | Start observability | `cd infra && docker compose up -d prometheus grafana redis-exporter` | Grafana accessible at `:3000` |
| 9 | Start data pipeline | `cd infra && docker compose up -d signal-provider signal-provider-svc` | `/health` returns 200 |
| 10 | Start signal generator | `.venv/bin/python -m services.signal_generator.main &` | Check Kafka: messages appearing on `apex.signals.raw` |

### 6.3 Post-Deploy Smoke Tests

| # | Test | Command | Pass |
|---|------|---------|------|
| 1 | Health checks | `bash scripts/health_check.sh` | Exit code 0 |
| 2 | Redis calibrator | `docker exec infra-redis-1 redis-cli EXISTS apex:calibration:curve` | 1 |
| 3 | Kill switch off | `docker exec infra-redis-1 redis-cli GET apex:kill_switch` | nil |
| 4 | Consumer lag = 0 | Check 3 consumer groups | All partitions LAG=0 |
| 5 | Inject test signal | Producer → `apex.signals.raw`, consume from `apex.signals.scored` | JSON with `probability`, `isotonic_prob`, `platt_prob` |
| 6 | Risk pass-through | Consume from `apex.risk.approved` within 30s of injection | Approved message present |
| 7 | Decision record | `SELECT count(*) FROM decision_records` | Count increased |
| 8 | Kill switch test | `SET apex:kill_switch true`, wait 6s, check `/status`, then `DEL apex:kill_switch` | Risk engine shows `kill_switch: true` then `false` |
| 9 | Grafana dashboard | Open `http://localhost:3000` | apex-trading dashboard loads with live data |
| 10 | DB row counts | Query `ohlcv_bars`, `features`, `decision_records` | Non-zero and growing |

### 6.4 Paper Mode Validation

**Enable paper orders:**
```bash
# In infra/.env:
TRADING_ENABLED=true

# Restart only execution-engine:
cd infra && docker compose up -d execution-engine
```

**Run validation:**
```bash
bash scripts/verify_first_trade.sh
```

**Minimum burn-in: 5 full trading days** before considering live.

**Monitor daily:**
```bash
python scripts/paper_trading_monitor.py
```

### 6.5 Live Enablement Criteria

All must be true over ≥5 paper trading days:

| # | Criterion | Threshold |
|---|-----------|-----------|
| 1 | Paper Sharpe | ≥ 1.0 annualized |
| 2 | Hit rate | ≥ 55% |
| 3 | Max drawdown | ≤ 10% |
| 4 | Kill switch auto-triggered | 0 times |
| 5 | Consumer lag spike | Never > 50 messages |
| 6 | Service restarts | 0 crash-loops |
| 7 | Calibrator Brier score | ≤ 0.25 |
| 8 | `daily_feedback.py` executed | ≥ 3 times (builds calibration data) |
| 9 | `go_live_validator.py --strict` | Exit code 0 |

**Live activation:**
```bash
# 1. Swap credentials in infra/.env:
ALPACA_API_KEY=<live-key>
ALPACA_SECRET_KEY=<live-secret>
ALPACA_BASE_URL=https://api.alpaca.markets

# 2. Run validator:
python scripts/go_live_validator.py --strict --json

# 3. Restart pipeline (DO NOT restart infra — keep calibrator in Redis):
cd infra && docker compose up -d signal-engine risk-engine
# Set TRADING_ENABLED=true LAST:
cd infra && docker compose up -d execution-engine
```

### 6.6 Rollback Steps

| Severity | Action | Command | Effect |
|----------|--------|---------|--------|
| S1 — Halt orders | Kill switch | `docker exec infra-redis-1 redis-cli SET apex:kill_switch true` | Risk engine blocks all signals within 5s. No restart. |
| S2 — Disable execution | Trading flag | `TRADING_ENABLED=false` in `infra/.env`, `cd infra && docker compose up -d execution-engine` | Orders stop. Pipeline continues for audit trail. |
| S3 — Revert to paper | Credential swap | Replace keys + URL in `infra/.env`, restart pipeline services | All orders go to paper endpoint. |
| S4 — Revert calibration | Flag toggle | `ENABLE_ISOTONIC_CALIBRATION=false`, restart signal-engine | Reverts to Platt probability. |
| S5 — Full rollback | Git + rebuild | `git checkout v0.5.0`, `cd infra && docker compose down && docker compose build && docker compose up -d` | To known-good state. |

---

## Section 7 — AGENT / AUTOMATION QUESTION

### Do you need to create separate agents?

**No.** The current repo already covers every responsibility. Here's the mapping:

| Responsibility | Already Covered By | Additional Agent Needed? |
|---------------|-------------------|-------------------------|
| **Database management** | `infra/db/init.sql` + 3 migration files, `infra/db/verify.sh`, TimescaleDB retention policies + continuous aggregates in `init.sql` | **NO** — DB is schema-managed. No need for a DB agent. |
| **Model training** | `services/model_training/train_tft.py`, `train_lstm.py`, `train_xgb.py`, `train_ensemble.py`, `walk_forward.py` | **NO** — batch scripts exist. Run manually or via `retrain_scheduler.py`. |
| **Model registry** | `services/model_manager/model_registry.py` — `ModelRegistry` class stores/queries model state in Redis under `apex:models:*` | **NO** — library module, imported by other services. |
| **Model monitoring** | `services/model_monitor/main.py` — computes live Sharpe, compares to backtest, writes drift to Redis. Prometheus alerts in `infra/prometheus/model_alerts.yml`. | **NO** — service already deployed (`infra-model-monitor-1`). |
| **Calibration refit** | `scripts/daily_feedback.py` — labels positions, refits calibrator, updates `calibration_snapshots` | **NO** — script exists. Needs scheduling (cron or CronJob), not a new agent. |
| **Retrain scheduling** | `scripts/retrain_scheduler.py` — time and Sharpe-drift triggers, spawns walk-forward training | **NO** — script exists. Run as daemon or CronJob. |
| **Circuit breaking** | `scripts/circuit_breaker.py` — drawdown-triggered kill switch, latching, fail-closed | **NO** — script exists. Run as background process or systemd unit. |
| **Health monitoring** | `scripts/health_check.sh` — checks 7 services, 3 consumer groups, DB freshness, kill switch | **NO** — script exists. Schedule via cron. |
| **Position reconciliation** | `infra-position-reconciler-1` container already running | **NO** — already deployed. |
| **Signal attribution** | `services/attribution/tracker.py` — joins signals with order outcomes | **NO** — code exists. Needs Dockerfile + docker-compose entry. |
| **Dashboard** | `apex-dashboard/` — Next.js app with full portfolio/trading UI | **NO** — exists. Deploy standalone. |
| **Social sentiment** | `twitter_tft/` — 4 docker-compose services for StockTwits/Reddit → FinBERT → Kafka | **NO** — already deployed. |

### What actually needs work (but not new agents)

| Item | What to do | Estimated effort |
|------|-----------|------------------|
| `signal_generator` needs Dockerfile | Write Dockerfile + add to docker-compose | ~30 min |
| `exit_monitor` needs Dockerfile | Write Dockerfile + add to docker-compose | ~30 min |
| `circuit_breaker.py` needs scheduling | Add to docker-compose as daemon OR cron | ~15 min |
| `daily_feedback.py` needs scheduling | Add cron entry or k8s CronJob | ~15 min |
| `retrain_scheduler.py` needs scheduling | Run as daemon or CronJob | ~15 min |
| `attribution/tracker.py` needs Dockerfile | Write Dockerfile + add to docker-compose | ~30 min |
| K8s ConfigMap needs feature flags | Add 6 env vars to `configmap.yaml` | ~5 min |
| K8s SealedSecrets need real values | Run `scripts/seal_secret.sh` per-cluster | ~20 min |

**Total**: These are all configuration/packaging tasks, not new service development.

---

## Section 8 — FINAL RECOMMENDATION

### The Smallest Safe Path to Production

#### TODAY (before market close)

1. **Run the full test suite:**
   ```bash
   .venv/bin/python -m pytest tests/ -v --tb=short
   ```

2. **Verify the live Docker stack is healthy:**
   ```bash
   bash scripts/health_check.sh
   ```

3. **Create a Dockerfile for `signal_generator`** (this is the critical gap — without it, no signals flow):
   ```dockerfile
   # services/signal_generator/Dockerfile
   FROM python:3.11-slim AS builder
   RUN apt-get update && apt-get install -y gcc librdkafka-dev && rm -rf /var/lib/apt/lists/*
   COPY requirements.txt /tmp/
   RUN pip install --prefix=/install --no-cache-dir -r /tmp/requirements.txt

   FROM python:3.11-slim
   COPY --from=builder /install /usr/local
   ENV PYTHONPATH=/app
   WORKDIR /app
   COPY shared/ shared/
   COPY configs/ configs/
   COPY services/__init__.py services/
   COPY services/signal_generator/ services/signal_generator/
   COPY services/graceful_shutdown.py services/
   RUN groupadd -r apex && useradd -r -g apex apex
   USER apex
   CMD ["python", "-m", "services.signal_generator.main"]
   ```

4. **Add `signal-generator` to docker-compose** and start it.

5. **Verify end-to-end**: inject signal → see scored output → see risk approval.

#### BEFORE PAPER TRADING

1. **Dockerize `exit_monitor`** — provides stop-loss/take-profit for open positions. Not strictly required for paper testing, but important for risk management.

2. **Schedule `circuit_breaker.py`** — run as background process or docker-compose service. This is your safety net against drawdown.

3. **Verify Alpaca paper credentials work:**
   ```bash
   curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        https://paper-api.alpaca.markets/v2/account | python3 -m json.tool
   ```

4. **Set `TRADING_ENABLED=true`** and restart execution-engine.

5. **Run `verify_first_trade.sh`** after first market open.

6. **Run `paper_trading_monitor.py`** daily after close for 5 trading days minimum.

#### BEFORE REAL LIVE TRADING

1. **Paper trading results meet all 9 criteria in Section 6.5.**

2. **Run `daily_feedback.py` at least 3 times** so the calibrator refits from real trade outcomes.

3. **Run `go_live_validator.py --strict`** — must return exit code 0.

4. **If using k8s**: Add feature flags to ConfigMap, regenerate SealedSecrets, push images to registry, deploy with `kubectl apply -k deploy/k8s/overlays/prod/`.

5. **Swap Alpaca credentials to live**, set `ALPACA_BASE_URL=https://api.alpaca.markets`.

6. **Start with `TRADING_ENABLED=false`** for one market session — watch signals flow through without orders.

7. **Enable `TRADING_ENABLED=true`** — this is the moment of truth.

8. **Monitor first 30 minutes with:**
   ```bash
   watch -n 5 'curl -s http://localhost:8008/status | python3 -m json.tool'
   docker logs -f infra-execution-engine-1 2>&1 | grep -i "order\|fill\|submitted"
   ```

9. **Keep `scripts/circuit_breaker.py` running**. It will latch the kill switch at 5% intraday drawdown. Manual reset only.

### Summary

| Phase | Key Action | Blocking? |
|-------|-----------|-----------|
| Today | Dockerize `signal_generator`, verify full flow | **YES** — pipeline has no signal source without it |
| Before paper | Schedule `circuit_breaker.py`, verify Alpaca creds, enable `TRADING_ENABLED` | YES |
| During paper (5 days) | Run `daily_feedback.py`, `paper_trading_monitor.py`, watch Grafana | YES |
| Before live | Pass all Section 6.5 criteria, run `go_live_validator.py --strict`, swap to live creds | YES |
| Live day 1 | Start with `TRADING_ENABLED=false`, observe, then enable. Keep circuit breaker running. | — |

The system is architecturally complete. What remains is packaging (Dockerfiles), scheduling (cron jobs), and validation (paper trading burn-in). No new services need to be written.
