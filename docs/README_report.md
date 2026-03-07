# QuantConnect.VS (APEX + LEAN)

This workspace contains a local QuantConnect LEAN setup with:
- Python and C# algorithm variants
- Enhanced metrics and local metrics database
- Unified run pipeline script for backtest + reporting
- Optional Signal API integration

---

## Step-by-Step Documentation

## Step 1) Prerequisites

Make sure these are available on Linux:

```bash
dotnet --version
python3 --version
```

Recommended Python environments in this workspace:
- `lean_venv` (general workspace tooling)
- `lean311_env` (used by LEAN Python runtime/report integration)

---

## Step 2) Go to Workspace Root

```bash
cd /home/kironix/workspace/QuantConnect.VS
```

All commands below assume you are in this folder.

---

## Step 3) Run a First Validation Backtest

Run the lightweight baseline mode:

```bash
bash ./run_strategy.sh test
```

This does all of the following automatically:
1. Updates `Lean/Launcher/bin/Debug/config.json`
2. Runs LEAN backtest
3. Copies result JSON to report directory
4. Runs enhanced reporter (`MyProject/backtest_reporter.py`)
5. Generates QuantConnect HTML report
6. Starts a local report server (`http://localhost:8766/report.html`)

---

## Step 4) Choose Run Mode

Use any of these modes:

```bash
bash ./run_strategy.sh test
bash ./run_strategy.sh sma
bash ./run_strategy.sh ensemble
bash ./run_strategy.sh framework
bash ./run_strategy.sh simple-cs
bash ./run_strategy.sh ensemble-cs
bash ./run_strategy.sh paper-trading     # full microservice stack (paper mode)
```

Mode summary:
- `test`: minimal Python smoke test
- `sma`: simple SMA crossover strategy
- `ensemble`: Python APEX ensemble strategy
- `framework`: Python framework strategy
- `simple-cs`: C# smoke test strategy
- `ensemble-cs`: C# APEX ensemble strategy
- `paper-trading`: starts all 8 APEX microservices + infra in isolated paper mode

---

## Step 5) (Optional) Start Signal API While Running

```bash
bash ./run_strategy.sh ensemble --start-api
```

If started, API endpoints are available at:
- `http://localhost:8000/docs`
- `http://localhost:8000/health`

---

## Step 5b) (Optional) Start Full Paper Trading Stack

```bash
bash ./run_strategy.sh paper-trading
# With dashboard:
bash ./run_strategy.sh paper-trading --start-ui
```

This starts all 8 microservices in fully isolated paper mode using
`configs/paper_trading.yaml`. See [docs/PAPER_TRADING_RUNBOOK.md](docs/PAPER_TRADING_RUNBOOK.md)
for the complete operating guide.

---

## Step 6) Locate Outputs

After each run, check:

- LEAN result JSON: `Lean/Launcher/bin/Debug/<AlgorithmName>.json`
- Enhanced text report: `backtest_report_<AlgorithmName>_*.txt`
- Metrics DB: `MyProject/backtest_metrics.db`
- QuantConnect HTML report: `Lean/Report/bin/Debug/report.html`
- Live preview URL: `http://localhost:8766/report.html`

---

## Step 7) Run Reporter Directly (Manual Mode)

Auto-select latest valid backtest JSON:

```bash
python MyProject/backtest_reporter.py
```

Or run with explicit file + name:

```bash
python MyProject/backtest_reporter.py <path-to-result-json> <algorithm-name>
```

---

## Step 8) Validate Run Success Quickly

Useful check commands:

```bash
grep -E "Backtest completed successfully|APEX Strategy Complete|ERROR:: Engine.Run" /tmp/run_*.log
```

Or inspect latest launcher logs in:
- `Lean/Launcher/bin/Debug/*-log.txt`

---

## Step 9) Key Files You Will Edit Most

- `run_strategy.sh` (main pipeline)
- `MyProject/backtest_reporter.py` (metrics/reporting)
- `Lean/Algorithm.Python/APEXEnsembleAlgorithm.py`
- `Lean/Algorithm.Python/APEXFrameworkAlgorithm.py`
- `Lean/Algorithm.CSharp/APEXEnsembleAlgorithm.cs`
- `Lean/Algorithm.CSharp/SimpleCSAlgorithm.cs`

