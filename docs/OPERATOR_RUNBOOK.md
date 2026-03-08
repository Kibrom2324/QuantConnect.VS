# APEX Operator Runbook

**Generated:** 2026-03-08  
**Repository:** `/home/kironix/workspace/QuantConnect.VS`  
**Compose file:** `infra/docker-compose.yml`  
**Env file:** `infra/.env`  

> **Rule:** Every stage ends with a STOP/GO checkpoint.  
> Do NOT proceed to the next stage unless every check shows GO.  
> Every rollback command is listed inline — you never need to improvise under pressure.

---

## Pre-Requisites

```bash
cd /home/kironix/workspace/QuantConnect.VS
export COMPOSE_FILE=infra/docker-compose.yml
```

All `docker compose` commands below assume this working directory and COMPOSE_FILE.

---

## Stage 0 — Verify Baseline Infrastructure

### 0.1 Confirm all infrastructure containers are running

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null | sort
```

**Expected:** These 17 containers show `Up` (most with `(healthy)`):

| Container | Role |
|-----------|------|
| `apex-timescaledb` | TimescaleDB |
| `infra-redis-1` | Redis |
| `infra-kafka-1` | Kafka |
| `infra-prometheus-1` | Prometheus |
| `infra-grafana-1` | Grafana |
| `infra-mlflow-1` | MLflow |
| `apex-data-ingestion` | Market data ingest |
| `apex-feature-engineering` | Feature pipeline |
| `infra-signal-generator-1` | Signal generator |
| `infra-signal-engine-1` | Signal engine (scoring) |
| `infra-risk-engine-1` | Risk engine |
| `infra-execution-engine-1` | Order execution |
| `infra-model-monitor-1` | Model drift monitor |
| `apex-timesfm` | TimesFM service |
| `infra-monitoring-1` | Monitoring sidecar |
| `infra-position-reconciler-1` | Alpaca reconciler |
| `infra-backtester-1` | Backtester |

### 0.2 Verify individual subsystem health

```bash
# Redis
docker compose exec -T redis redis-cli ping
# Expected: PONG

# Kafka — list topics
docker exec infra-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --list
# Expected: apex.signals.raw, apex.signals.scored, apex.risk.approved, apex.orders.results, ...

# TimescaleDB
docker exec apex-timescaledb pg_isready -U apex_user -d apex
# Expected: accepting connections

docker exec apex-timescaledb psql -U apex_user -d apex -c \
  "SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';"
# Expected: timescaledb | 2.x.x

# MLflow
curl -sf http://localhost:5001/health && echo "OK"
# Expected: OK

# Prometheus
curl -sf http://localhost:9090/-/ready && echo "OK"
# Expected: OK

# Grafana
curl -sf http://localhost:3000/api/health | python3 -m json.tool
# Expected: {"commit":"...","database":"ok","version":"10.4.0"}
```

### 0.3 Verify DB schema and data

```bash
docker exec apex-timescaledb psql -U apex_user -d apex -At -c "
SELECT 'ohlcv_bars' as tbl, COUNT(*) FROM ohlcv_bars
UNION ALL SELECT 'features', COUNT(*) FROM features
UNION ALL SELECT 'signals', COUNT(*) FROM signals
UNION ALL SELECT 'orders', COUNT(*) FROM orders
UNION ALL SELECT 'positions', COUNT(*) FROM positions
UNION ALL SELECT 'decision_records', COUNT(*) FROM decision_records
UNION ALL SELECT 'calibration_snapshots', COUNT(*) FROM calibration_snapshots
UNION ALL SELECT 'portfolio_snapshots', COUNT(*) FROM portfolio_snapshots
ORDER BY 1;"
```

**Expected minimums:** ohlcv_bars > 500K, features > 500K, calibration_snapshots ≥ 5, decision_records ≥ 8.

### 0.4 Verify Redis calibration state

```bash
# Isotonic calibration curve loaded
docker exec infra-redis-1 redis-cli exists "apex:calibration:curve"
# Expected: 1

# Kill switch inactive
docker exec infra-redis-1 redis-cli get "apex:kill_switch"
# Expected: (nil)  — means OFF
```

### 0.5 Verify Kafka consumer groups — LAG must be 0

```bash
docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --all-groups 2>/dev/null \
  | grep "^apex-" | awk '{ printf "%-25s %-25s partition=%-3s lag=%s\n", $1, $2, $3, $6 }'
