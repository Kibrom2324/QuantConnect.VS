# APEX Go-Live Plan

**Repository:** `https://github.com/Kibrom2324/QuantConnect.VS.git`
**Branch:** `main` | **Tag:** `v0.5.0` | **Commit:** `d7dec7f`
**Date:** 2026-03-08

---

## Section 1 — What Is Already Complete

### 1.1 Core Pipeline (proven end-to-end in Docker Compose)

| Service | Container | Port | Status |
|---------|-----------|------|--------|
| `services/signal_engine` | `infra-signal-engine-1` | 8014→8006 | Healthy, 16 h uptime |
| `services/risk_engine` | `infra-risk-engine-1` | 8008→8004 | Healthy, 20 h uptime |
| `services/execution` | `infra-execution-engine-1` | 8015→8006 | Healthy, 18 h uptime |
| `services/data_ingestion` | `apex-data-ingestion` | 8011→8001 | Healthy, 24 h uptime |
| `services/feature_engineering` | `apex-feature-engineering` | 8013→8003 | Healthy, 24 h uptime |
| `services/model_monitor` | `infra-model-monitor-1` | 8020→8020 | Healthy, 19 h uptime |

### 1.2 Infrastructure (all healthy ≥ 24 h)

| Component | Container | Port |
|-----------|-----------|------|
| Redis 7 | `infra-redis-1` | 16379→6379, AOF enabled |
| Kafka 3.7 (KRaft) | `infra-kafka-1` | 9092, 9094 |
| TimescaleDB (PG 15) | `apex-timescaledb` | 15432→5432 |
| MLflow | `infra-mlflow-1` | 5001→5000 |
| Prometheus | `infra-prometheus-1` | 9090 |
| Grafana | `infra-grafana-1` | 3000 |

### 1.3 Kafka Pipeline (zero lag confirmed)

| Consumer Group | Topic | Partitions | Lag |
|----------------|-------|------------|-----|
| `apex-signal-engine-v1` | `apex.signals.raw` | 4 | 0 |
| `apex-risk-engine-v1` | `apex.signals.scored` | 4 | 0 |
| `apex-execution-v1` | `apex.risk.approved` | 4 | 0 |

### 1.4 Feature Flags (verified in container env)

| Flag | Value | Verified In |
|------|-------|-------------|
| `ENABLE_ISOTONIC_CALIBRATION` | `true` | signal-engine |
| `ENABLE_PREDICTION_LINEAGE` | `true` | signal-engine |
| `ENABLE_DECISION_RECORDS` | `true` | execution-engine |
| `TRADING_ENABLED` | `false` | `infra/.env` + execution-engine container env |
| `KILL_SWITCH` | `false` | `infra/.env`; Redis key `apex:kill_switch` = nil |

### 1.5 Calibration Artifacts

- Isotonic calibrator loaded from Redis key `apex:calibration:curve` (664 bytes, pickle protocol 4)
- Platt scaler loaded from `configs/models/platt_scaler.json`
- Cutover and rollback both proven with live Kafka messages
- 13/13 calibrator unit tests passing (`tests/test_calibrator.py`)

### 1.6 Database Schema (11 tables in `apex` database)

Created by `infra/db/init.sql` + 3 migrations:

| Migration File | Tables Created |
|----------------|----------------|
| `infra/db/init.sql` | `ohlcv_bars`, `features`, `signals`, `orders`, `positions`, `portfolio_snapshots`, `model_performance`, `market_raw_minute`, `signals_scored` |
| `infra/db/lineage_migration.sql` | `decision_records`, `calibration_snapshots` |
| `infra/db/feedback_migration.sql` | `trade_feedback`, `veto_counterfactuals`, `model_regime_accuracy` |
| `infra/db/signal_attribution_migration.sql` | `signal_attribution` |

### 1.7 Kubernetes Manifests (written, not deployed)

- `deploy/k8s/base/` — 17 manifests including all pipeline services, infra StatefulSets, ConfigMap, SealedSecrets
- `deploy/k8s/overlays/prod/kustomization.yaml` — replicas: 2, Kafka replicas: 3, resource scaling
- `deploy/k8s/overlays/dev/kustomization.yaml` — replicas: 1, LOG_LEVEL: DEBUG

### 1.8 Test Suite

22 test files in `tests/`, covering: calibrator, kill switch, schemas, ensemble lineage, position sizing, regime detection, staleness, OOD detection, integration.

