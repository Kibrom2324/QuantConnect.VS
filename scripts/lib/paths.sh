#!/usr/bin/env bash
# scripts/lib/paths.sh — Canonical path definitions for APEX scripts.
# Source this from any script:  source "$(dirname "$0")/lib/paths.sh"
#
# This file is the SINGLE SOURCE OF TRUTH for paths referenced by
# operator scripts. See docs/REPO_GUARDRAILS.md for the full list.
#
# NOTE: Does NOT set shell options (set -e/-u/-o pipefail).
#       Each consuming script controls its own error handling.

# ── Repo root ───────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ── Key paths ───────────────────────────────────────────────────────
COMPOSE_FILE="infra/docker-compose.yml"
ENV_FILE="infra/.env"
SCRIPTS_DIR="scripts"
DOCS_DIR="docs"

# ── Convenience wrappers ────────────────────────────────────────────
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }
load_env() { set -a && source "$ENV_FILE" && set +a; }

# ── Service names (match docker-compose.yml exactly) ────────────────
SVC_REDIS="redis"
SVC_KAFKA="kafka"
SVC_TIMESCALEDB="timescaledb"
SVC_MLFLOW="mlflow"
SVC_SIGNAL_GENERATOR="signal-generator"
SVC_SIGNAL_ENGINE="signal-engine"
SVC_RISK_ENGINE="risk-engine"
SVC_EXECUTION_ENGINE="execution-engine"
SVC_DATA_INGESTION="data_ingestion"
SVC_FEATURE_ENGINEERING="feature_engineering"

# ── Kafka topics ────────────────────────────────────────────────────
TOPIC_SIGNALS_RAW="apex.signals.raw"
TOPIC_SIGNALS_SCORED="apex.signals.scored"
TOPIC_RISK_APPROVED="apex.risk.approved"
TOPIC_ORDERS_RESULTS="apex.orders.results"
TOPIC_DLQ="apex.dlq"

# ── Redis keys ──────────────────────────────────────────────────────
REDIS_KILL_SWITCH="apex:kill_switch"
REDIS_CALIBRATION="apex:calibration:curve"
REDIS_LAST_SIGNAL_TS="apex:signal_engine:last_signal_ts"

# ── Database ────────────────────────────────────────────────────────
DB_HOST="localhost"
DB_PORT="15432"
DB_NAME="apex"
DB_USER="apex_user"
DB_PASS="apex_pass"