```

**Expected:** All `lag=0` for these three groups:(
- `apex-signal-engine-v1` on `apex.signals.raw`
- `apex-risk-engine-v1` on `apex.signals.scored`
- `apex-execution-v1` on `apex.risk.approved`

### STOP/GO Checkpoint 0

| Check | Criteria | Pass? |
|-------|----------|-------|
| All 17 containers running | `docker compose ps` shows `Up` | |
| Redis PONG | `redis-cli ping` returns PONG | |
| Kafka topics exist | ≥ 6 apex.* topics listed | |
| TimescaleDB ready + extension | pg_isready OK, timescaledb loaded | |
| MLflow health | curl :5001/health returns OK | |
| ohlcv_bars > 500K | count query | |
| Calibration curve in Redis | EXISTS = 1 | |
| Kill switch OFF | GET returns nil | |
| Consumer lag = 0 | All 3 active groups | |

**All checks pass → GO to Stage 1.**  
**Any check fails → STOP. Fix before continuing.**

---

## Stage 1 — Verify Signal Pipeline Flow

### 1.1 Confirm signal-generator is producing

```bash
docker compose logs --tail=10 signal-generator 2>/dev/null | grep -E "scan_complete|signal_generator_started"
```

**Expected:** `scan_complete` with `symbols_published=` ≥ 80.

### 1.2 Consume a raw signal from Kafka

```bash
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 \
  --topic apex.signals.raw \
  --max-messages 1 \
  --timeout-ms 90000
```

**Expected:** JSON with `{"symbol": "...", "factor_score": ..., "ts": "...", "source": "signal_generator"}`.

**If no message after 90s:** signal-generator may be stuck.

```bash
# Troubleshoot:
docker compose logs --tail=50 signal-generator
docker compose restart signal-generator
```

### 1.3 Consume a scored signal

```bash
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 \
  --topic apex.signals.scored \
  --max-messages 1 \
  --timeout-ms 90000
```

**Expected:** JSON with `score`, `direction`, `calibrated_prob`, `prediction_ids`.

### 1.4 Consume a risk-approved signal

```bash
docker exec infra-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 \
  --topic apex.risk.approved \
  --max-messages 1 \
  --timeout-ms 90000
```

**Expected:** JSON with risk fields: `position_size_pct`, `stop_loss`, `take_profit`.

### STOP/GO Checkpoint 1

| Check | Criteria | Pass? |
|-------|----------|-------|
| signal-generator running | `scan_complete` in logs | |
| Raw signals flowing | Consumed JSON from `apex.signals.raw` | |
| Scored signals flowing | Consumed JSON from `apex.signals.scored` | |
| Risk-approved flowing | Consumed JSON from `apex.risk.approved` | |

**All pass → GO to Stage 2.**

---

## Stage 2 — Run Tests & Health Check

### 2.1 Run full test suite

```bash
cd /home/kironix/workspace/QuantConnect.VS
.venv/bin/python -m pytest tests/ -v --tb=short
```

**Expected:** `323 passed, 0 failed`.

### 2.2 Run health check script

```bash
bash scripts/health_check.sh
```

**Expected:** All infrastructure and microservice checks pass. Data freshness warnings are expected outside market hours — these are informational, not blocking.

### 2.3 Run go-live validator (dry-run)

```bash
set -a && source infra/.env && set +a
.venv/bin/python scripts/go_live_validator.py
```

**Expected:** Some checks may fail (e.g., TRADING_ENABLED=false). This is a baseline. Note which checks pass and which fail.

### STOP/GO Checkpoint 2

| Check | Criteria | Pass? |
|-------|----------|-------|
| pytest 323/323 | 0 failures | |
| health_check.sh | Infrastructure + services all ✓ | |
| go_live_validator.py | Baseline captured | |

**All pass → GO to Stage 3.**

---

## Stage 3 — Start Safety Systems

### 3.1 Start circuit breaker daemon

```bash
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a
mkdir -p logs
nohup .venv/bin/python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &
echo "Circuit breaker PID: $!"
```

### 3.2 Verify circuit breaker is running

```bash
sleep 3 && tail -5 logs/circuit_breaker.log
```

**Expected:**
```
circuit_breaker  Circuit breaker starting — threshold=5.0%, interval=60s
circuit_breaker  Redis connected at localhost:6379
circuit_breaker  Drawdown monitor active — polling every 60s
httpx  HTTP Request: GET https://paper-api.alpaca.markets/v2/account "HTTP/1.1 200 OK"
circuit_breaker  poll=1  equity=$100xxx.xx  high=$100xxx.xx  drawdown=0.00%  threshold=5.0%
```

### 3.3 Verify safety flags in infra/.env

```bash
grep -E "TRADING_ENABLED|KILL_SWITCH|ALPACA_BASE_URL|ENABLE_ISOTONIC|ENABLE_PREDICTION_LINEAGE|ENABLE_DECISION_RECORDS" infra/.env
```

**Expected:**
```
TRADING_ENABLED=false          ← still off
KILL_SWITCH=false              ← not tripped
ALPACA_BASE_URL=https://paper-api.alpaca.markets  ← PAPER, not live
ENABLE_ISOTONIC_CALIBRATION=true
ENABLE_PREDICTION_LINEAGE=true
ENABLE_DECISION_RECORDS=true
```

### 3.4 Verify Alpaca account is paper (not live)

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
       -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
       https://paper-api.alpaca.markets/v2/account \
  | python3 -c "import sys,json; a=json.load(sys.stdin); print(f'Status: {a[\"status\"]}  Equity: \${float(a[\"equity\"]):,.2f}  Buying Power: \${float(a[\"buying_power\"]):,.2f}')"
```

