# REPO GUARDRAILS — Immutable Facts

> **Purpose**: Single source of truth for every path, name, and convention in this repo.
> Any AI assistant, script, or doc that references these items MUST use the exact
> strings below. If it doesn't match, the reference is wrong — not this file.
>
> **Last verified**: 2025-06-01 — commit `860acb8`

---

## 1. File-system layout

| What                 | Path (from repo root)          |
|----------------------|--------------------------------|
| Docker Compose file  | `infra/docker-compose.yml`     |
| Environment file     | `infra/.env`                   |
| Operator scripts     | `scripts/`                     |
| Shared library       | `shared/`                      |
| Service code         | `services/<name>/`             |
| Documentation        | `docs/`                        |
| Kubernetes manifests | `deploy/k8s/`                  |
| Config YAML          | `configs/`                     |
| DB init scripts      | `infra/db/`                    |
| Grafana dashboards   | `infra/grafana/`               |
| Prometheus config    | `infra/prometheus/`            |

### Run-from rules

- **All commands** assume CWD = repo root (`/home/kironix/workspace/QuantConnect.VS`).
- Scripts MUST be invoked as `bash scripts/<name>.sh`, never `./scripts/<name>.sh` or bare `<name>.sh`.
- `docker compose` commands MUST include `-f infra/docker-compose.yml`.
- Environment sourcing: `set -a && source infra/.env && set +a`.
- Every shell script MUST auto-detect repo root:
  ```bash
  REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$REPO_ROOT"
  ```

---

## 2. Docker Compose services (canonical names)

### Infrastructure
| Service              | Container name pattern        |
|----------------------|-------------------------------|
| `redis`              | `infra-redis-1`               |
| `redis-exporter`     | `infra-redis-exporter-1`      |
| `kafka`              | `infra-kafka-1`               |
| `timescaledb`        | `apex-timescaledb`            |
| `schema-registry`    | `infra-schema-registry-1`     |
| `prometheus`         | `infra-prometheus-1`          |
| `grafana`            | `infra-grafana-1`             |
| `mlflow`             | `infra-mlflow-1`              |

### Core pipeline (in execution order)
| Service                | Container name pattern            |
|------------------------|-----------------------------------|
| `data_ingestion`       | `apex-data-ingestion`             |
| `feature_engineering`  | `apex-feature-engineering`        |
| `signal-generator`     | `infra-signal-generator-1`        |
| `signal-engine`        | `infra-signal-engine-1`           |
| `risk-engine`          | `infra-risk-engine-1`             |
| `execution-engine`     | `infra-execution-engine-1`        |

### Supporting services
| Service                | Purpose                         |
|------------------------|---------------------------------|
| `tft-service`          | Temporal Fusion Transformer     |
| `timesfm-service`      | Google TimesFM                  |
| `model-monitor`        | Drift / performance monitoring  |
| `model-scheduler`      | Retrain scheduling              |
| `signal-provider`      | REST API for signals            |
| `signal-provider-svc`  | Signal provider wrapper         |
| `apex-dashboard`       | Next.js dashboard               |
| `social-ingest`        | Reddit/StockTwits ingestion     |
| `social-sentiment`     | FinBERT sentiment scoring       |
| `social-features`      | Sentiment feature engineering   |
| `social-kafka-publish`  | Push sentiment to Kafka         |

---

## 3. Kafka topics

| Topic                    | Producer           | Consumer            |
|--------------------------|--------------------|---------------------|
| `apex.signals.raw`       | signal-generator   | signal-engine       |
| `apex.signals.scored`    | signal-engine      | risk-engine         |
| `apex.signals.sentiment` | social-kafka-publish | signal-engine     |
| `apex.risk.approved`     | risk-engine        | execution-engine    |
| `apex.orders.results`    | execution-engine   | monitoring          |
| `apex.dlq`               | any service        | ops (dead letters)  |

---

## 4. Redis keys

