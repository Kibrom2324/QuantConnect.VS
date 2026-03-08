# NEXT UP — Remaining Work

> Items below are NOT done. Check them off by moving to `docs/DONE.md` once committed.

---

## Current state (as of commit `860acb8`)

- `TRADING_ENABLED=true` in `infra/.env` (operator-set).
- `apex:kill_switch=true` in Redis (operator-set via `docker compose -f infra/docker-compose.yml exec redis redis-cli set apex:kill_switch true`).
- Net effect: execution engine will accept orders, but risk engine blocks them at the kill switch. **No orders can reach Alpaca right now.**

---

## Paper Trading — Monday Market Open

### Pre-market (before 09:30 ET / 13:30 UTC)

1. Verify infra is up:
   ```bash
   bash scripts/health_check.sh
   ```
2. Verify data freshness after market opens:
   ```bash
   bash scripts/market_hours_freshness.sh
   ```
3. Remove kill switch:
   ```bash
   docker compose -f infra/docker-compose.yml exec redis redis-cli del apex:kill_switch
   ```
4. Confirm `TRADING_ENABLED=true` in `infra/.env` (already set).
5. Restart execution engine to pick up env:
   ```bash
   docker compose -f infra/docker-compose.yml restart execution-engine
   ```

### First trade verification

6. Watch execution engine logs:
   ```bash
   docker compose -f infra/docker-compose.yml logs -f execution-engine
   ```
7. Run first-trade check:
   ```bash
   bash scripts/verify_first_trade.sh
   ```
8. Check decision records:
   ```bash
   docker compose -f infra/docker-compose.yml exec timescaledb \
     psql -U apex_user -d apex -c "SELECT * FROM decision_records ORDER BY created_at DESC LIMIT 5;"
   ```
9. If first paper trade succeeds → move this section to `docs/DONE.md`.

---

## Live Trading — NOT YET (requires all items below)

### Must-do before live

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Wire TFT inference into signal-engine | NOT STARTED | `tft-service` container runs but signal-engine doesn't call it. Need HTTP/gRPC client in `services/signal_engine/`. |
| 2 | Build XGB inference service | NOT STARTED | No service exists. Need `services/xgb_service/` with model loading from MLflow. |
| 3 | Validate ensemble with all 3 models | NOT STARTED | Currently factor_score gets 100% weight. TFT (0.35) + XGB (0.30) renormalize to 0. |
| 4 | Deploy `circuit_breaker` as Docker service | NOT STARTED | Script exists (`scripts/circuit_breaker.py`) but not containerized. Needs Dockerfile + compose entry. |
| 5 | Deploy `exit_monitor` service | NOT STARTED | Code exists but not in docker-compose. Needs service definition. |
| 6 | Run `scripts/go_live_validator.py` with live credentials | NOT STARTED | Requires live Alpaca API keys (not paper). |
| 7 | Switch Alpaca URL to live | NOT STARTED | Change `ALPACA_BASE_URL` from `paper-api.alpaca.markets` to `api.alpaca.markets` in `infra/.env`. |
| 8 | Set real Reddit credentials | NOT STARTED | `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are `placeholder` in `infra/.env`. |
| 9 | Stress-test with historical replay | NOT STARTED | `scripts/replay_harness.py` exists but hasn't been run against full pipeline. |

### Nice-to-have before live

| # | Task | Notes |
|---|------|-------|
| A | Grafana alert rules for drawdown / position limits | Grafana is up but no alert rules configured. |
| B | Automated daily feedback loop | `scripts/daily_feedback.py` exists, needs cron or systemd timer. |
| C | Schema Registry enforcement | Container defined but schemas not registered. |
| D | Kubernetes deployment | `deploy/k8s/` exists with manifests, untested. |
