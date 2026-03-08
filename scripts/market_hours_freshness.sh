#!/usr/bin/env bash
# ─── scripts/market_hours_freshness.sh ─────────────────────────────────────────
# APEX — Market-Hours Data Freshness Validator
#
# Checks that all pipeline stages have fresh data during market hours.
# Designed to be run ONLY during weekday market hours (Mon-Fri, 09:30-16:00 ET).
# On weekends or outside market hours, reports expected staleness.
#
# Usage:
#   bash scripts/market_hours_freshness.sh           # from repo root
#   bash scripts/market_hours_freshness.sh --force   # run checks even outside market hours
# ──────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# Load canonical paths (repo root, compose file, service names, etc.)
source "$(dirname "$0")/lib/paths.sh"
export COMPOSE_FILE

FORCE=false
FAILED=0
WARNED=0

for arg in "$@"; do
    [[ "$arg" == "--force" ]] && FORCE=true
done

pass() { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
fail() { FAILED=$((FAILED+1)); printf '  \033[0;31m✗\033[0m %s\n' "$*"; }
warn() { WARNED=$((WARNED+1)); printf '  \033[0;33m⚠\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[0;36m%s\033[0m\n' "$*"; }

# ─── Market hours check ──────────────────────────────────────────────────────
NOW_UTC=$(date -u +%s)
DOW=$(date -u +%u)  # 1=Mon .. 7=Sun
HOUR_UTC=$(date -u +%H)

# US market hours in UTC: approximately 13:30-20:00 (EDT) or 14:30-21:00 (EST)
# Use conservative window: 13-21 UTC covers both DST and standard time
IS_WEEKDAY=$(( DOW <= 5 ? 1 : 0 ))
IS_MARKET_HOURS=$(( HOUR_UTC >= 13 && HOUR_UTC <= 21 ? 1 : 0 ))
IS_MARKET_OPEN=$(( IS_WEEKDAY && IS_MARKET_HOURS ? 1 : 0 ))

hdr "── APEX Market-Hours Freshness Check ──────────────────────────────"
echo "  Time (UTC):     $(date -u '+%Y-%m-%d %H:%M:%S')"
echo "  Day of week:    $(date -u '+%A') (DOW=$DOW)"
echo "  Market open:    $([ $IS_MARKET_OPEN -eq 1 ] && echo 'YES' || echo 'NO')"
echo ""

if [[ $IS_MARKET_OPEN -eq 0 && "$FORCE" != "true" ]]; then
    warn "Market is CLOSED — data staleness is expected."
    warn "Run with --force to check freshness anyway."
    echo ""
    echo "  Next expected data flow: Monday 13:00 UTC (data_ingestion poll_latest_bars)"
    echo ""
    # Still run the checks but report as warnings, not failures
fi

# Thresholds (seconds)
OHLCV_THRESHOLD=900       # 15 min — data_ingestion polls every 15 min
FEATURES_THRESHOLD=900    # 15 min — feature_engineering triggers from Redis pub/sub
SIGNAL_THRESHOLD=300      # 5 min  — signal_generator scans every 60s
DECISION_THRESHOLD=600    # 10 min — execution may not produce if no risk-approved signals

# ─── ohlcv_bars freshness ────────────────────────────────────────────────────
hdr "── ohlcv_bars (data_ingestion → TimescaleDB) ──────────────────────"

OHLCV_AGE=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX(time)))::int, 99999) FROM ohlcv_bars;" 2>/dev/null)
OHLCV_AGE="${OHLCV_AGE//[^0-9]/}"
OHLCV_AGE="${OHLCV_AGE:-99999}"
OHLCV_COUNT=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COUNT(*) FROM ohlcv_bars;" 2>/dev/null)
OHLCV_COUNT="${OHLCV_COUNT//[^0-9]/}"

if [[ $IS_MARKET_OPEN -eq 1 || "$FORCE" == "true" ]]; then
    if [[ "$OHLCV_AGE" -le "$OHLCV_THRESHOLD" ]]; then
        pass "ohlcv_bars: last row ${OHLCV_AGE}s ago (< ${OHLCV_THRESHOLD}s threshold) [${OHLCV_COUNT} rows]"
    elif [[ $IS_MARKET_OPEN -eq 0 ]]; then
        warn "ohlcv_bars: last row ${OHLCV_AGE}s ago — expected: market closed [${OHLCV_COUNT} rows]"
    else
        fail "ohlcv_bars: last row ${OHLCV_AGE}s ago (> ${OHLCV_THRESHOLD}s) — data_ingestion may be stalled [${OHLCV_COUNT} rows]"
    fi
else
    warn "ohlcv_bars: last row ${OHLCV_AGE}s ago — market closed, staleness expected [${OHLCV_COUNT} rows]"
fi

# ─── features freshness ──────────────────────────────────────────────────────
hdr "── features (feature_engineering → TimescaleDB) ───────────────────"

FEAT_AGE=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX(time)))::int, 99999) FROM features;" 2>/dev/null)
FEAT_AGE="${FEAT_AGE//[^0-9]/}"
FEAT_AGE="${FEAT_AGE:-99999}"
FEAT_COUNT=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COUNT(*) FROM features;" 2>/dev/null)
FEAT_COUNT="${FEAT_COUNT//[^0-9]/}"