| Key                                 | Type   | Purpose                              |
|--------------------------------------|--------|--------------------------------------|
| `apex:kill_switch`                   | string | `true` = block all orders            |
| `apex:calibration:curve`             | string | Pickled isotonic regression model     |
| `apex:signal_engine:last_signal_ts`  | string | ISO timestamp of last scored signal   |
| `apex:signal_engine:active_model`    | string | Currently active model ID             |
| `apex:signal_engine:ab_test`         | string | A/B test config JSON                  |
| `apex:ensemble:weights`              | string | JSON ensemble weights                 |
| `apex:ensemble:predictions`          | list   | Last 5000 predictions                 |
| `apex:positions`                     | hash   | Open position map                     |
| `apex:llm:sentiment:{SYMBOL}`        | string | Per-symbol LLM sentiment score        |
| `apex:regime:{SYMBOL}`               | string | Per-symbol market regime              |
| `apex:models:{model_id}`            | string | Individual model metadata             |
| `apex:models:all`                    | set    | Registry of all model IDs             |
| `apex:model_schedule`                | string | Retrain schedule config               |
| `apex:model_version_counter`         | string | Integer counter for model versions    |
| `apex:model_alerts`                  | list   | Alert queue                           |
| `apex:model_events`                  | list   | Last 1000 model events                |
| `apex:agent_log`                     | list   | Last 1000 agent log entries           |
| `apex:feedback:brier_score`          | string | Running Brier score                   |

---

## 5. TimescaleDB

| Setting    | Value                            |
|------------|----------------------------------|
| Host       | `timescaledb` (in Docker network)|
| Port       | `5432` (container), `15432` (host)|
| Database   | `apex`                           |
| User       | `apex_user`                      |
| Password   | `apex_pass`                      |

### Tables
| Table                   | Purpose                              |
|-------------------------|--------------------------------------|
| `ohlcv_bars`            | OHLCV candlestick data               |
| `market_raw_minute`     | Raw minute-level market data          |
| `signals`               | Raw signal records                    |
| `signals_scored`        | Scored / calibrated signals           |
| `features`              | Engineered features                   |
| `orders`                | Order submissions and fills           |
| `positions`             | Position snapshots                    |
| `portfolio_snapshots`   | Portfolio value over time             |
| `model_performance`     | Model accuracy/loss tracking          |
| `decision_records`      | Audit trail: every order decision     |
| `calibration_snapshots` | Isotonic calibration history          |
| `trade_feedback`        | Post-trade P&L feedback              |
| `model_regime_accuracy` | Accuracy segmented by market regime   |
| `veto_counterfactuals`  | "What if we hadn't vetoed?" analysis  |
| `signal_attribution`    | Feature attribution per signal        |

---

## 6. MLflow

| Setting          | Value                     |
|------------------|---------------------------|
| Tracking URI     | `http://mlflow:5000`      |
| Host port        | `5001`                    |
| Experiment       | `apex_ensemble`           |

---

## 7. Ports (host → container)

| Service      | Host  | Container |
|--------------|-------|-----------|
| TimescaleDB  | 15432 | 5432      |
| Redis        | 16379 | 6379      |
| Kafka        | 9094  | 9092      |
| MLflow       | 5001  | 5000      |
| Grafana      | 3000  | 3000      |
| Prometheus   | 9090  | 9090      |
| Dashboard    | 3001  | 3000      |

---

## 8. Safety invariants

1. `TRADING_ENABLED` in `infra/.env` defaults to `false`. Execution engine MUST check this on every message.
2. `apex:kill_switch` in Redis is the canonical emergency stop. Risk engine checks it before approving.
3. Both guards must be satisfied for an order to reach Alpaca.
4. `ENABLE_DECISION_RECORDS=true` — every order decision (approved, vetoed, error) is written to `decision_records`.
5. `ENABLE_PREDICTION_LINEAGE=true` — every scored signal records its model inputs.
6. `ENABLE_ISOTONIC_CALIBRATION=true` — probabilities are calibrated via isotonic regression before risk scoring.

---

## 9. Naming rules for AI assistants

- **NEVER** invent service names. Use only names from §2.
- **NEVER** invent Kafka topics. Use only topics from §3.
- **NEVER** invent Redis keys. Use only keys from §4.
- **NEVER** reference `.env` without the `infra/` prefix.
- **NEVER** reference `docker-compose.yml` without the `infra/` prefix.
- **NEVER** write `./scripts/foo.sh` — always `bash scripts/foo.sh`.
- **NEVER** assume `source .env` works — always `set -a && source infra/.env && set +a`.
- When in doubt, `grep` this file for the canonical value.