**Expected:** `Status: ACTIVE  Equity: $100,xxx.xx  Buying Power: $xxx,xxx.xx`

### STOP/GO Checkpoint 3

| Check | Criteria | Pass? |
|-------|----------|-------|
| Circuit breaker running | PID active, logs show polling | |
| TRADING_ENABLED=false | grep confirms | |
| ALPACA_BASE_URL = paper | Not live URL | |
| Alpaca account ACTIVE | curl returns status=ACTIVE | |
| Kill switch OFF | Redis returns nil | |

**All pass → GO to Stage 4.**

---

## Stage 4 — Enable Paper Trading

> **THIS IS THE CRITICAL STEP.** After this, the system will place real orders on the Alpaca paper account.

### 4.1 Enable trading flag

```bash
cd /home/kironix/workspace/QuantConnect.VS

# Edit infra/.env
sed -i 's/^TRADING_ENABLED=false/TRADING_ENABLED=true/' infra/.env

# Verify the change
grep TRADING_ENABLED infra/.env
# Expected: TRADING_ENABLED=true
```

### 4.2 Restart execution engine to pick up new flag

```bash
cd infra
docker compose up -d execution-engine
```

### 4.3 Verify execution engine restarted with trading enabled

```bash
sleep 5
docker compose logs --tail=20 execution-engine 2>/dev/null | grep -i "trading\|started\|enabled"
```

**Expected:** Log line showing `trading_enabled=true` or `TRADING_ENABLED=true`.

### 4.4 ROLLBACK (if anything looks wrong)

```bash
# IMMEDIATE ROLLBACK — disable trading
sed -i 's/^TRADING_ENABLED=true/TRADING_ENABLED=false/' infra/.env
cd infra && docker compose up -d execution-engine

# EMERGENCY — activate kill switch (stops ALL order flow instantly)
docker exec infra-redis-1 redis-cli set "apex:kill_switch" "1"
```

### STOP/GO Checkpoint 4

| Check | Criteria | Pass? |
|-------|----------|-------|
| TRADING_ENABLED=true | grep confirms | |
| execution-engine restarted | Container shows `Up <seconds>` | |
| No error logs | `docker compose logs execution-engine` clean | |

**All pass → GO to Stage 5.**

---

## Stage 5 — Monitor First Trade Cycle

> **Timing:** Run this during US market hours (9:30-16:00 ET / 14:30-21:00 UTC).  
> Signals are generated every 60 seconds. Allow 2-5 minutes for a full cycle.