if [[ $IS_MARKET_OPEN -eq 1 || "$FORCE" == "true" ]]; then
    if [[ "$FEAT_AGE" -le "$FEATURES_THRESHOLD" ]]; then
        pass "features: last row ${FEAT_AGE}s ago (< ${FEATURES_THRESHOLD}s) [${FEAT_COUNT} rows]"
    elif [[ $IS_MARKET_OPEN -eq 0 ]]; then
        warn "features: last row ${FEAT_AGE}s ago — expected: market closed [${FEAT_COUNT} rows]"
    else
        fail "features: last row ${FEAT_AGE}s ago (> ${FEATURES_THRESHOLD}s) — feature_engineering may be stalled [${FEAT_COUNT} rows]"
    fi
else
    warn "features: last row ${FEAT_AGE}s ago — market closed, staleness expected [${FEAT_COUNT} rows]"
fi

# ─── decision_records freshness ───────────────────────────────────────────────
hdr "── decision_records (execution_engine → TimescaleDB) ──────────────"

DR_AGE=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX(timestamp)))::int, 99999) FROM decision_records;" 2>/dev/null)
DR_AGE="${DR_AGE//[^0-9]/}"
DR_AGE="${DR_AGE:-99999}"
DR_COUNT=$(docker compose exec -T timescaledb psql -U apex_user -d apex -At \
    -c "SELECT COUNT(*) FROM decision_records;" 2>/dev/null)
DR_COUNT="${DR_COUNT//[^0-9]/}"

if [[ $IS_MARKET_OPEN -eq 1 || "$FORCE" == "true" ]]; then
    if [[ "$DR_AGE" -le "$DECISION_THRESHOLD" ]]; then
        pass "decision_records: last row ${DR_AGE}s ago (< ${DECISION_THRESHOLD}s) [${DR_COUNT} rows]"
    elif [[ $IS_MARKET_OPEN -eq 0 ]]; then
        warn "decision_records: last row ${DR_AGE}s ago — expected: market closed [${DR_COUNT} rows]"
    else
        fail "decision_records: last row ${DR_AGE}s ago (> ${DECISION_THRESHOLD}s) — execution may be idle [${DR_COUNT} rows]"
    fi
else
    warn "decision_records: last row ${DR_AGE}s ago — market closed, staleness expected [${DR_COUNT} rows]"
fi

# ─── Redis signal freshness ──────────────────────────────────────────────────
hdr "── Redis signal timestamp (signal_provider → Redis) ───────────────"

SIG_TS=$(docker compose exec -T redis redis-cli get "apex:signal_engine:last_signal_ts" 2>/dev/null | tr -d '[:space:]')
if [[ -z "$SIG_TS" || "$SIG_TS" == "nil" || "$SIG_TS" == "(nil)" ]]; then
    if [[ $IS_MARKET_OPEN -eq 1 ]]; then
        fail "Redis apex:signal_engine:last_signal_ts: not set — signal-provider-svc may not be running"
    else
        warn "Redis apex:signal_engine:last_signal_ts: not set — signal-provider-svc not running (optional for core pipeline)"
    fi
else
    SIG_AGE=$(python3 -c "import time; print(int(time.time() - float('$SIG_TS')))" 2>/dev/null || echo "99999")
    if [[ "$SIG_AGE" -le "$SIGNAL_THRESHOLD" ]]; then
        pass "Redis signal timestamp: ${SIG_AGE}s ago (< ${SIGNAL_THRESHOLD}s)"
    elif [[ $IS_MARKET_OPEN -eq 0 ]]; then
        warn "Redis signal timestamp: ${SIG_AGE}s ago — market closed"
    else
        fail "Redis signal timestamp: ${SIG_AGE}s ago (> ${SIGNAL_THRESHOLD}s) — signal pipeline stalled"
    fi
fi

# ─── Kafka consumer lag ───────────────────────────────────────────────────────
hdr "── Kafka consumer lag ─────────────────────────────────────────────"

for grp in apex-signal-engine-v1 apex-risk-engine-v1 apex-execution-v1; do
    lag=$(docker compose exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh \
        --bootstrap-server localhost:9092 \
        --describe --group "$grp" 2>/dev/null \
        | awk 'NR>1 && $6~/^[0-9]+$/ { sum += $6 } END { print sum+0 }')
    if [[ -z "$lag" ]]; then
        warn "Consumer group $grp: lag unknown"
    elif [[ "$lag" -gt 50 ]]; then
        fail "Consumer group $grp: lag=${lag} — messages backing up"
    else
        pass "Consumer group $grp: lag=${lag}"
    fi
done

# ─── Summary ──────────────────────────────────────────────────────────────────
hdr "── Summary ────────────────────────────────────────────────────────"
if [[ $FAILED -eq 0 && $WARNED -eq 0 ]]; then
    printf '  \033[0;32mALL CHECKS PASSED\033[0m\n'
elif [[ $FAILED -eq 0 ]]; then
    printf '  \033[0;33m%d warning(s), 0 failures — likely OK (market closed)\033[0m\n' "$WARNED"
else
    printf '  \033[0;31m%d FAILED, %d warnings\033[0m\n' "$FAILED" "$WARNED"
fi
echo ""
exit "$FAILED"
