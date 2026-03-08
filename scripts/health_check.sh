#!/usr/bin/env bash
# ─── scripts/health_check.sh ──────────────────────────────────────────────────
# APEX Platform Health Check
#
# Verifies all 8 services are running and their key subsystems are healthy.
# Exit code 0 = fully healthy, non-zero = at least one check failed.
#
# Usage:
#   bash scripts/health_check.sh              — check docker-compose services
#   bash scripts/health_check.sh --k8s        — check Kubernetes pods (apex namespace)
#   bash scripts/health_check.sh --quiet      — only print failures
# ──────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# Load canonical paths (repo root, compose file, service names, etc.)
source "$(dirname "$0")/lib/paths.sh"
export COMPOSE_FILE

MODE="compose"
QUIET=false
FAILED=0

for arg in "$@"; do
    case "$arg" in
        --k8s)   MODE="k8s"   ;;
        --quiet) QUIET=true   ;;
    esac
done

# ─── Formatting ───────────────────────────────────────────────────────────────

pass() { $QUIET || printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
fail() { FAILED=$((FAILED+1)); printf '  \033[0;31m✗\033[0m %s\n' "$*"; }
hdr()  { $QUIET || printf '\n\033[0;36m%s\033[0m\n' "$*"; }

# ─── Docker Compose checks ────────────────────────────────────────────────────

check_compose() {
    hdr "── Infrastructure Services ──────────────────────────────────────────"

    # Redis
    if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
        pass "Redis: PONG received"
    else
        fail "Redis: no PONG — check container logs: docker compose logs redis"
    fi

    # Redis AOF enabled
    if docker compose exec -T redis redis-cli config get appendonly 2>/dev/null | grep -q yes; then
        pass "Redis: AOF persistence ENABLED"
    else
        fail "Redis: AOF not enabled — data will not survive restart"
    fi

    # Kafka — attempt to list topics
    if docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh \
            --bootstrap-server localhost:9092 --list 2>/dev/null | grep -q .; then
        pass "Kafka: broker reachable, topics listed"
    elif docker compose exec -T kafka kafka-topics.sh \
            --bootstrap-server localhost:9092 --list 2>/dev/null | grep -q .; then
        pass "Kafka: broker reachable (fallback path)"
    else
        fail "Kafka: cannot reach broker — check: docker compose logs kafka"
    fi

    # TimescaleDB
    if docker compose exec -T timescaledb pg_isready -U apex_user -d apex 2>/dev/null | grep -q "accepting"; then
        pass "TimescaleDB: accepting connections"
    else
        fail "TimescaleDB: not ready — check: docker compose logs timescaledb"
    fi

    # TimescaleDB — verify extension active
    if docker compose exec -T timescaledb psql -U apex_user -d apex -c \
            "SELECT extname FROM pg_extension WHERE extname='timescaledb';" 2>/dev/null | grep -q timescaledb; then
        pass "TimescaleDB: extension loaded"
    else
        fail "TimescaleDB: extension not loaded — init.sql may not have run"
    fi

    # MLflow
    if curl -sf http://localhost:5001/health 2>/dev/null | grep -q .; then
        pass "MLflow: /health endpoint OK"
    else
        fail "MLflow: not responding on port 5001 — check: docker compose logs mlflow"
    fi

    hdr "── APEX Microservices ────────────────────────────────────────────────"

    SERVICES=(
        "apex-risk-engine"
        "apex-signal-engine"
        "apex-signal-generator"
        "apex-execution-engine"
        "apex-data_ingestion"
        "apex-feature_engineering"
    )

    for svc in "${SERVICES[@]}"; do
        # Try both <project>_<service>_1 and docker compose ps naming
        short="${svc#apex-}"
        status=$(docker compose ps --status running "$short" 2>/dev/null | tail -n +2 | head -1)
        if [[ -n "$status" ]]; then
            pass "${svc}: running"
        else
            fail "${svc}: NOT running — start: docker compose up -d $short"
        fi
    done

    # Kill-switch must be OFF (key absent or = '0')
    ks=$(docker compose exec -T redis redis-cli get "apex:kill_switch" 2>/dev/null)
    if [[ "$ks" == "1" ]]; then
        fail "Kill switch: ACTIVE — all trading is blocked! Run: docker compose exec redis redis-cli set apex:kill_switch 0"
    else
        pass "Kill switch: inactive (trading enabled)"
    fi

    # ─── Kafka consumer lag ───────────────────────────────────────────────────
    hdr "── Kafka Consumer Lag ───────────────────────────────────────────────"

    CONSUMER_GROUPS=(
        "apex-execution-v1"
        "apex-risk-engine-v1"
        "apex-signal-engine-v1"
    )
    for grp in "${CONSUMER_GROUPS[@]}"; do
        lag=$(docker compose exec -T kafka \
            /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:9092 \
            --describe --group "$grp" 2>/dev/null \
            | awk 'NR>1 && $6~/^[0-9]+$/ { sum += $6 } END { print sum+0 }')
        if [[ -z "$lag" ]]; then
            pass "Consumer group $grp: lag unknown (group may be inactive)"
        elif [[ "$lag" -gt 100 ]]; then
            fail "Consumer group $grp: lag=${lag} EXCEEDS threshold of 100 messages"
        else
            pass "Consumer group $grp: lag=${lag} (within threshold)"
        fi
    done

    # ─── TimescaleDB data freshness (last 5 min) ──────────────────────────────
    hdr "── TimescaleDB Data Freshness ───────────────────────────────────────"

    declare -A TABLE_TS_COL=( ["ohlcv_bars"]="time" ["features"]="time" ["decision_records"]="timestamp" )
    for tbl in "${!TABLE_TS_COL[@]}"; do
        col="${TABLE_TS_COL[$tbl]}"
        # Returns seconds since latest row; 99999 if table is empty / missing
        age=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
            -c "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX($col)))::int, 99999)
                FROM $tbl;" 2>/dev/null || echo "99999")
        age="${age//[^0-9]/}"   # strip whitespace
        age="${age:-99999}"
        if [[ "$age" -le 300 ]]; then
            pass "TimescaleDB.$tbl: last row ${age}s ago (< 5 min)"
        elif [[ "$age" -le 600 ]]; then
            fail "TimescaleDB.$tbl: last row ${age}s ago (> 5 min — stale data)"
        else
            fail "TimescaleDB.$tbl: no recent data (age=${age}s or table missing)"
        fi
    done

    # ─── Signal freshness ─────────────────────────────────────────────────────
    hdr "── Signal Freshness ─────────────────────────────────────────────────"

    sig_ts=$(docker compose exec -T redis redis-cli get "apex:signal_engine:last_signal_ts" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$sig_ts" || "$sig_ts" == "nil" ]]; then
        fail "Last signal timestamp: not found in Redis (key apex:signal_engine:last_signal_ts)"
    else
        now_epoch=$(date +%s)
        sig_epoch=$(date -d "$sig_ts" +%s 2>/dev/null || echo 0)
        sig_age=$(( now_epoch - sig_epoch ))
        if [[ "$sig_age" -le 600 ]]; then
            pass "Last signal: ${sig_age}s ago (< 10 min) — pipeline flowing"
        else
            fail "Last signal: ${sig_age}s ago — pipeline may be stalled (threshold 10 min)"
        fi
    fi
}

# ─── Kubernetes checks ────────────────────────────────────────────────────────

check_k8s() {
    NS="apex"
    hdr "── Kubernetes Namespace: $NS ─────────────────────────────────────────"

    # Namespace exists
    if kubectl get namespace "$NS" &>/dev/null; then
        pass "Namespace '$NS' exists"
    else
        fail "Namespace '$NS' not found — run: kubectl apply -f deploy/k8s/base/namespace.yaml"
        return
    fi

    # Check each deployment's ready replicas
    DEPLOYMENTS=(
        risk-engine signal-engine lean-alpha execution
        data-ingestion feature-engineering exit-monitor mlflow
    )
    for dep in "${DEPLOYMENTS[@]}"; do
        ready=$(kubectl -n "$NS" get deployment "$dep" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
        desired=$(kubectl -n "$NS" get deployment "$dep" -o jsonpath='{.spec.replicas}' 2>/dev/null)
        if [[ "$ready" == "$desired" && -n "$ready" ]]; then
            pass "${dep}: ${ready}/${desired} replicas ready"
        else
            fail "${dep}: ${ready:-0}/${desired:-?} replicas ready — check: kubectl -n $NS describe deployment $dep"
        fi
    done

    # SealedSecrets
    hdr "── Sealed Secrets ────────────────────────────────────────────────────"
    for sec in apex-alpaca-secret apex-db-secret apex-api-secret apex-redis-secret; do
        if kubectl -n "$NS" get secret "$sec" &>/dev/null; then
            pass "Secret '$sec' exists (decrypted by controller)"
        else
            fail "Secret '$sec' not found — run: ./scripts/seal_secret.sh seal-all && kubectl apply -f deploy/k8s/base/sealed-secret-apex-generated.yaml"
        fi
    done
}

# ─── Dispatch ─────────────────────────────────────────────────────────────────

if [[ "$MODE" == "k8s" ]]; then
    check_k8s
else
    check_compose
fi

hdr "── Summary ───────────────────────────────────────────────────────────────"
if [[ $FAILED -eq 0 ]]; then
    printf '\033[0;32m  All checks passed — system is healthy.\033[0m\n\n'
    exit 0
else
    printf '\033[0;31m  %d check(s) FAILED — see output above.\033[0m\n\n' "$FAILED"
    exit 1
fi