### 1.9 Operational Scripts

| Script | Purpose |
|--------|---------|
| `scripts/go_live_validator.py` | 10-check pre-flight validation for live mode |
| `scripts/fit_isotonic_from_snapshots.py` | Refit calibrator from `calibration_snapshots`, push to Redis |
| `scripts/daily_feedback.py` | Post-close labeling, calibrator refit, attribution refresh |
| `scripts/circuit_breaker.py` | Manual kill-switch activation |
| `scripts/health_check.sh` | Service health probe |
| `scripts/verify_first_trade.sh` | First-trade validation |
| `scripts/paper_trading_monitor.py` | Monitor paper trading performance |
| `scripts/replay_harness.py` | Replay historical signals through pipeline |

---

## Section 2 — What Must Be Verified Before Deploy

### 2.1 Local Validation (Docker Compose — current host)

Run all of these on the development machine before touching production.

**a) Unit Tests**
```bash
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python -m pytest tests/ -v --tb=short
```
Pass criteria: all 22 test files green. Pay attention to `test_calibrator.py` (13 tests) and `test_kill_switch.py`.

**b) Infrastructure Connectivity**
```bash
# TimescaleDB
docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT count(*) FROM decision_records;"

# Redis
docker exec infra-redis-1 redis-cli PING
docker exec infra-redis-1 redis-cli EXISTS apex:calibration:curve

# Kafka topics
docker exec infra-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list | grep apex
```
Expected: PONG, `(integer) 1`, six `apex.*` topics.

**c) Consumer Lag = 0**
```bash
for group in apex-signal-engine-v1 apex-risk-engine-v1 apex-execution-v1; do
  echo "--- $group ---"
  docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --describe --group "$group" 2>&1 | grep -v "^$"
done
```
Every partition must show `LAG = 0`.

**d) Health Endpoints**
```bash
curl -s http://localhost:8014/health   # signal-engine → 200
curl -s http://localhost:8008/health   # risk-engine   → 200
curl -s http://localhost:8015/health   # execution     → 200
```

**e) Kill Switch Off**
```bash
docker exec infra-redis-1 redis-cli GET apex:kill_switch
# Must return (nil) or "false"
```

**f) Isotonic Calibration Active**
```bash
docker logs infra-signal-engine-1 2>&1 | grep "isotonic_calibrator_loaded_from_redis"
# Must appear in startup logs
```

**g) Env Flag Audit**
```bash
docker exec infra-signal-engine-1 env | grep -E "ENABLE_ISOTONIC|ENABLE_PREDICTION|SKIP_MARKET"
# ENABLE_ISOTONIC_CALIBRATION=true
# ENABLE_PREDICTION_LINEAGE=true
# SKIP_MARKET_HOURS must NOT exist

docker exec infra-execution-engine-1 env | grep -E "ENABLE_DECISION|TRADING_ENABLED|ALPACA_BASE"
# ENABLE_DECISION_RECORDS=true
# TRADING_ENABLED must be false until cutover
# ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### 2.2 Production Validation (Kubernetes — before traffic)

**a) SealedSecrets Regeneration**

The file `deploy/k8s/base/sealed-secret-apex.yaml` contains PLACEHOLDER values for all four secrets. Before deploying to any k8s cluster:

```bash
# Install kubeseal CLI, then for each secret:
kubectl create secret generic apex-alpaca-secret \
  --from-literal=api_key="$ALPACA_API_KEY" \
  --from-literal=secret_key="$ALPACA_SECRET_KEY" \
  --namespace apex --dry-run=client -o yaml | \
  kubeseal --controller-namespace kube-system --format yaml \
  > deploy/k8s/base/sealed-secret-apex-alpaca.yaml

# Repeat for: apex-db-secret, apex-api-secret, apex-redis-secret
```

**b) ConfigMap Feature Flags**

The file `deploy/k8s/base/configmap.yaml` is **missing** these flags:
```
ENABLE_ISOTONIC_CALIBRATION
ENABLE_PREDICTION_LINEAGE
ENABLE_DECISION_RECORDS
TRADING_ENABLED
LOG_LEVEL
```
They must be added to the ConfigMap before k8s deploy, or services will default to `false`.

**c) Image Registry**

All k8s deployments reference `apex/{service}:latest`. Before deploy:
```bash
# Build and push each image
for svc in signal_engine risk_engine execution data_ingestion feature_engineering model_monitor; do
  docker build -t your-registry.io/apex/$svc:v0.5.0 -f services/$svc/Dockerfile .
  docker push your-registry.io/apex/$svc:v0.5.0