---

## Step 10) Troubleshooting

### A) C# algorithm type not found

Rebuild and refresh algorithm DLL:

```bash
cd /home/kironix/workspace/QuantConnect.VS/Lean
dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug
cp Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll Launcher/bin/Debug/
```

Then rerun from workspace root.

### B) Reporter usage error

If run with no args, it now auto-selects latest valid backtest JSON.
If you want explicit control, pass `<json path> <algorithm name>`.

### C) Signal API push fails

This is non-blocking if API is down. Start API with `--start-api` or run API separately.

### D) Matplotlib ticklabel warnings in report generation

These are non-blocking and do not prevent `report.html` creation.

---

## Environment Notes

- LEAN Python runtime is configured around Python 3.11 symbols.
- Runner script handles Python env sanitization for backtest and report phases.

---

## Current Status (Validation Summary)

Validated in this workspace:
- `test`: pass
- `sma`: pass
- `ensemble`: pass
- `framework`: pass (can still produce low/no trades depending on data/filters)
- `simple-cs`: pass
- `ensemble-cs`: pass

---

## Algorithm Status

| Mode | File | Signal Type | Status | Known Issues |
|---|---|---|---|---|
| `sma` | `SMACrossoverAlgorithm.py` | 50/200 SMA crossover | ✅ Baseline | — |
| `ensemble` | `APEXEnsembleAlgorithm.py` | TFT + RSI + EMA + MACD + Stoch + Sent ensemble | ✅ Running | ✅ All known issues fixed 2026-02-27 |
| `framework` | `APEXFrameworkAlgorithm.py` | Framework-based | ⚠️ Low trades | Data/filter dependent |
| `ensemble-cs` | `APEXEnsembleAlgorithm.cs` | C# port of ensemble | ✅ Running | Mirrors Python bugs |

---

## Known Bugs

| ID | File | Bug | Status |
|---|---|---|---|
| CF-3 | `backtest_reporter.py` | Sharpe uses √252 on minute data → should be √(252×390) | ✅ FIXED 2026-02-27 |
| CF-10 | `configs/app.yaml` | Duplicate `take_profit_pct` and `stop_loss_pct` keys | ✅ FIXED 2026-02-27 |
| Bug-A | `services/risk_engine/engine.py` | Risk limits load from `"config/"` not `"configs/"` | ✅ FIXED 2026-02-27 |
| Bug-B | `lean_alpha/main.py`, `signal_engine/main.py`, `execution/main.py` | Kafka `enable.auto.commit: True` | ✅ FIXED 2026-02-27 |
| CF-6 | `services/risk_engine/engine.py` | Redis crash resets kill switch to OFF | ✅ FIXED 2026-02-27 |
| CF-1 | `services/model_training/walk_forward.py` | Max-Sharpe fold selection → look-ahead bias | ✅ FIXED 2026-02-27 |
| CF-2 | `services/model_training/walk_forward.py` | Embargo gap too short (21 bars) | ✅ FIXED 2026-02-27 |
| CF-4 | `services/model_training/dataset.py` | Scaler fitted on full dataset (leakage) | ✅ FIXED 2026-02-27 |
| CF-5 | `services/risk_engine/engine.py` | CVaR Gaussian approximation | ✅ FIXED 2026-02-27 |
| CF-7 | `services/execution/main.py` | Consumer committed before flush | ✅ FIXED 2026-02-27 |
| CF-8 | `services/execution/main.py` | No Alpaca timeout → event-loop starvation | ✅ FIXED 2026-02-27 |
| CF-9 | `services/graceful_shutdown.py` | Shutdown hung indefinitely | ✅ FIXED 2026-02-27 |

---

## Roadmap

### Phase 00 — Fix Foundation (Completed 2026-02-27)

- [x] CF-3: Fix Sharpe annualization (resolution-aware ann_factor)
- [x] Bug-A: Fix risk limits YAML path (`config/` → `configs/`)
- [x] CF-10: configs/app.yaml created with no duplicate keys
- [x] Bug-B: Kafka auto-commit False + manual commit on success
- [x] CF-6: Fail-closed on Redis errors