### 5.1 Watch execution engine logs live

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
docker compose logs -f execution-engine 2>/dev/null | head -100
# Press Ctrl+C when you see an order attempt or after 5 min
```

### 5.2 Run first-trade verification script

```bash
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a
bash scripts/verify_first_trade.sh
```

**Expected:** All 4 checks pass:
1. Kafka topics have messages flowing
2. Risk engine loaded limits
3. Signal reached execution
4. Alpaca accepted at least one order

### 5.3 Check Alpaca paper orders

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
       -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
       "https://paper-api.alpaca.markets/v2/orders?status=all&limit=5" \
  | python3 -m json.tool | head -40
```

**Expected:** One or more orders with `status` = `filled`, `new`, or `accepted`.

### 5.4 Check positions

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
       -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
       "https://paper-api.alpaca.markets/v2/positions" \
  | python3 -m json.tool
```

### 5.5 Check decision records grew in DB

```bash
docker exec apex-timescaledb psql -U apex_user -d apex -c \
  "SELECT COUNT(*) as total, MAX(timestamp) as latest FROM decision_records;"
```

**Expected:** Count increasing, latest timestamp within the last few minutes.

### 5.6 Check orders table

```bash
docker exec apex-timescaledb psql -U apex_user -d apex -c \
  "SELECT * FROM orders ORDER BY created_at DESC LIMIT 5;"
```

### 5.7 ROLLBACK — if orders look wrong

```bash
# 1. Kill switch — stops all new orders immediately
docker exec infra-redis-1 redis-cli set "apex:kill_switch" "1"

# 2. Close all positions via Alpaca (liquidate)
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "https://paper-api.alpaca.markets/v2/positions"

# 3. Cancel all open orders
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "https://paper-api.alpaca.markets/v2/orders"

# 4. Disable trading
sed -i 's/^TRADING_ENABLED=true/TRADING_ENABLED=false/' infra/.env
cd infra && docker compose up -d execution-engine
```

### STOP/GO Checkpoint 5

| Check | Criteria | Pass? |
|-------|----------|-------|
| verify_first_trade.sh | 4/4 checks pass | |
| Alpaca shows orders | At least 1 order | |
| decision_records growing | Count > previous | |
| No error logs | execution-engine logs clean | |
| Drawdown < 1% | Circuit breaker log shows drawdown OK | |

**All pass → GO to Stage 6.**

---

## Stage 6 — Daily Paper Trading Operations

### 6.1 Daily monitoring (run after market close, ~17:00 ET)

```bash
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a

# Paper trading daily report
.venv/bin/python scripts/paper_trading_monitor.py
# Writes JSON to logs/paper_trading/YYYY-MM-DD.json
# Exit 0 = healthy, Exit 1 = loss limit breached

# Daily intelligence brief
.venv/bin/python scripts/daily_intelligence_brief.py
# Produces Markdown scorecard

# Daily feedback loop (labels positions, refits calibrator)
.venv/bin/python scripts/daily_feedback.py
```

### 6.2 Daily health check

```bash
bash scripts/health_check.sh
```

### 6.3 Check circuit breaker still running

```bash
pgrep -af circuit_breaker.py
# If no output, restart it:
set -a && source infra/.env && set +a
nohup .venv/bin/python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &
```

### 6.4 Check consumer lag (should be 0 or near-0)

```bash
docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --all-groups 2>/dev/null \
  | awk '/^apex-/ { lag+=$6 } END { print "Total lag:", lag+0 }'
```

### 6.5 Check Grafana dashboards

Open in browser:
- **Main dashboard:** http://localhost:3000 (login: admin / admin)
- **Prometheus alerts:** http://localhost:9090/alerts

### 6.6 Weekly: check model freshness

```bash
docker compose logs --tail=20 model-monitor 2>/dev/null | grep -i "sharpe\|drift\|stale"
```

---

## Stage 7 — Paper Trading Graduation Criteria

Run this after **at least 5 full trading days** of paper trading:

### 7.1 Aggregate paper performance

```bash
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a
.venv/bin/python scripts/paper_trading_monitor.py
```

### 7.2 Check account equity trend

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
       -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
       "https://paper-api.alpaca.markets/v2/account" \
  | python3 -c "
import sys, json
a = json.load(sys.stdin)
equity = float(a['equity'])
initial = 100000.0
pnl_pct = (equity - initial) / initial * 100
print(f'Equity: \${equity:,.2f}')
print(f'P&L: {pnl_pct:+.2f}%')
print(f'Status: {a[\"status\"]}')
"
```