done
```
Update image tags in k8s manifests from `:latest` to `:v0.5.0`.

**d) Persistent Volume Claims**

Three PVCs must exist before StatefulSets start:
- `redis-pvc` (2 Gi)
- `kafka-data` (5 Gi)
- `db-data` (10 Gi)
- `mlflow-pvc` (5 Gi)

**e) go_live_validator.py (Live Mode Only)**
```bash
python scripts/go_live_validator.py --strict --json
```
10 checks: Alpaca URL, env vars, Alpaca credentials, kill switch, TimescaleDB, Redis, Polygon API. Must return exit code 0.

---

## Section 3 — What Artifacts Must Exist Before Startup

### 3.1 Model Artifacts

| Artifact | Location | How to Produce |
|----------|----------|----------------|
| TFT model v1 | `models/TFT_v1/` | Pre-trained, committed in repo |
| TFT model v3 | `models/TFT_v3/` | Pre-trained, committed in repo |
| Platt scaler | `configs/models/platt_scaler.json` | Pre-trained, committed in repo |
| Isotonic calibrator | Redis key `apex:calibration:curve` | Run `scripts/fit_isotonic_from_snapshots.py --redis-port 16379` |

### 3.2 Calibration Artifacts

The isotonic calibrator **must be in Redis before signal-engine starts**. If Redis is empty (fresh deploy):

```bash
# Requires calibration_snapshots table to have data
python scripts/fit_isotonic_from_snapshots.py \
  --dsn "postgresql://apex_user:apex_pass@localhost:15432/apex" \
  --redis-host localhost \
  --redis-port 16379 \
  --redis-key apex:calibration:curve
```

Verify:
```bash
redis-cli -p 16379 EXISTS apex:calibration:curve
# Must return (integer) 1
```

If no `calibration_snapshots` data exists yet, set `ENABLE_ISOTONIC_CALIBRATION=false` in `infra/.env` and run in Platt-only mode until `scripts/daily_feedback.py` populates enough data (≥50 closed positions recommended).

### 3.3 Database Schema

All 4 SQL files must be applied in order:

```bash
# On first deploy or fresh database
docker exec -i apex-timescaledb psql -U apex_user -d apex < infra/db/init.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < infra/db/lineage_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < infra/db/feedback_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < infra/db/signal_attribution_migration.sql
```

Verify:
```bash
docker exec apex-timescaledb psql -U apex_user -d apex -c "\dt"
# Must show ≥ 11 tables
```

### 3.4 Kafka Topics

Six topics must exist. They are auto-created by producers, but to pre-create:

```bash
for topic in apex.signals.raw apex.signals.scored apex.risk.approved \
             apex.orders.results apex.dlq apex.signals.sentiment; do
  docker exec infra-kafka-1 /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server localhost:9092 --create --if-not-exists \
    --topic "$topic" --partitions 4 --replication-factor 1
done
```

### 3.5 Configuration Files

| File | Required For | Key Guard |
|------|-------------|-----------|
| `configs/paper_trading.yaml` | Paper mode | `app.environment: paper`, `alpaca.base_url: https://paper-api.alpaca.markets` |
| `configs/live_trading.yaml` | Live mode | `app.environment: live`, `alpaca.base_url: https://api.alpaca.markets` |
| `configs/limits.yaml` | Risk engine | Per-symbol position limits |
| `configs/app.yaml` | All services | `app.environment: paper` default |
| `infra/.env` | Docker Compose | All env vars, secrets |

### 3.6 Credentials

| Credential | Location (Docker Compose) | Location (K8s) |
|------------|--------------------------|----------------|
| Alpaca API key + secret | `infra/.env` → `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | `apex-alpaca-secret` SealedSecret |
| TimescaleDB password | `infra/.env` → `POSTGRES_PASSWORD` | `apex-db-secret` SealedSecret |
| Redis password | `infra/.env` → `REDIS_PASSWORD` (empty for dev) | `apex-redis-secret` SealedSecret |
| Grafana password | `infra/.env` → `GRAFANA_PASSWORD` | Not in k8s manifests |

---

## Section 4 — Exact Deployment Order of Services

### 4.1 Docker Compose (Local / Paper Trading)

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
```