### Phase 01 — Signal Expansion (Completed 2026-02-27)

- [x] Stochastic alpha: %K/%D crossover signal wired into ensemble
- [x] Reddit sentiment: TimescaleDB `reddit_sentiment` table → sentiment signal
- [x] Bayesian weight updater: beta-binomial posterior weights
- [x] Bear regime direction: long signal dampening only (correct by design)

### Phase 02 — Portfolio Layer

- [x] ETF universe selector: dynamic multi-asset universe via `ETFConstituentsUniverseSelectionModel`
- [x] Black-Litterman construction: blend CAPM equilibrium with alpha views (closed-form per-asset BL)
- [x] TimescaleDB aggregates: continuous OHLCV (5m / 15m / 1h) + retention policies

---

## System Architecture

```
[Polygon/yfinance] → DataIngestion (8001) → [market.raw]
                                                   ↓
                                      FeatureEngineering (8002)
                                                   ↓
                                           [market.engineered]
                                                   ↓
                                  TFT ModelInference (8003)
                                                   ↓
                                          [predictions.tft]
                                                   ↓
        LeanAlpha (8014) — RSI/EMA/MACD/Stochastic/SMA/Sentiment
                                                   ↓
                                          [alpha.signals]
                                                   ↓
                           SignalEngine (8015) — Bayesian Ensemble
                                                   ↓
                                          [signals.scored]
                                                   ↓
                                      RiskEngine (8004)
                                                   ↓
                                          [risk.approved]
                                                   ↓
                             ExecutionAgent (8005) → Alpaca Paper API
                                                   ↓
                                          ExitMonitor (8010)
```

## Algorithm Status

| Mode          | File                         | Signals                     | Status            | Notes                    |
|---------------|------------------------------|-----------------------------|-------------------|--------------------------|
| `test`        | BasicTemplateAlgorithm.py    | None                        | ✅ Smoke test     | —                        |
| `sma`         | SMACrossoverAlgorithm.py     | SMA 50/200                  | ✅ Baseline       | —                        |
| `ensemble`    | APEXEnsembleAlgorithm.py     | TFT+RSI+EMA+MACD+Stoch+Sent | ✅ Running        | CF-3 fixed 2026-02-27    |
| `framework`   | APEXFrameworkAlgorithm.py    | Framework RSI/EMA/MACD      | ⚠️ Low trades    | Data dependent           |
| `simple-cs`   | SimpleCSAlgorithm.cs         | Smoke test                  | ✅ Pass           | —                        |
| `ensemble-cs` | APEXEnsembleAlgorithm.cs     | TFT+RSI+EMA+MACD            | ✅ Running        | Mirrors Python           |

## Bug Fix Status