### 7.3 Check signal performance

```bash
.venv/bin/python scripts/signal_attribution_report.py
```

### 7.4 Graduation criteria (all must be true)

| # | Criterion | Target | Command to verify |
|---|-----------|--------|-------------------|
| 1 | Trading days completed | ≥ 5 | `ls logs/paper_trading/ \| wc -l` |
| 2 | No kill switch trips | 0 trips | `grep "KILL_SWITCH\|kill_switch.*1" logs/circuit_breaker.log \| wc -l` |
| 3 | Max drawdown | < 5% | Circuit breaker logs |
| 4 | Hit rate | ≥ 50% | signal_attribution_report output |
| 5 | Daily Sharpe | > 0 | paper_trading_monitor reports |
| 6 | No error spikes | < 1% error rate | Prometheus: `rate(http_requests_errors[5m])` |
| 7 | Consumer lag stable | P95 < 100 | Kafka consumer group lag history |
| 8 | Calibration Brier | < 0.30 | `docker exec apex-timescaledb psql -U apex_user -d apex -c "SELECT brier_score FROM calibration_snapshots ORDER BY snapshot_ts DESC LIMIT 1;"` |

### STOP/GO Checkpoint 7

**All 8 criteria met → GO to Stage 8 (Live Trading).**  
**Any criterion fails → STAY in paper trading. Investigate and fix first.**

---

## Stage 8 — Go Live (Future — Do NOT Run Until Stage 7 Passes)

### 8.1 Run go-live validator

```bash
set -a && source infra/.env && set +a
.venv/bin/python scripts/go_live_validator.py --strict
```

**Required:** Exit code 0 (GO).

### 8.2 Switch to live credentials

> ⚠️ **WARNING:** This will trade REAL MONEY. Triple-check everything.

```bash
# 1. Update infra/.env with live Alpaca credentials
#    ALPACA_API_KEY=<live_key>
#    ALPACA_SECRET_KEY=<live_secret>
#    ALPACA_BASE_URL=https://api.alpaca.markets   ← LIVE URL

# 2. Tighten risk limits — use live config
#    cp configs/live_trading.yaml configs/active_trading.yaml

# 3. Restart all services
cd infra && docker compose up -d

# 4. Restart circuit breaker with new env
pkill -f circuit_breaker.py
set -a && source infra/.env && set +a
nohup .venv/bin/python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &
```

### 8.3 Live rollback

```bash
# IMMEDIATE — kill switch
docker exec infra-redis-1 redis-cli set "apex:kill_switch" "1"

# Liquidate all positions
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "https://api.alpaca.markets/v2/positions"

# Cancel all orders
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "https://api.alpaca.markets/v2/orders"

# Disable trading
sed -i 's/^TRADING_ENABLED=true/TRADING_ENABLED=false/' infra/.env
cd infra && docker compose up -d execution-engine
```

---

## Appendix A — Service Port Map

| Port | Service | Protocol |
|------|---------|----------|
| 3000 | Grafana | HTTP |
| 3001 | APEX Dashboard | HTTP |
| 5001 | MLflow | HTTP |
| 8000 | Signal Provider | HTTP |
| 8007 | Signal Provider Svc | HTTP |
| 8008 | Risk Engine | HTTP |
| 8009 | TFT Service | HTTP |
| 8010 | TimesFM Service | HTTP |
| 8011 | Data Ingestion | HTTP |
| 8013 | Feature Engineering | HTTP |
| 8014 | Signal Engine | HTTP |
| 8015 | Execution Engine | HTTP |
| 8020 | Model Monitor | HTTP |
| 9090 | Prometheus | HTTP |
| 9092 | Kafka (external) | TCP |
| 9094 | Kafka (internal) | TCP |
| 9121 | Redis Exporter | HTTP |
| 15432 | TimescaleDB | TCP |
| 16379 | Redis | TCP |

## Appendix B — Kafka Topic Map