**Phase 1 — Infrastructure (no dependencies)**
```bash
docker compose up -d redis kafka timescaledb mlflow
# Wait for all healthchecks to pass:
docker compose ps  # All must show (healthy)
```

**Phase 2 — Schema + Artifacts**
```bash
# Apply DB migrations (idempotent)
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/init.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/lineage_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/feedback_migration.sql
docker exec -i apex-timescaledb psql -U apex_user -d apex < db/signal_attribution_migration.sql

# Push calibrator (if not already in Redis)
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python scripts/fit_isotonic_from_snapshots.py --redis-port 16379
```

**Phase 3 — Model Services**
```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
docker compose up -d tft-service timesfm-service model-monitor
```

**Phase 4 — Data Pipeline**
```bash
docker compose up -d signal-provider signal-provider-svc
docker compose up -d signal-engine
# Wait for signal-engine to log: "isotonic_calibrator_loaded_from_redis"
docker compose logs -f signal-engine 2>&1 | head -20
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

**Phase 7 — Optional / Social**
```bash
docker compose up -d social-ingest social-sentiment social-features social-kafka-publish
docker compose up -d schema-registry
```

### 4.2 Kubernetes (Production)

```bash
cd /home/kironix/workspace/QuantConnect.VS

# Step 1 — Namespace + Secrets
kubectl apply -f deploy/k8s/base/namespace.yaml
kubectl apply -f deploy/k8s/base/sealed-secrets-controller.yaml
kubectl apply -f deploy/k8s/base/sealed-secret-apex.yaml   # Must have real values, not PLACEHOLDER

# Step 2 — ConfigMap (with feature flags added)
kubectl apply -f deploy/k8s/base/configmap.yaml

# Step 3 — Infrastructure StatefulSets
kubectl apply -f deploy/k8s/base/redis-deployment.yaml
kubectl apply -f deploy/k8s/base/kafka-statefulset.yaml
kubectl apply -f deploy/k8s/base/timescaledb-statefulset.yaml
kubectl apply -f deploy/k8s/base/mlflow-deployment.yaml
# Wait: kubectl -n apex get pods --watch  (all Running + Ready)

# Step 4 — Run DB migrations (one-time Job or exec into timescaledb pod)

# Step 5 — Pipeline services (order matters)
kubectl apply -f deploy/k8s/base/signal-engine-deployment.yaml
kubectl apply -f deploy/k8s/base/risk-engine-deployment.yaml
kubectl apply -f deploy/k8s/base/execution-deployment.yaml
kubectl apply -f deploy/k8s/base/data-ingestion-deployment.yaml
kubectl apply -f deploy/k8s/base/feature-engineering-deployment.yaml
kubectl apply -f deploy/k8s/base/exit-monitor-deployment.yaml

# Or use kustomize for the full overlay:
kubectl apply -k deploy/k8s/overlays/prod/
```

---

## Section 5 — Exact Smoke Tests After Deployment

Run these **in order** after all services are up.

### Test 1 — Infrastructure Health

```bash
# TimescaleDB
docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT 1;"
# → 1

# Redis
docker exec infra-redis-1 redis-cli PING
# → PONG

# Kafka
docker exec infra-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list | grep apex
# → 6 topics
```

### Test 2 — Service Health Endpoints

```bash
curl -sf http://localhost:8014/health | python3 -m json.tool
# → {"status": "healthy", "service": "signal_engine", ...}

curl -sf http://localhost:8008/health | python3 -m json.tool
# → {"status": "healthy", "service": "risk_engine", ...}

curl -sf http://localhost:8015/health | python3 -m json.tool
# → {"status": "healthy", "service": "execution_engine", ...}
```

### Test 3 — Calibrator Loaded

```bash
docker exec infra-redis-1 redis-cli EXISTS apex:calibration:curve
# → (integer) 1

docker logs infra-signal-engine-1 2>&1 | grep isotonic
# → "isotonic_calibrator_loaded_from_redis"
```

### Test 4 — Kafka Consumer Lag

```bash
for group in apex-signal-engine-v1 apex-risk-engine-v1 apex-execution-v1; do
  docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --describe --group "$group" 2>&1 \
    | awk '{print $6}' | grep -v LAG | grep -v "^$" | sort -u