| ID     | File                              | Bug                                       | Status                                             |
|--------|-----------------------------------|-------------------------------------------|----------------------------------------------------|
| CF-3   | `backtest_reporter.py`            | √252 on minute data → Sharpe 20× off     | ✅ FIXED 2026-02-27 — resolution-aware ann_factor  |
| CF-10  | `configs/app.yaml`                | Duplicate TP/SL YAML keys                 | ✅ FIXED — created with no duplicates              |
| Bug-A  | `services/risk_engine/engine.py`      | config/ vs configs/ path              | ✅ FIXED 2026-02-27 — uses configs/limits.yaml     |
| Bug-B  | lean_alpha / signal_engine / execution | Kafka auto-commit True               | ✅ FIXED 2026-02-27 — False + manual commit in all 3 |
| CF-1   | `services/model_training/walk_forward.py` | Max-Sharpe fold → look-ahead bias | ✅ FIXED 2026-02-27 — returns folds[-1] (most recent) |
| CF-2   | `services/model_training/walk_forward.py` | Embargo too short (21 bars)      | ✅ FIXED 2026-02-27 — EMBARGO_BARS = 180           |
| CF-4   | `services/model_training/dataset.py`  | Scaler on full dataset (leakage)      | ✅ FIXED 2026-02-27 — IS-only fit + JSON sidecar   |
| CF-5   | `services/risk_engine/engine.py`      | CVaR Gaussian approximation          | ✅ FIXED 2026-02-27 — historical simulation        |
| CF-7   | `services/execution/main.py`          | Commit before flush                   | ✅ FIXED 2026-02-27 — flush() then commit()        |
| CF-8   | `services/execution/main.py`          | No Alpaca timeout                     | ✅ FIXED 2026-02-27 — httpx.Timeout(30.0)          |
| CF-9   | `services/graceful_shutdown.py`       | Shutdown hung indefinitely            | ✅ FIXED 2026-02-27 — asyncio.wait_for(30s)        |
| CF-6   | risk_engine                       | Redis crash resets kill switch            | ✅ Pattern fixed in shared/core/trading_safety.py  |
| HI-4   | APEXEnsembleAlgorithm.py          | Bear regime flattens short signals        | ✅ ALREADY CORRECT — longs ×0.3, shorts unchanged  |
| HI-6   | infra/prometheus/alerts.yml       | PipelineStale fires 24/7                  | ✅ FIXED — market hours gated (UTC 14–21 weekdays) |
| HI-8   | infra/docker-compose.yml          | Redis no AOF, no healthcheck              | ✅ FIXED — AOF + healthcheck + depends_on          |
| MD-1   | Kafka                             | No retention limits                       | ✅ FIXED — docker-compose + configs/app.yaml       |
| MD-2   | TimescaleDB                       | No retention policy                       | ✅ FIXED — infra/db/init.sql                       |

## Roadmap

### Phase 00 — Fix Foundation (Completed 2026-02-27)
- [x] CF-3: Sharpe annualization fix (resolution-aware ann_factor)
- [x] CF-10: configs/app.yaml created with no duplicate keys
- [x] CF-6 pattern: shared/core/trading_safety.py fail-closed kill switch
- [x] HI-6: Prometheus alerts market-hours gated
- [x] HI-8: Redis AOF + healthcheck in docker-compose
- [x] MD-1/MD-2: Kafka/TimescaleDB retention limits
- [x] Bug-A: risk_engine uses configs/limits.yaml (not config/)
- [x] Bug-B: Kafka manual commit — all 3 consumer services

### Phase 01 — Signal Expansion (Completed 2026-02-27)
- [x] Stochastic %K/%D alpha (ADDITION 1)
- [x] Reddit sentiment signal (ADDITION 2)
- [x] Bayesian weight updater (ADDITION 3 + BayesianWeightUpdater class)
- [x] Bear regime direction — correct by design
- [x] TFT staleness gate — signal_engine/ensemble.py (600s TTL)

### Phase 02 — Portfolio Layer (Completed 2026-02-28)
- [x] ETFConstituentsUniverseSelectionModel (LiquidETFUniverseSelectionModel — top 20 by dollar volume)
- [x] Black-Litterman portfolio construction (closed-form per-asset; prior = 1/n, views = insight magnitude)
- [x] TimescaleDB continuous aggregates (5m / 15m / 1h OHLCV materialized views + retention policies)

### Phase 03 — APEX Core Services (Completed 2026-02-27)
- [x] Created services/ directory — all 8 microservices
- [x] CF-1: walk_forward returns folds[-1] (most recent, not max-Sharpe)
- [x] CF-2: embargo_bars = 180 (was 21)
- [x] CF-4: FoldScaler IS-only fit + JSON sidecar persistence
- [x] CF-5: CVaR historical simulation (was Gaussian approximation)
- [x] CF-7: producer.flush() before consumer.commit()
- [x] CF-8: Alpaca httpx.Timeout(30.0)
- [x] CF-9: asyncio.wait_for(30s) on all shutdown coroutines
- [x] RSI / EMA-cross / MACD standalone alpha modules
- [x] data_ingestion, feature_engineering, exit_monitor services

