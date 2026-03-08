# APEX Go-Live Runbook

**Last updated: 2026-02-28**
**Applies to: Phase 06 — Live Trading Readiness**

This runbook covers everything needed to transition APEX from paper trading to
live capital: pre-flight validation, step-by-step startup, emergency halt
procedures, and credential rotation.

> **NEVER proceed to live trading without passing all 10 checks in
> `scripts/go_live_validator.py`.**

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Pre-Flight Checklist](#2-pre-flight-checklist)
3. [Step-by-Step Go-Live Commands](#3-step-by-step-go-live-commands)
4. [Monitoring During Live Session](#4-monitoring-during-live-session)
5. [How to Halt Trading Immediately](#5-how-to-halt-trading-immediately)
6. [Kill Switch Protocol](#6-kill-switch-protocol)
7. [How to Rotate Credentials Safely](#7-how-to-rotate-credentials-safely)
8. [Rollback to Paper Trading](#8-rollback-to-paper-trading)
9. [Incident Response](#9-incident-response)
10. [Config Reference](#10-config-reference)

---

## 1. Prerequisites

### Minimum requirements before going live

| Requirement | Where to verify |
|-------------|----------------|
| 30+ days of paper trading | `logs/paper_trading/` — all reports must exist |
| Win rate ≥ 52% | `python scripts/paper_trading_monitor.py --date YYYY-MM-DD` |
| Sharpe ratio ≥ 1.2 (OOS) | MLflow at `http://localhost:5000` |
| Zero daily loss breaches in last 30 days | grep loss alerts in `logs/paper_trading/` |
| Max drawdown ≤ 4% in any paper session | `logs/paper_trading/` summary |
| Live Alpaca account approved | https://app.alpaca.markets — Status: ACTIVE |
| Live API keys generated | Separate from paper keys |
| Polygon.io live plan active | https://polygon.io/dashboard |
| TimescaleDB `live` schema initialised | `psql -U apex -d apexdb -c '\dn'` |

### Live vs Paper key differences

| Item | Paper | Live |
|------|-------|------|
| Alpaca base URL | `https://paper-api.alpaca.markets` | `https://api.alpaca.markets` |
| API key prefix | Starts with `PK` | Different prefix |
| Alpaca account | Paper portfolio (simulated) | Real brokerage account |
| Real money at risk | **NO** | **YES** |

---

## 2. Pre-Flight Checklist

Complete every item before running go-live commands.

### A. Statistical validation (do this the day before)

- [ ] Pull the last 30 days of paper reports:
  ```bash
  ls logs/paper_trading/*.json | wc -l   # should be ≥ 30
  ```
- [ ] Confirm win rate ≥ 52%:
  ```bash
  python scripts/paper_trading_monitor.py | grep "win_rate"
  ```
- [ ] Confirm Sharpe ≥ 1.2 in MLflow walk-forward runs
- [ ] Confirm zero daily loss limit breaches:
  ```bash
  grep -l "loss_limit_breached.*true" logs/paper_trading/*.json && echo "FOUND BREACHES — DO NOT GO LIVE"
  ```
- [ ] Confirm max intraday drawdown ≤ 4% across all sessions

### B. Environment preparation (morning of go-live)

- [ ] Update `infra/.env` with live credentials:
  ```bash
  $EDITOR infra/.env
  # Set:
  #   ALPACA_API_KEY=<live-key>
  #   ALPACA_SECRET_KEY=<live-secret>
  #   ALPACA_BASE_URL=https://api.alpaca.markets
  ```
- [ ] Verify no paper values remain:
  ```bash
  grep "paper-api" infra/.env && echo "ERROR: still pointing at paper endpoint"
  ```
- [ ] Set `APEX_CONFIG` to live config:
  ```bash
  export APEX_CONFIG=configs/live_trading.yaml
  ```
- [ ] Confirm `live` schema exists in TimescaleDB:
  ```bash
  docker compose exec timescaledb psql -U apex -d apexdb \
    -c "SELECT schema_name FROM information_schema.schemata WHERE schema_name='live';"
  ```
  If missing, run:
  ```bash
  docker compose exec timescaledb psql -U apex -d apexdb \
    -f /docker-entrypoint-initdb.d/init_live_schema.sql
  ```

### C. Kill switch pre-check

- [ ] Confirm kill switch is OFF:
  ```bash
  docker compose exec redis redis-cli GET apex:kill_switch
  # Expected: (nil) or "false"
  ```
- [ ] If kill switch is ON from a previous session:
  ```bash
  # Read the metadata FIRST to understand why it was triggered
  docker compose exec redis redis-cli GET apex:kill_switch:metadata
  # Only reset after investigation:
  docker compose exec redis redis-cli SET apex:kill_switch false
  ```

### D. Run the validator (last step before go-live)

```bash
set -a && source infra/.env && set +a
python scripts/go_live_validator.py
```

**All 10 checks must show PASS (or WARN at most).  Any FAIL = NO-GO.**

For a strict pass (no WARNs allowed):
```bash
python scripts/go_live_validator.py --strict
```

Save the validator output:
```bash
python scripts/go_live_validator.py --json > logs/live_trading/validator_$(date +%Y%m%d).json
```

---

## 3. Step-by-Step Go-Live Commands

Run these in sequence.  Do not skip steps.

### Step 1 — Source credentials and set config

```bash
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a
export APEX_CONFIG=configs/live_trading.yaml
export COMPOSE_FILE=infra/docker-compose.yml
```

### Step 2 — Stop paper trading stack (if running)

```bash
docker compose down
# Verify all containers stopped:
docker compose ps
```

### Step 3 — Run go/no-go validator

```bash
python scripts/go_live_validator.py --strict
# Exit code must be 0 — if not, STOP HERE and fix the failures.
```

### Step 4 — Start infrastructure services

```bash
docker compose up -d redis kafka timescaledb mlflow
echo "Waiting 45 seconds for infrastructure to initialise..."
sleep 45
bash scripts/health_check.sh
# All infrastructure checks must pass before proceeding.
```

### Step 5 — Start circuit breaker (MANDATORY before any trading)

```bash
mkdir -p logs/live_trading
nohup python scripts/circuit_breaker.py \
  >> logs/live_trading/circuit_breaker.log 2>&1 &
echo $! > /tmp/circuit_breaker.pid
echo "Circuit breaker started (PID: $(cat /tmp/circuit_breaker.pid))"

# Verify it is running:
sleep 2
python scripts/go_live_validator.py --skip env_vars alpaca_credentials polygon_api \
  | grep circuit_breaker
```

### Step 6 — Start APEX microservices

```bash
docker compose up -d \
  data-ingestion \
  feature-engineering \
  model-inference \
  lean-alpha \
  signal-engine \
  risk-engine \
  execution \
  exit-monitor
```

### Step 7 — Verify signal pipeline is flowing

```bash
# Watch for first signals (allow 2–3 minutes after market open):
docker compose logs -f signal-engine risk-engine execution
```

Expected log pattern (healthy):
```
signal-engine   INFO  signal published: symbol=AAPL confidence=0.73
risk-engine     INFO  signal APPROVED: AAPL → risk.approved
execution       INFO  order submitted: AAPL BUY 10 @ market
```

### Step 8 — Verify first order reached Alpaca

```bash
bash scripts/verify_first_trade.sh
```

Or check directly in the Alpaca dashboard:
https://app.alpaca.markets/live-trading/orders

### Step 9 — Start dashboard (optional)

```bash
docker compose up -d apex-dashboard
# Dashboard: http://localhost:3001/dashboard
```

### Step 10 — Confirm monitoring is active

```bash
# Grafana: http://localhost:3000   (check apex_pipeline_stale alert is green)
# Prometheus: http://localhost:9090
# MLflow: http://localhost:5000
```

---

## 4. Monitoring During Live Session

### Daily routine

| Time (ET) | Action |
|-----------|--------|
| 09:15     | Check kill switch is OFF; review overnight alerts |
| 09:25     | Run `bash scripts/health_check.sh` |
| 09:30     | Market opens — watch first signal flow in logs |
| 10:00     | Verify at least one order placed (if signals > 0.70 confidence) |
| 12:00     | Midday P&L check via Alpaca dashboard |
| 15:30     | EOD flatten begins at 15:45 — watch execution logs |
| 17:00     | Run daily report: `python scripts/paper_trading_monitor.py` |
| 17:30     | Review report; note any alerts for next day |

### Key metrics to watch

| Metric | Healthy range | Action if outside range |
|--------|--------------|------------------------|
| Intraday drawdown | < 3% | Alert; monitor closely |
| Intraday drawdown | ≥ 5% | Circuit breaker fires automatically |
| Win rate (rolling 5d) | ≥ 48% | Review signal quality |
| Kafka consumer lag | < 50 messages | Check service logs |
| Signal age | < 5 min | Check model-inference service |
| Kill switch state | `false` / `(nil)` | Investigate immediately if `true` |

### Checking live P&L

```bash
# Via Alpaca API:
curl -s \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/account \
  | python3 -m json.tool | grep -E "equity|last_equity|unrealized_pl"
```

---

## 5. How to Halt Trading Immediately

There are three escalating levels of halt, in order of speed:

### Level 1 — Soft halt (stops new orders, keeps existing positions)

Set the kill switch in Redis:
```bash
docker compose exec redis redis-cli SET apex:kill_switch true
```

All APEX services check this key before submitting orders.  Takes effect
within 5 seconds.  **Existing positions are held** — they will be managed
by `exit-monitor` until their stop-loss or take-profit fires.

### Level 2 — Hard halt (stops new orders + flattens all positions)

```bash
# 1. Set kill switch
docker compose exec redis redis-cli SET apex:kill_switch true

# 2. Flatten all positions via Alpaca (cancel all open orders first)
curl -s -X DELETE \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/orders

# 3. Close all positions
curl -s -X DELETE \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/positions
```

### Level 3 — Full stack shutdown

```bash
# 1. Set kill switch
docker compose exec redis redis-cli SET apex:kill_switch true

# 2. Stop circuit breaker
kill $(cat /tmp/circuit_breaker.pid 2>/dev/null) 2>/dev/null || true

# 3. Stop all services
docker compose down

# 4. Verify no containers running
docker compose ps
```

### One-liner emergency halt

If you cannot access individual commands, this single command does all three:

```bash
docker compose exec redis redis-cli SET apex:kill_switch true && \
  curl -s -X DELETE \
    -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
    -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
    https://api.alpaca.markets/v2/orders && \
  docker compose down
```

---

## 6. Kill Switch Protocol

The kill switch is a **latching mechanism** — once set to `true`, it NEVER
resets automatically.  This is by design (CF-6 fix).

### Reading kill switch state

```bash
# State:
docker compose exec redis redis-cli GET apex:kill_switch

# Metadata (why it was triggered):
docker compose exec redis redis-cli GET apex:kill_switch:metadata
```

### Manual reset procedure

**Do not reset without completing the investigation checklist below.**

#### Investigation checklist

Before resetting, answer all of these:

- [ ] Why did the kill switch trigger?
  - [ ] Drawdown breach (circuit breaker)
  - [ ] Daily loss limit (risk engine)
  - [ ] Manual intervention
  - [ ] Unknown — investigate `apex:kill_switch:metadata`
- [ ] Is the root cause understood and documented?
- [ ] Are open positions reasonable?
- [ ] Has the market condition that caused the breach passed?
- [ ] Is the remaining session safe to trade? (time of day, volatility)

#### Reset command

Only after completing the checklist:

```bash
# Read metadata before resetting
docker compose exec redis redis-cli GET apex:kill_switch:metadata

# Reset
docker compose exec redis redis-cli SET apex:kill_switch false

# Verify
docker compose exec redis redis-cli GET apex:kill_switch
# Expected: "false"

# Log the reset (for audit trail)
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Kill switch manually reset by $(whoami)" \
  >> logs/live_trading/kill_switch_audit.log
```

### Kill switch states

| Redis value | Meaning | Trading allowed |
|-------------|---------|----------------|
| `(nil)` / not set | Never triggered | Yes |
| `"false"` | Manually reset (previously triggered) | Yes |
| `"true"` | ACTIVE — kill switch engaged | **NO** |

---

## 7. How to Rotate Credentials Safely

Credential rotation must be done with zero downtime.  Follow this order to
avoid an outage.

### Rotating Alpaca API keys

> Alpaca allows multiple active keys — generate the new key BEFORE revoking
> the old one.

**Step 1 — Generate new live keys**

1. Log in to https://app.alpaca.markets/live-trading
2. Go to **Account → API Keys → Generate New Key**
3. Save the new key and secret in a password manager immediately
4. Do **NOT** revoke the old key yet

**Step 2 — Test new keys**

```bash
# Test without affecting production:
ALPACA_API_KEY=<new-key> ALPACA_SECRET_KEY=<new-secret> \
  python scripts/go_live_validator.py --skip kill_switch timescaledb redis circuit_breaker live_config
# Verify: alpaca_credentials check shows PASS
```

**Step 3 — Update `infra/.env` and re-seal K8s secrets**

```bash
# Update infra/.env
$EDITOR infra/.env   # change ALPACA_API_KEY and ALPACA_SECRET_KEY

# If using Kubernetes:
./scripts/seal_secret.sh seal-all
kubectl apply -f deploy/k8s/base/sealed-secret-apex-generated.yaml
```

**Step 4 — Rolling restart (zero-downtime)**

For Docker Compose:
```bash
set -a && source infra/.env && set +a
docker compose up -d --no-deps execution exit-monitor lean-alpha
```

For Kubernetes:
```bash
kubectl -n apex rollout restart deployment/apex-execution deployment/apex-exit-monitor
kubectl -n apex rollout status deployment/apex-execution
```

**Step 5 — Verify and revoke old key**

```bash
# Confirm new keys are active in logs:
docker compose logs execution | grep "Alpaca client initialised"

# Now revoke the old key via Alpaca dashboard
```

### Rotating TimescaleDB password

```bash
# 1. Set new password in infra/.env:
$EDITOR infra/.env   # change TIMESCALEDB_PASSWORD and DATABASE_URL

# 2. Update the password in PostgreSQL:
docker compose exec timescaledb psql -U apex -d apexdb \
  -c "ALTER USER apex PASSWORD '<new-password>';"

# 3. Restart affected services:
set -a && source infra/.env && set +a
docker compose up -d --no-deps data-ingestion feature-engineering risk-engine

# 4. If K8s: seal and apply
./scripts/seal_secret.sh seal-all
kubectl apply -f deploy/k8s/base/sealed-secret-apex-generated.yaml
kubectl -n apex rollout restart deployment
```

### Rotating Polygon.io API key

```bash
# 1. Update infra/.env
$EDITOR infra/.env   # change POLYGON_API_KEY

# 2. Restart data-ingestion only (only service that uses Polygon)
set -a && source infra/.env && set +a
docker compose up -d --no-deps data-ingestion

# 3. Verify in logs:
docker compose logs data-ingestion | grep "Polygon"
```

### Post-rotation validation

Always run the validator after any credential rotation:

```bash
set -a && source infra/.env && set +a
python scripts/go_live_validator.py
```

---

## 8. Rollback to Paper Trading

If live trading is not performing as expected and you need to roll back:

```bash
# 1. Halt all live trading (Level 3)
docker compose exec redis redis-cli SET apex:kill_switch true
docker compose down

# 2. Flatten all live positions (if any remain)
set -a && source infra/.env && set +a
curl -s -X DELETE \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/positions

# 3. Switch back to paper credentials in infra/.env
$EDITOR infra/.env
# Set:
#   ALPACA_API_KEY=<paper-key>
#   ALPACA_SECRET_KEY=<paper-secret>
#   ALPACA_BASE_URL=https://paper-api.alpaca.markets
#   APEX_CONFIG=configs/paper_trading.yaml

# 4. Start paper stack
set -a && source infra/.env && set +a
export APEX_CONFIG=configs/paper_trading.yaml
bash ./run_strategy.sh paper-trading

# 5. Reset kill switch (paper trading uses the same Redis key)
docker compose exec redis redis-cli SET apex:kill_switch false
```

---

## 9. Incident Response

### Scenario: Unexpected large loss

1. **Immediately**: halt all trading (Level 1 kill switch)
2. **Assess**: log into Alpaca dashboard and review all recent fills
3. **Diagnose**: check signal-engine and risk-engine logs for the time of the loss
4. **Document**: write an incident report in `logs/live_trading/incidents/`
5. **Root cause**: identify which signal / model generated the losing trade
6. **Fix**: do NOT re-enable live trading until root cause is fixed and tested
7. **Paper test**: run the fix in paper mode for ≥ 5 trading days
8. **Go-live again**: re-run full go-live procedure from Step 1

### Scenario: Services crash mid-session

```bash
# 1. Check which service died:
docker compose ps

# 2. View its last logs:
docker compose logs --tail=100 <service-name>

# 3. Restart the failed service only:
docker compose up -d --no-deps <service-name>

# 4. Verify pipeline is flowing again:
docker compose logs -f signal-engine | head -20
```

### Scenario: Redis is unreachable

This is a **critical failure** — the kill switch cannot be written.

```bash
# 1. Immediately set kill switch via env var as fallback:
export KILL_SWITCH=true
docker compose up -d --no-deps execution   # restart execution with env var

# 2. Diagnose Redis:
docker compose logs redis | tail -50
docker compose restart redis
sleep 10

# 3. Verify Redis is back:
docker compose exec redis redis-cli ping

# 4. Restore kill switch state in Redis:
docker compose exec redis redis-cli SET apex:kill_switch false   # or true if halted

# 5. Restart circuit breaker:
kill $(cat /tmp/circuit_breaker.pid 2>/dev/null)
nohup python scripts/circuit_breaker.py >> logs/live_trading/circuit_breaker.log 2>&1 &
echo $! > /tmp/circuit_breaker.pid
```

### Scenario: Circuit breaker fires automatically

The circuit breaker sets the kill switch when drawdown ≥ 5%.

```bash
# 1. Confirm it fired:
docker compose exec redis redis-cli GET apex:kill_switch
# Expected: "true"

# 2. Read why:
docker compose exec redis redis-cli GET apex:kill_switch:metadata

# 3. View circuit breaker log:
tail -50 logs/live_trading/circuit_breaker.log

# 4. Check current positions:
curl -s \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/positions | python3 -m json.tool

# 5. Decide: flatten positions or let stops manage exit
# 6. After investigation, follow Kill Switch Protocol (§6) to reset
```

---

## 10. Config Reference

### Key differences: paper vs live

| Parameter | `paper_trading.yaml` | `live_trading.yaml` |
|-----------|---------------------|---------------------|
| `environment` | `paper` | `live` |
| `max_position_pct` | `0.02` (2%) | `0.01` (1%) |
| `daily_loss.limit_pct` | `0.03` (3%) | `0.02` (2%) |
| `max_open_positions` | `10` | `8` |
| `min_signal_confidence` | `0.60` | `0.70` |
| `drawdown.max_drawdown_pct` | `0.06` (6%) | `0.05` (5%) |
| `eod_flatten` | `false` | `true` |
| `max_holding_days` | `5` | `3` |
| `trailing_stop_pct` | `0.03` | `0.02` |
| `stop_loss_pct` | `0.02` | `0.015` |
| `take_profit_pct` | `0.04` | `0.03` |
| Kafka topic prefix | `paper.` | _(none — canonical)_ |
| TimescaleDB schema | `paper` | `live` |
| Redis key prefix | `apex:paper:` | `apex:live:` |

### Script reference

| Script | Purpose | When to run |
|--------|---------|-------------|
| `scripts/go_live_validator.py` | Pre-flight GO/NO-GO | Before every live session start |
| `scripts/circuit_breaker.py` | Drawdown monitor daemon | Running always during live trading |
| `scripts/paper_trading_monitor.py` | Daily P&L report | After market close (17:00 ET) |
| `scripts/health_check.sh` | Infrastructure health | Before market open (09:25 ET) |
| `scripts/verify_first_trade.sh` | Confirm first order | After 09:35 ET on first day |

### Useful one-liners

```bash
# Current kill switch state
docker compose exec redis redis-cli GET apex:kill_switch

# Kill switch engagement metadata
docker compose exec redis redis-cli GET apex:kill_switch:metadata

# Circuit breaker process status
pgrep -fa circuit_breaker.py

# Live account equity
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/account | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'equity=${float(d[\"equity\"]):,.2f}  last_equity=${float(d[\"last_equity\"]):,.2f}')"

# Open positions
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/positions | python3 -m json.tool

# Flatten all positions immediately
curl -s -X DELETE \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  https://api.alpaca.markets/v2/positions

# Today's fills
curl -s \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  "https://api.alpaca.markets/v2/account/activities/FILL?after=$(date -u +%Y-%m-%d)T00:00:00Z" \
  | python3 -m json.tool
```