| Topic | Producer | Consumer |
|-------|----------|----------|
| `apex.signals.raw` | signal-generator | signal-engine (`apex-signal-engine-v1`) |
| `apex.signals.scored` | signal-engine | risk-engine (`apex-risk-engine-v1`) |
| `apex.risk.approved` | risk-engine | execution-engine (`apex-execution-v1`) |
| `apex.orders.results` | execution-engine | — |
| `apex.dlq` | any service | — (dead letter queue) |
| `apex.signals.sentiment` | social-kafka-publish | signal-engine |

## Appendix C — Emergency Procedures

### Kill Switch — Stop All Trading Instantly

```bash
docker exec infra-redis-1 redis-cli set "apex:kill_switch" "1"
```

**Effect:** Execution engine refuses all new orders. Existing positions remain open.

### Kill Switch — Resume Trading

```bash
# Only after investigation and root cause resolution:
docker exec infra-redis-1 redis-cli del "apex:kill_switch"
```

### Liquidate All Positions (Paper)

```bash
set -a && source infra/.env && set +a
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "$ALPACA_BASE_URL/v2/positions"
```

### Cancel All Open Orders

```bash
curl -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
               -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
               "$ALPACA_BASE_URL/v2/orders"
```

### Restart Entire Stack

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
docker compose down
docker compose up -d
# Wait 60s for all healthchecks to pass
sleep 60
docker compose ps
```

### Restart Single Service (e.g., signal-engine)

```bash
cd /home/kironix/workspace/QuantConnect.VS/infra
docker compose restart signal-engine
docker compose logs --tail=20 signal-engine
```

### Reset Circuit Breaker

```bash
# Kill old process
pkill -f circuit_breaker.py

# Restart
cd /home/kironix/workspace/QuantConnect.VS
set -a && source infra/.env && set +a
nohup .venv/bin/python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &
```

### Check Why No Orders Are Being Placed

```bash
# 1. Is trading enabled?
grep TRADING_ENABLED infra/.env

# 2. Is kill switch active?
docker exec infra-redis-1 redis-cli get "apex:kill_switch"

# 3. Is execution engine running?
docker compose ps execution-engine

# 4. Is signal pipeline flowing? (check consumer lag)
docker exec infra-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group apex-execution-v1

# 5. Are signals being vetoed? (check risk engine logs)
docker compose logs --tail=30 risk-engine | grep -i "veto\|reject\|block"

# 6. Is market open?
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
       -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
       "https://paper-api.alpaca.markets/v2/clock" | python3 -m json.tool
```

## Appendix D — Cron Schedule (Recommended)

Add to operator's crontab (`crontab -e`):

```cron
# Circuit breaker watchdog — restart if not running (every 5 min)
*/5 * * * * pgrep -f circuit_breaker.py || (cd /home/kironix/workspace/QuantConnect.VS && set -a && source infra/.env && set +a && nohup .venv/bin/python scripts/circuit_breaker.py >> logs/circuit_breaker.log 2>&1 &)

# Daily paper trading report — after market close (17:15 ET = 22:15 UTC)
15 22 * * 1-5 cd /home/kironix/workspace/QuantConnect.VS && set -a && source infra/.env && set +a && .venv/bin/python scripts/paper_trading_monitor.py >> logs/daily_monitor.log 2>&1

# Daily feedback loop — after report (17:30 ET = 22:30 UTC)
30 22 * * 1-5 cd /home/kironix/workspace/QuantConnect.VS && set -a && source infra/.env && set +a && .venv/bin/python scripts/daily_feedback.py >> logs/daily_feedback.log 2>&1

# Daily intelligence brief (17:45 ET = 22:45 UTC)
45 22 * * 1-5 cd /home/kironix/workspace/QuantConnect.VS && set -a && source infra/.env && set +a && .venv/bin/python scripts/daily_intelligence_brief.py >> logs/daily_brief.log 2>&1

# Health check — every 15 min during market hours (9:30-16:00 ET = 14:30-21:00 UTC)
*/15 14-20 * * 1-5 cd /home/kironix/workspace/QuantConnect.VS && bash scripts/health_check.sh >> logs/health_check.log 2>&1
```

## Appendix E — Git Commit Checkpoint

After completing each major stage, commit your state:

```bash
cd /home/kironix/workspace/QuantConnect.VS
git add -A
git commit -m "stage N complete: <description>"
git tag v0.5.N
git push origin main --tags
```

Current baseline: commit `d7dec7f`, tag `v0.5.0`.