### Phase 04 — Production Hardening (Completed 2026-02-28)
- [x] Docker build + push in CI (3 service Dockerfiles + docker-build CI job)
- [x] K8s manifests (deploy/k8s/ — base + dev/prod overlays via Kustomize)
- [x] Secrets vault integration (Bitnami Sealed Secrets — see _How to Seal a New Secret_ below)
- [x] MLflow experiment tracking (walk_forward.py + MLflow service in compose)
- [x] Integration tests (end-to-end Kafka flow — tests/test_integration.py, 88 total passing)

### Phase 05 — APEX Dashboard (Completed 2026-02-28)
- [x] Next.js 14 trading dashboard (apex-dashboard/) — 4 pages: /dashboard, /signals, /backtest, /models
- [x] BFF API routes (all secrets server-side, never exposed to browser)
- [x] Dashboard docker-compose service (port 3001) with multi-stage Dockerfile
- [x] `run_strategy.sh --start-ui` flag

### Signal Attribution Tracking (Completed 2026-02-28)
- [x] `services/attribution/tracker.py` — Kafka consumer service: caches scored signal snapshots (LRU, 10min TTL); on every closed trade pairs fill with latest signal state; writes one row per signal × trade to `signal_attribution` hypertable; UNIQUE constraint prevents Kafka at-least-once duplicates
- [x] `scripts/signal_attribution_report.py` — CLI report: per-signal win rate, avg P&L, total P&L, alignment rate, Sharpe contribution, avg ensemble weight; flags signals with win rate < 45%, negative avg weight, or negative Sharpe contribution; supports `--days`, `--symbol`, `--signal`, `--json`, `--csv`; exit code 1 if any signal flagged
- [x] `infra/db/signal_attribution_migration.sql` — TimescaleDB hypertable `signal_attribution` (7-day chunks, 365-day retention, 4 indexes, validation view `signal_attribution_summary`)

### Phase 07 — Model Operations (Completed 2026-02-28)
- [x] `scripts/retrain_scheduler.py` — daemon: triggers walk-forward retrain on 30-day cadence OR 30% live Sharpe drift; logs to MLflow `apex-retrain-events`; webhook alert on completion
- [x] `services/model_monitor/main.py` — hourly poll service: 14-day live Sharpe from Alpaca, MLflow baseline, drift ratio; 6 Prometheus gauges on port 8020; writes `apex:model:live_sharpe_14d` to Redis
- [x] `services/model_monitor/Dockerfile` + docker-compose `model-monitor` service
- [x] `infra/prometheus/model_alerts.yml` — 7 rules: ModelSharpeDrift (≥30%), ModelSharpeDriftCritical (≥50%), ModelStale (45d), ModelStaleCritical (60d), ModelMonitorDown, ModelLiveSharpeNegative, ModelMonitorPollStale
- [x] `infra/docker-compose.yml` updated — Prometheus mounts `model_alerts.yml`; `model-monitor` service added

### Phase 08 — Paper Trading Validation (IN PROGRESS 🔄)
- [x] `configs/paper_trading.yaml` — fully isolated paper config (2% position cap, 3% daily loss limit, top-20 QQQ universe)
- [x] `scripts/paper_trading_monitor.py` — daily report: P&L, win rate, hold time, weight drift, loss alerts
- [x] `scripts/health_check.sh` — extended with Kafka lag, TimescaleDB freshness, signal age checks
- [x] `docs/PAPER_TRADING_RUNBOOK.md` — complete operating guide + go/no-go checklist
- [x] `run_strategy.sh paper-trading` — one-command full stack startup with paper safety guard
- [ ] 30-day paper trading window — monitoring active
- [ ] Statistical validation (win rate ≥ 52%, Sharpe ≥ 1.2, zero daily loss breaches)
- [ ] Go/No-Go sign-off for live capital

---

## Secrets Management

All production secrets (API keys, database passwords) are sealed with
[Bitnami Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets).
The encrypted `SealedSecret` manifests in `deploy/k8s/base/sealed-secret-apex.yaml`
are **safe to commit** — they are cluster-specific ciphertext, useless without the
controller's private key.

### Secret inventory