done
# All must output: 0
```

### Test 5 — End-to-End Signal Flow (inject a test signal)

```bash
# Produce a raw signal
echo '{"symbol":"NVDA","direction":"long","score":0.72,"confidence":0.65,"model_id":"smoke-test","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' | \
  docker exec -i infra-kafka-1 /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server localhost:9092 --topic apex.signals.raw

# Consume scored signal (wait ≤ 30 s)
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic apex.signals.scored \
  --group "smoke-$(date +%s)" \
  --max-messages 1 --timeout-ms 30000
```

Expected: JSON message containing `probability`, `isotonic_prob`, `platt_prob`, `raw_edge_bps`, `ensemble_method`.

### Test 6 — Risk Engine Pass-Through

```bash
# Check that scored signal was forwarded (if confidence ≥ threshold)
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic apex.risk.approved \
  --group "smoke-risk-$(date +%s)" \
  --max-messages 1 --timeout-ms 30000
```

### Test 7 — Kill Switch

```bash
# Activate
docker exec infra-redis-1 redis-cli SET apex:kill_switch true
sleep 6  # risk-engine polls every 5s

# Verify risk-engine rejects
curl -s http://localhost:8008/status | python3 -m json.tool
# → kill_switch: true

# Deactivate
docker exec infra-redis-1 redis-cli DEL apex:kill_switch
sleep 6
curl -s http://localhost:8008/status | python3 -m json.tool
# → kill_switch: false
```

### Test 8 — Decision Records Written

```bash
docker exec apex-timescaledb psql -U apex_user -d apex \
  -c "SELECT count(*) FROM decision_records WHERE created_at > now() - interval '1 hour';"
# → ≥ 1 (from the smoke-test signal above, if ENABLE_DECISION_RECORDS=true)
```

### Test 9 — Risk Engine Prometheus Metrics

```bash
curl -s http://localhost:8008/metrics | grep apex_risk
# → apex_risk_checks_total, apex_risk_rejections_total, apex_portfolio_drawdown_pct, etc.
```

### Test 10 — Rollback Verification (Isotonic → Platt)

```bash
# In infra/.env, set ENABLE_ISOTONIC_CALIBRATION=false
# Then:
docker compose up -d signal-engine  # recreates container

# Inject another signal and verify scored output shows:
# probability == platt_prob (isotonic_prob still present but not used)
```

---

## Section 6 — Criteria for Enabling Paper Mode and Then Live Trading

### 6.1 Paper Trading Activation

**Prerequisites (all must be true):**

| # | Criterion | How to Verify |
|---|-----------|---------------|
| 1 | All Section 5 smoke tests pass | Run tests 1–9 above |
| 2 | `configs/paper_trading.yaml` loaded | `app.environment: paper` guard |
| 3 | `ALPACA_BASE_URL=https://paper-api.alpaca.markets` | `grep ALPACA_BASE_URL infra/.env` |
| 4 | `TRADING_ENABLED=false` in `infra/.env` | Execution-engine won't submit orders yet |
| 5 | `KILL_SWITCH=false` | Redis key nil or `false` |
| 6 | Calibrator in Redis | `redis-cli EXISTS apex:calibration:curve` → 1 |
| 7 | Consumer lag = 0 | Test 4 above |

**Enable paper order flow:**
```bash
# Edit infra/.env:
TRADING_ENABLED=true

# Restart only execution-engine:
cd /home/kironix/workspace/QuantConnect.VS/infra
docker compose up -d execution-engine
```

**Post-enable monitoring:**
```bash
# Watch for first order
docker logs -f infra-execution-engine-1 2>&1 | grep -i "order\|alpaca\|submitted"

# Or run the verification script
bash scripts/verify_first_trade.sh

# Monitor paper trading
python scripts/paper_trading_monitor.py
```

**Paper trading burn-in period: minimum 5 full trading days** before considering live.

### 6.2 Paper → Live Promotion Criteria

All of the following must be satisfied over the paper trading burn-in:

| # | Criterion | Threshold | Source |
|---|-----------|-----------|--------|
| 1 | Paper Sharpe ratio | ≥ 1.0 (annualized) | `scripts/paper_trading_monitor.py` |
| 2 | Paper hit rate | ≥ 55% | `decision_records` + `trade_feedback` tables |
| 3 | Max drawdown | ≤ 10% | `portfolio_snapshots` table |
| 4 | Kill switch never auto-triggered | 0 activations | Redis logs |
| 5 | No Kafka consumer lag > 50 | All groups | Consumer group describe |
| 6 | Calibrator Brier score | ≤ 0.25 | `scripts/daily_feedback.py` output |
| 7 | No service crash-loops | 0 restarts | `docker ps` restart count |
| 8 | `daily_feedback.py` run ≥ 3 times | Enough data for calibrator refit | `calibration_snapshots` row count |
| 9 | All 10 go_live_validator checks pass | Exit code 0 | `python scripts/go_live_validator.py --strict` |

