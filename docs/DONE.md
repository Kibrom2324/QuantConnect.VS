# DONE — Completed Work

> Items below are finished, tested, and committed. Do not re-do them.
> Each entry links to the commit that landed the change.

---

## v0.5.0 — Isotonic Calibration Cutover (`d7dec7f`)

- Isotonic regression calibrator loads from Redis (`apex:calibration:curve`).
- Fallback to MLflow artifact if Redis key missing.
- Rollback: delete Redis key → automatic fallback.
- Signal lineage proven end-to-end.
- Ensemble weights: factor_score 0.35, TFT 0.35, XGB 0.30 (TFT/XGB renormalize to 0 since no inference services are wired).
- 68 tests passing at this tag.

## TRADING_ENABLED Safety Guard (`9df72ad`)

- Execution engine (`services/execution/main.py`) checks `TRADING_ENABLED` env var on every Kafka message.
- When `false`: logs `trading_disabled_skipping_order`, writes decision record with reason `TRADING_ENABLED=false`, does NOT call Alpaca.
- `TRADING_ENABLED` env var plumbed through `infra/docker-compose.yml` to execution-engine service.
- `scripts/health_check.sh` Redis key fixed: `apex:last_signal_ts` → `apex:signal_engine:last_signal_ts`.
- Veto test passed: 9 decision records, 0 orders, 0 Alpaca calls.

## Paper-Trading Readiness Scripts (`317b03e`)

- `scripts/market_hours_freshness.sh` — checks data staleness vs market hours.
- `scripts/paper_ready_report.sh` — runs 28 checks, produces GO / NO-GO verdict.
- Paper trading verdict: **GO** (25 pass, 3 warn, 0 fail).
- Warnings are expected (weekend data staleness, no recent orders, TFT/XGB unwired).

## Script Portability (`dad77f2`)

- All 4 scripts auto-detect repo root via `REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"`.
- `scripts/verify_first_trade.sh` fixed: correct `.env` path (`infra/.env`), correct Kafka topic (`apex.orders.results`), correct service name (`execution-engine`).
- Kill switch reset to `false` in `infra/.env`.

## Operator Command Fixes (`860acb8`)

- 13 files audited and corrected:
  - `infra/docker-compose.yml` — 9 error messages now reference `infra/.env`.
  - `shared/core/env.py` — developer error message fixed.
  - `services/attribution/tracker.py` — error message fixed.
  - `docs/GO_LIVE_PLAN.md` — 5 `.env` → `infra/.env`.
  - `docs/PRODUCTION_DEPLOYMENT_PLAN.md` — 7 docker compose + .env fixes.
  - `docs/OPERATOR_RUNBOOK.md` — `.env` → `infra/.env`.
  - `docs/README_report.md` — `source .env` → `set -a && source infra/.env && set +a`.
  - `docs/PAPER_TRADING_RUNBOOK.md` — `.env`, `source .env`, `./scripts/` all fixed.
  - `docs/GO_LIVE_RUNBOOK.md` — 13 fixes.
  - All 4 scripts — usage comments corrected.
- Every operator command now works from repo root with no edits.

## Signal Generator Dockerized

- `services/signal_generator/Dockerfile` and `requirements.txt` created.
- Added to `infra/docker-compose.yml` as `signal-generator`.
- Produces 93 symbols per scan to `apex.signals.raw`.
- Container running: `infra-signal-generator-1`.

## Infrastructure Verified

- 17 services running (28+ hours uptime on core infra).
- Kafka consumer lag = 0 on all groups.
- TimescaleDB: `ohlcv_bars` populated.
- Redis: kill switch functional.
- MLflow: experiment `apex_ensemble`, 1 finished run (ENS_v4).
- 323 tests passing, zero failures.