| K8s Secret name       | Keys stored                             | Used by                               |
|-----------------------|-----------------------------------------|---------------------------------------|
| `apex-alpaca-secret`  | `api_key`, `secret_key`                 | execution, data-ingestion             |
| `apex-db-secret`      | `username`, `password`                  | timescaledb, all APEX services        |
| `apex-api-secret`     | `anthropic_api_key`, `polygon_api_key`  | lean-alpha, signal-engine             |
| `apex-redis-secret`   | `password`                              | risk-engine, exit-monitor, lean-alpha |

### First-time setup

```bash
# 1. Install kubeseal CLI
curl -sSL https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.27.0/kubeseal-0.27.0-linux-amd64.tar.gz \
  | tar xz -C /usr/local/bin kubeseal

# 2. Install the controller and fetch the cluster public key
./scripts/seal_secret.sh bootstrap

# 3. Fill in real secret values
cp .env.example .env
$EDITOR .env   # set ALPACA_API_KEY, ALPACA_SECRET_KEY, TIMESCALEDB_PASSWORD, etc.

# 4. Seal all secrets and write to deploy/k8s/base/sealed-secret-apex.yaml
./scripts/seal_secret.sh seal-all

# 5. Apply to cluster
kubectl apply -f deploy/k8s/base/sealed-secret-apex.yaml

# 6. Verify the controller decrypted them
./scripts/seal_secret.sh verify
```

### Rotating a secret

```bash
# Edit the new value in .env, then:
./scripts/seal_secret.sh rotate
# This re-seals from .env, writes sealed-secret-apex.yaml, and kubectl-applies.
# Restart pods to pick up the new secret:
kubectl -n apex rollout restart deployment
```

### Docker Compose (local dev)

For local development, secrets are passed as environment variables sourced from `.env`.
All `[REQUIRED]` entries in `.env.example` must be filled before `docker compose up`.
The compose file enforces this with `${VAR:?message}` syntax — missing vars abort startup.

```bash
export COMPOSE_FILE=infra/docker-compose.yml
source .env
docker compose up -d
```

### Security notes

- **Never** commit `.env` or any file containing real secret values.
- `sealed-secret-apex.yaml` placeholder values (`PLACEHOLDER__*`) **will not work** in a cluster — always run `seal-all` before deploying.
- Sealed values are **cluster-specific** — regenerate when the cluster key changes (e.g., after a disaster-recovery cluster rebuild).
- The `sealed-secrets-cert.pem` (public key) is safe to commit and required to run `seal-all` offline.

---

## How to Seal a New Secret

All Kubernetes secrets are managed via [Bitnami Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets).
Encrypted `SealedSecret` manifests are safe to commit to Git.

### First-time setup (per cluster)

```bash
# 1. Install kubeseal CLI
#    Linux: wget .../kubeseal-linux-amd64.tar.gz | tar xz -C /usr/local/bin kubeseal
#    macOS: brew install kubeseal

# 2. Install the controller + fetch the cluster public key
./scripts/seal_secret.sh bootstrap

# 3. Fill in your secrets
cp .env.example .env
$EDITOR .env     # Set ALPACA_API_KEY, ALPACA_SECRET_KEY, POSTGRES_PASSWORD, etc.

# 4. Seal all secrets from .env into a commit-safe YAML
./scripts/seal_secret.sh seal-all
# → creates: deploy/k8s/base/sealed-secret-apex-generated.yaml

# 5. Apply to cluster
kubectl apply -f deploy/k8s/base/sealed-secret-apex-generated.yaml

# 6. Verify the controller decrypted them into plain Secrets
./scripts/seal_secret.sh verify
```

### Rotating a single secret

```bash
# Seal one value and update the generated YAML, then re-apply
./scripts/seal_secret.sh seal-all
kubectl apply -f deploy/k8s/base/sealed-secret-apex-generated.yaml
```

### Required secrets (see `.env.example` for full list)

| Secret name          | K8s Secret          | Keys                          |
|----------------------|---------------------|-------------------------------|
| Alpaca API           | `apex-alpaca-secret` | `api_key`, `secret_key`       |
| TimescaleDB password | `apex-db-secret`     | `username`, `password`        |
| Anthropic + Polygon  | `apex-api-secret`    | `anthropic_api_key`, `polygon_api_key` |
| Redis password       | `apex-redis-secret`  | `password`                    |

---

