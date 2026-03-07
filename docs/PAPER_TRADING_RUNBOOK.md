# APEX Paper Trading Runbook

**Last updated: 2026-02-28**

This document covers everything needed to operate APEX in paper trading mode:
start the full stack, monitor it daily, interpret metrics, and decide when it is
safe to move to live capital.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Starting the Full Stack](#2-starting-the-full-stack)
3. [Daily Monitoring Workflow](#3-daily-monitoring-workflow)
4. [Health Check Reference](#4-health-check-reference)
5. [Kill Switch Protocol](#5-kill-switch-protocol)
6. [Metric Interpretation Guide](#6-metric-interpretation-guide)
7. [Go / No-Go Checklist for Live Trading](#7-go--no-go-checklist-for-live-trading)
8. [Troubleshooting](#8-troubleshooting)
9. [Config Reference](#9-config-reference)

---

## 1. Prerequisites

### Environment

| Tool | Minimum version | Check |
|------|----------------|-------|
| Docker + Compose | 24.x + 2.24 | `docker compose version` |
| Node.js | 18+ | `node --version` |
| Python | 3.11 | `python3 --version` |
| kubectl (K8s only) | 1.28 | `kubectl version` |

### Alpaca Paper Account

1. Sign up at https://app.alpaca.markets (free)
2. Go to **Paper Trading** → API Keys → **Generate New Key**
3. Copy `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`
4. Confirm base URL is `https://paper-api.alpaca.markets` (never the live URL)

### Secrets

```bash
cp .env.example .env
# Fill in (minimum required for paper trading):
#   ALPACA_API_KEY=<your-paper-key>
#   ALPACA_SECRET_KEY=<your-paper-secret>
#   POSTGRES_PASSWORD=<choose-strong-password>
$EDITOR .env
```

> **NEVER** set `ALPACA_BASE_URL=https://api.alpaca.markets` — that is the live endpoint.
> The paper safety guard in `run_strategy.sh` will abort if it detects a non-paper URL.

---

## 2. Starting the Full Stack

### Option A — One command (recommended)

```bash
cd /home/kironix/workspace/QuantConnect.VS
source .env
bash ./run_strategy.sh paper-trading
```

This automatically:
1. Verifies `configs/paper_trading.yaml` environment guard
2. Starts Redis, Kafka, TimescaleDB, MLflow, Schema Registry
3. Waits 30 s for infrastructure to initialise
4. Runs health checks on infrastructure
5. Starts all APEX microservices (signal-provider, Prometheus, Grafana)
6. Prints a status panel with all service URLs

Add `--start-ui` to also launch the Next.js dashboard:

```bash
bash ./run_strategy.sh paper-trading --start-ui
```

### Option B — Manual step-by-step

```bash
source .env
export COMPOSE_FILE=infra/docker-compose.yml
export APEX_CONFIG=configs/paper_trading.yaml

# 1. Infrastructure
docker compose up -d redis kafka timescaledb mlflow schema-registry
sleep 30

# 2. Health check
./scripts/health_check.sh

# 3. APEX services
docker compose up -d signal-provider prometheus grafana

# 4. Tail logs
docker compose logs -f signal-provider
```

### What's running after startup

| Service | Port | Description |
|---------|------|-------------|
| Signal Provider API | 8000 | FastAPI — signals, kill-switch, positions |
| Grafana | 3000 | Metrics dashboards |
| MLflow | 5000 | Walk-forward fold history |
| Prometheus | 9090 | Metrics scraping / alerting |
| TimescaleDB | 5432 | Market data + trade records |
| Kafka | 9092 | Event bus |
| Redis | 6379 | State store (kill switch, weights) |
| APEX Dashboard (optional) | 3001 | Next.js trading UI |

---

## 3. Daily Monitoring Workflow

### Morning checklist (before 9:25 ET)

```bash
# 1. Confirm all services are up
./scripts/health_check.sh

# 2. Confirm kill switch is OFF
docker compose exec -T redis redis-cli get apex:kill_switch
# Expected: (nil) or 0

# 3. Confirm Alpaca paper account is funded
curl -s https://paper-api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  | python3 -m json.tool | grep -E '"equity"|"cash"'
```

### End-of-day report (after 16:05 ET)

```bash
python scripts/paper_trading_monitor.py
```

Output: human-readable summary + `logs/paper_trading/YYYY-MM-DD.json`

For historical dates:

```bash
python scripts/paper_trading_monitor.py --date 2026-02-27
```

Run silently — only print if there's an alert:

```bash
python scripts/paper_trading_monitor.py --alert-only
```

**Set up a cron job** (runs at 17:00 ET daily):

```bash
crontab -e
# Add:
0 22 * * 1-5 cd /home/kironix/workspace/QuantConnect.VS && \
  source .env && \
  python scripts/paper_trading_monitor.py --alert-only \
  >> logs/paper_trading/cron.log 2>&1
```

### Weekly review

```bash
# List all daily reports
ls -la logs/paper_trading/*.json

# Quick P&L summary across all reports
python3 - << 'EOF'
import json, glob, pathlib
reports = sorted(glob.glob("logs/paper_trading/2*.json"))
for p in reports[-7:]:  # last 7 days
    d = json.loads(pathlib.Path(p).read_text())
    acc  = d.get("account", {})
    tr   = d.get("trades",  {})
    print(f"{d['date']}  PnL={acc.get('pnl_today_pct',0):+.2%}  "
          f"WR={tr.get('win_rate','n/a')}  Alerts={len(d.get('alerts',[]))}")
EOF
```

---

## 4. Health Check Reference

```bash
./scripts/health_check.sh          # Docker Compose
./scripts/health_check.sh --k8s    # Kubernetes (apex namespace)
./scripts/health_check.sh --quiet  # Only print failures
```

### Checks performed

| Category | Check | Pass condition |
|----------|-------|---------------|
| Redis | PING | PONG received |
| Redis | AOF persistence | `appendonly yes` |
| Kafka | Broker reachable | Topics list returned |
| **Kafka** | **Consumer lag** | **All groups < 100 messages** |
| TimescaleDB | pg_isready | Accepting connections |
| TimescaleDB | Extension | timescaledb extension loaded |
| **TimescaleDB** | **Data freshness** | **Latest row < 5 min old** |
| MLflow | HTTP health | /health returns 200 |
| Microservices | Container status | All 7 services running |
| Redis | Kill switch | Key absent or = '0' |
| **Redis** | **Signal freshness** | **apex:last_signal_ts < 10 min** |
| K8s | Deployments | readyReplicas = spec.replicas |
| K8s | Sealed Secrets | All 4 secrets decrypted |

Bold rows are new checks added in Phase 05 (paper validation mode).

---

## 5. Kill Switch Protocol

The kill switch is a Redis key (`apex:kill_switch`) that the Risk Engine polls
every 5 seconds. When set to `"1"`, all pending orders are cancelled and no new
orders are submitted.

### Trigger the kill switch

```bash
# Immediately halt all trading
docker compose exec redis redis-cli set apex:kill_switch 1
```

Or via the API:

```bash
curl -X POST http://localhost:8000/kill-switch/enable
```

Or via the APEX Dashboard → Dashboard page → Kill Switch toggle.

### Resume trading

```bash
docker compose exec redis redis-cli set apex:kill_switch 0
# Or:
curl -X POST http://localhost:8000/kill-switch/disable
```

### Automatic triggers

The kill switch fires automatically within **5 seconds** if:

| Condition | Threshold |
|-----------|-----------|
| Daily P&L loss | −3% of NAV |
| Intraday drawdown | −6% from peak |
| Redis crash | Fail-closed (sets to ACTIVE) |

These thresholds are defined in `configs/paper_trading.yaml` under `risk.daily_loss`.

---

## 6. Metric Interpretation Guide

### Daily Monitor report fields

| Field | What it means | Healthy range |
|-------|--------------|---------------|
| `pnl_today_pct` | Day's P&L as % of NAV | > −3% |
| `win_rate` | % of round trips closed profitably | ≥ 50% over 30-day window |
| `avg_holding_minutes` | Average trade duration | 30–480 min (intraday OK) |
| `max_intraday_pct` | Worst peak-to-trough during day | < 6% |
| `ensemble_weights.drifts` | How far live weights drifted from baseline | < 15% per model |

### Signal confidence accuracy

After 30+ days of paper trading, compare `signal.confidence` to actual outcomes
to measure calibration:

```bash
python3 - << 'EOF'
import json, glob
reports = sorted(glob.glob("logs/paper_trading/2*.json"))
trips = []
for p in reports:
    for t in json.loads(open(p).read()).get("trades", {}).get("details", []):
        trips.append(t)
print(f"Total round trips: {len(trips)}")
wins = [t for t in trips if t.get("win")]
print(f"Overall win rate:  {len(wins)/len(trips):.1%}")
EOF
```

### Readiness thresholds for live trading

| Metric | Paper trading must show | Window |
|--------|------------------------|--------|
| Win rate | ≥ 52% | 30+ trade days |
| Daily loss breach count | 0 | 30 days |
| Sharpe ratio (annualised) | ≥ 1.2 | 30 days of daily P&L |
| Max intraday drawdown | ≤ 4% on worst day | 30 days |
| Kill switch auto-triggers | ≤ 1 | 30 days |
| Signal age breaches | 0 | 5 consecutive days |
| Kafka consumer lag > 100 | 0 | 5 consecutive days |

---

## 7. Go / No-Go Checklist for Live Trading

Complete ALL items before switching to live capital.

### Operational readiness

- [ ] 30 full trading days completed in paper mode without system restarts
- [ ] No daily loss limit breached in last 30 days
- [ ] Health check passes every morning for last 10 days (save screenshots)
- [ ] Kill switch tested manually — confirmed halts within 5 seconds
- [ ] Kill switch auto-trigger tested (simulate −3% loss, confirm it fires)
- [ ] `paper_trading_monitor.py` runs cleanly (exit 0) for last 10 consecutive days
- [ ] All Kafka consumer lags consistently < 100 messages
- [ ] All TimescaleDB tables have data consistently < 5 min old during market hours

### Statistical readiness

- [ ] Win rate ≥ 52% over ≥ 30 round trips
- [ ] Annualised Sharpe ≥ 1.2 (compute from daily P&L series)
- [ ] Maximum intraday drawdown ≤ 4% on any single day
- [ ] No day with > −2% P&L in last 20 days
- [ ] Ensemble weight drift < 15% over 30 days
- [ ] At least 2 full walk-forward folds completed and logged in MLflow

### Infrastructure readiness

- [ ] Sealed Secrets deployed and verified in K8s: `./scripts/seal_secret.sh verify`
- [ ] Prometheus `apex_pipeline_stale` alert confirmed working
- [ ] Grafana dashboard reviewed — no anomalies in signal latency panel
- [ ] Redis AOF confirmed enabled (`./scripts/health_check.sh`)
- [ ] Backup / disaster-recovery plan documented

### Business readiness

- [ ] Live Alpaca API keys generated (separate from paper keys)
- [ ] Live credentials sealed: `ALPACA_BASE_URL=https://api.alpaca.markets`
- [ ] Max position sizes reviewed — confirm 2% per symbol is still acceptable
- [ ] Emergency contact (person) identified who can trigger kill switch

### Final sign-off

> Sign print name and date when ready:  
> **Operator:** ______________________  **Date:** __________

---

## 8. Troubleshooting

### "No configuration file provided" on docker compose

```bash
export COMPOSE_FILE=infra/docker-compose.yml
```

Both `health_check.sh` and `verify_first_trade.sh` set this automatically.

### Kill switch stuck ACTIVE after restart

Redis AOF should persist the key across restarts. If it did not:

```bash
# Check AOF state
docker compose exec redis redis-cli config get appendonly
# If "no", enable it:
docker compose exec redis redis-cli config set appendonly yes
docker compose exec redis redis-cli config rewrite
```

### Signals stop flowing (apex:last_signal_ts > 10 min)

```bash
# 1. Check signal provider logs
docker compose logs --tail=50 signal-provider

# 2. Check Kafka topic for signals.scored
docker compose exec kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic paper.signals.scored --max-messages 3 --timeout-ms 5000

# 3. Check consumer lag
./scripts/health_check.sh
```

### TimescaleDB data older than 5 minutes

```bash
# Check data ingestion
docker compose logs --tail=30 data-ingestion 2>/dev/null || true

# Manually check latest market_data row
docker compose exec timescaledb psql -U apex -d apexdb \
  -c "SELECT MAX(time) FROM market_data;"
```

### paper_trading_monitor.py exits with code 2

Alpaca API unreachable. Check:
1. `.env` contains `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`
2. `source .env` was run in the current shell
3. The paper endpoint is reachable: `curl -I https://paper-api.alpaca.markets/v2/account`

---

## 9. Config Reference

### `configs/paper_trading.yaml` key settings

| Setting | Value | Notes |
|---------|-------|-------|
| `app.environment` | `paper` | Guards against accidental live trading |
| `universe.mode` | `whitelist` | Only top-20 QQQ tickers allowed |
| `risk.max_position_pct` | 2% | Per-symbol hard limit |
| `risk.portfolio.max_total_risk_pct` | 10% | Aggregate exposure cap |
| `risk.portfolio.max_open_positions` | 10 | Max concurrent positions |
| `risk.daily_loss.limit_pct` | 3% | Kill switch trigger level |
| `risk.daily_loss.check_interval_seconds` | 5 | How often the Risk Engine polls |
| `risk.daily_loss.alert_pct` | 2% | Warning level (80% of limit) |
| `risk.trade.stop_loss_pct` | 2% | Per-trade stop loss |
| `risk.trade.take_profit_pct` | 4% | Per-trade take profit |
| `risk.min_signal_confidence` | 60% | Signals below this are discarded |
| `trading_hours.open` | 09:30 ET | No signals before this time |
| `trading_hours.close` | 16:00 ET | No new signals after this time |
| `kafka.topics.*` | `paper.*` | Isolated from production topics |

> To change any risk parameter, edit `configs/paper_trading.yaml` and restart affected services:
> ```bash
> docker compose restart signal-provider
> ```