### 6.3 Live Trading Activation

**Step 1 — Config swap**

```bash
# Point services at live config
# In configs/live_trading.yaml, verify these guards:
#   app.environment: live
#   alpaca.base_url: https://api.alpaca.markets
#   risk.portfolio.max_position_pct: 0.01  (half of paper)
#   risk.min_signal_confidence: 0.70        (stricter)
```

**Step 2 — Credential swap**

```bash
# In infra/.env, replace paper credentials:
ALPACA_API_KEY=<live-api-key>
ALPACA_SECRET_KEY=<live-secret-key>
ALPACA_BASE_URL=https://api.alpaca.markets
```

**Step 3 — Validator pass**

```bash
python scripts/go_live_validator.py --strict --json
# Must return exit code 0 with all 10 checks PASS
```

**Step 4 — Staged restart**

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra

# Do NOT restart infra (Redis, Kafka, TimescaleDB) — keep calibrator in memory
# Restart pipeline services only:
docker compose up -d signal-engine
docker compose up -d risk-engine

# Enable trading flag LAST:
# Edit infra/.env: TRADING_ENABLED=true
docker compose up -d execution-engine
```

**Step 5 — Immediate post-live checks**

```bash
# Verify Alpaca connectivity
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
     -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
     https://api.alpaca.markets/v2/account | python3 -m json.tool
# → status: ACTIVE

# Watch first live order
docker logs -f infra-execution-engine-1 2>&1 | grep -i "order\|submitted\|filled"

# Monitor risk engine continuously
watch -n 5 'curl -s http://localhost:8008/status | python3 -m json.tool'
```

### 6.4 Rollback Procedure

**Severity 1 — Immediate halt (kill switch)**
```bash
docker exec infra-redis-1 redis-cli SET apex:kill_switch true
# Risk engine blocks all signals within 5 seconds
# No restart required — all services keep running, orders stop
```

**Severity 2 — Disable order submission**
```bash
# Edit infra/.env: TRADING_ENABLED=false
docker compose up -d execution-engine
# Execution-engine restarts, stops submitting to Alpaca
# Signal + risk pipeline continues (audit trail preserved)
```

**Severity 3 — Revert to paper mode**
```bash
# Edit infra/.env:
ALPACA_API_KEY=<paper-key>
ALPACA_SECRET_KEY=<paper-secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
TRADING_ENABLED=false

# Restart all pipeline services
docker compose up -d signal-engine risk-engine execution-engine
```

**Severity 4 — Revert isotonic calibration**
```bash
# Edit infra/.env: ENABLE_ISOTONIC_CALIBRATION=false
docker compose up -d signal-engine
# Signal-engine reverts to Platt probability as active_prob
```

**Severity 5 — Full rollback to previous tag**
```bash
git checkout v0.5.0
cd infra && docker compose down
docker compose build
docker compose up -d
```

### 6.5 Scheduled Tasks (Not Yet Automated)

These must be scheduled before live trading is considered stable:

| Task | Script | Schedule | Notes |
|------|--------|----------|-------|
| Calibrator refit | `scripts/daily_feedback.py` | Daily 16:30 ET | Keeps old calibrator if new Brier is worse |
| Calibrator push | `scripts/fit_isotonic_from_snapshots.py` | After daily_feedback | Only if refit improved |
| Health check | `scripts/health_check.sh` | Every 5 min (cron) | Alert on non-200 |
| Paper monitor | `scripts/paper_trading_monitor.py` | Hourly during market hours | PnL, Sharpe, drawdown |

Recommended crontab:
```cron
# Daily feedback - labels + refit (after market close)
30 16 * * 1-5  cd /home/kironix/workspace/QuantConnect.VS && .venv/bin/python scripts/daily_feedback.py --redis-port 16379 >> logs/daily_feedback.log 2>&1

# Health check every 5 minutes
*/5 * * * *    bash /home/kironix/workspace/QuantConnect.VS/scripts/health_check.sh >> logs/health.log 2>&1
```