## Starting the Full System (First Paper Trade Run)

### Pre-flight Checklist

Before running `docker compose up`, verify:

- [ ] `.env` copied from `.env.example` and all `[REQUIRED]` vars filled in
- [ ] `ALPACA_BASE_URL` is `https://paper-api.alpaca.markets` (never live without explicit approval)
- [ ] `POSTGRES_PASSWORD` is a strong password (≥ 20 chars), not the placeholder
- [ ] `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are paper-trading keys from app.alpaca.markets
- [ ] `POLYGON_API_KEY` is set (or `DATA_SYMBOLS` reduced to avoid rate limits)
- [ ] No existing `mlruns/` dir conflicts with the MLflow volume mount

### Start Commands

```bash
# 1. Source secrets
source .env
export COMPOSE_FILE=infra/docker-compose.yml

# 2. Start all infrastructure services first
docker compose up -d redis kafka timescaledb mlflow
sleep 30   # allow Kafka and TimescaleDB to finish initializing

# 3. Run health checks (infrastructure only)
./scripts/health_check.sh

# 4. Start all APEX microservices
docker compose up -d

# 5. Tail logs to watch signal flow
docker compose logs -f data-ingestion lean-alpha signal-engine risk-engine execution

# 6. After 2-3 minutes: verify first trade
./scripts/verify_first_trade.sh
```

### Health Check Scripts

```bash
# Docker Compose environment
./scripts/health_check.sh

# Kubernetes environment
./scripts/health_check.sh --k8s

# Verify first order reached Alpaca
./scripts/verify_first_trade.sh
```

### Monitoring

| Service   | URL                         | What to check                    |
|-----------|-----------------------------|----------------------------------|
| MLflow    | http://localhost:5000        | Walk-forward fold runs + tags    |
| Grafana   | http://localhost:3000        | Pipeline latency, CVaR, kill-switch state |
| Prometheus | http://localhost:9090       | `apex_pipeline_stale` alert      |
| Alpaca    | https://app.alpaca.markets/paper-trading | Order history, P&L |

---

## APEX Dashboard

A Next.js 14 web UI that visualises live trading activity, signal stream, backtest results, and MLflow model runs.

### Pages

| URL                       | Description                                          |
|---------------------------|------------------------------------------------------|
| http://localhost:3001      | Redirect → `/dashboard`                              |
| http://localhost:3001/dashboard | Service status + Kill-switch + P&L + Positions |
| http://localhost:3001/signals   | Live signal stream with alpha breakdown         |
| http://localhost:3001/backtest  | Backtest file loader + equity curve + trades    |
| http://localhost:3001/models    | MLflow walk-forward runs + Sharpe bar chart     |

### Quick Start (dev server)

```bash
cp apex-dashboard/.env.local.example apex-dashboard/.env.local
# edit .env.local with your ALPACA keys if needed
cd apex-dashboard
npm install
npm run dev -- -p 3001
```

### With run_strategy.sh

```bash
# Start backtest + API + dashboard in one command
bash ./run_strategy.sh ensemble --start-api --start-ui
```

### With Docker Compose

```bash
export COMPOSE_FILE=infra/docker-compose.yml
docker compose build apex-dashboard
docker compose up -d apex-dashboard
# Dashboard available at http://localhost:3001
```

### Architecture

All data fetching is handled by Next.js BFF API routes (`/src/app/api/`).
Secrets (Alpaca keys, MLflow URL) are only accessed server-side — never exposed to the browser.

```
Browser → Next.js /api/* → FastAPI :8000 / MLflow :5000 / Alpaca API
```

### Dashboard `.env.local` keys

| Variable        | Default                                    | Description             |
|-----------------|--------------------------------------------|-------------------------|
| `APEX_API_URL`  | `http://localhost:8000`                    | FastAPI signal provider |
| `MLFLOW_API_URL`| `http://localhost:5000`                    | MLflow tracking server  |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets`       | Paper or live Alpaca    |
| `ALPACA_API_KEY`  | —                                        | Alpaca key (server-side only) |
| `ALPACA_SECRET_KEY` | —                                      | Alpaca secret (server-side only) |
