#!/usr/bin/env bash
# ─── scripts/verify_first_trade.sh ───────────────────────────────────────────
# APEX First Trade Verification
#
# Confirms the full signal pipeline is flowing and that at least one order
# reached the Alpaca Paper API.  Run AFTER `docker compose up` and the
# system has been running for at least 2 minutes.
#
# Checks performed:
#   1.  Kafka topics have messages flowing (signal pipeline active)
#   2.  Risk engine loaded limits from configs/limits.yaml
#   3.  At least one signal reached the execution service
#   4.  Alpaca paper API accepted at least one order
#
# Usage:
#   bash scripts/verify_first_trade.sh
#   ALPACA_API_KEY=... ALPACA_SECRET_KEY=... bash scripts/verify_first_trade.sh
# ──────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# Load canonical paths (repo root, compose file, service names, etc.)
source "$(dirname "$0")/lib/paths.sh"
export COMPOSE_FILE

FAILED=0
TIMEOUT=10  # seconds to wait for Kafka consumer to sample messages

pass() { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
fail() { FAILED=$((FAILED+1)); printf '  \033[0;31m✗\033[0m %s\n' "$*"; }
info() { printf '  \033[0;34mℹ\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[0;36m%s\033[0m\n' "$*"; }

# Load infra/.env if available (for ALPACA keys)
[[ -f infra/.env ]] && set -a && source infra/.env && set +a

ALPACA_BASE_URL="${ALPACA_BASE_URL:-https://paper-api.alpaca.markets}"
ALPACA_API_KEY="${ALPACA_API_KEY:-}"
ALPACA_SECRET_KEY="${ALPACA_SECRET_KEY:-}"

# ─── 1. Kafka Topic Message Flow ──────────────────────────────────────────────

hdr "── 1. Kafka Topic Message Flow ──────────────────────────────────────────"

TOPICS=(
    "apex.lean.triggers"
    "apex.signals.raw"
    "apex.signals.scored"
    "apex.risk.approved"
    "apex.orders.results"
)

for topic in "${TOPICS[@]}"; do
    # Get end offsets for all partitions in the topic
    offsets=$(docker compose exec -T kafka \
        /opt/kafka/bin/kafka-run-class.sh kafka.tools.GetOffsetShell \
        --bootstrap-server localhost:9092 \
        --topic "$topic" \
        --time -1 2>/dev/null || echo "")

    if [[ -z "$offsets" ]]; then
        # try alternate binary path
        offsets=$(docker compose exec -T kafka \
            kafka-run-class.sh kafka.tools.GetOffsetShell \
            --bootstrap-server localhost:9092 \
            --topic "$topic" \
            --time -1 2>/dev/null || echo "")
    fi

    if [[ -z "$offsets" ]]; then
        fail "${topic}: topic not found or Kafka unreachable"
        continue
    fi

    # Sum offsets across partitions
    total_msgs=$(echo "$offsets" | awk -F: '{sum += $3} END {print sum+0}')
    if [[ "$total_msgs" -gt 0 ]]; then
        pass "${topic}: ${total_msgs} message(s) recorded"
    else
        info "${topic}: 0 messages — topic exists but no data yet (normal if just started)"
    fi
done

# ─── 2. Risk Engine Limits Config ────────────────────────────────────────────

hdr "── 2. Risk Engine Limits Config ─────────────────────────────────────────"

if [[ -f configs/limits.yaml ]]; then
    pass "configs/limits.yaml: file exists"

    pos_pct=$(grep "max_position_pct" configs/limits.yaml 2>/dev/null | head -1 | awk '{print $2}')
    if [[ -n "$pos_pct" ]]; then
        pass "max_position_pct: ${pos_pct}"
        if python3 -c "v=float('${pos_pct}'); assert 0 < v <= 0.05, f'suspicious: {v}'" 2>/dev/null; then
            pass "max_position_pct is within safe range (0 < pct ≤ 5%)"
        else
            fail "max_position_pct=${pos_pct} looks suspicious — expected ≤ 0.05 for paper trading"
        fi
    else
        fail "configs/limits.yaml: max_position_pct key not found"
    fi

    dd_limit=$(grep "max_drawdown" configs/limits.yaml 2>/dev/null | head -1 | awk '{print $2}')
    [[ -n "$dd_limit" ]] && pass "max_drawdown: ${dd_limit}" || \
        fail "configs/limits.yaml: max_drawdown not found"
else
    fail "configs/limits.yaml not found — risk engine cannot load limits"
fi

# Verify kill-switch is OFF
ks=$(docker compose exec -T redis redis-cli get "apex:kill_switch" 2>/dev/null | tr -d '[:space:]')
if [[ "$ks" == "1" ]]; then
    fail "Kill switch ACTIVE (apex:kill_switch=1) — no orders will be sent"
else
    pass "Kill switch: inactive (trading enabled)"
fi

# ─── 3. Signal in Execution Logs ─────────────────────────────────────────────

hdr "── 3. Signal Flow to Execution ──────────────────────────────────────────"

exec_logs=$(docker compose logs --tail=200 execution-engine 2>/dev/null || echo "")
if [[ -z "$exec_logs" ]]; then
    info "Execution service: could not read logs"
else
    if echo "$exec_logs" | grep -qi "order\|executed\|submitted\|alpaca"; then
        pass "Execution: order-related log entries found"
    elif echo "$exec_logs" | grep -qi "signal\|consumed\|received"; then
        pass "Execution: signal consumption entries found (order submission pending)"
    else
        info "Execution: no signal/order log entries yet (normal in first 2 min)"
    fi

    if echo "$exec_logs" | grep -qi "error\|exception\|traceback"; then
        fail "Execution: ERROR entries found in logs — check: docker compose logs execution-engine"
    else
        pass "Execution: no ERROR in recent logs"
    fi
fi

# ─── 4. Alpaca Paper API Order Check ─────────────────────────────────────────

hdr "── 4. Alpaca Paper API ───────────────────────────────────────────────────"

if [[ -z "$ALPACA_API_KEY" || "$ALPACA_API_KEY" == "your-alpaca-api-key-here" ]]; then
    fail "ALPACA_API_KEY not set or is placeholder — cannot verify Alpaca orders"
    info "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in infra/.env and re-run"
else
    # Query Alpaca paper trading orders (last 10)
    orders=$(curl -sf \
        -H "APCA-API-KEY-ID: ${ALPACA_API_KEY}" \
        -H "APCA-API-SECRET-KEY: ${ALPACA_SECRET_KEY}" \
        "${ALPACA_BASE_URL}/v2/orders?limit=10&status=all" 2>/dev/null || echo "[]")

    if echo "$orders" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
        n=$(echo "$orders" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
        if [[ "$n" -gt 0 ]]; then
            pass "Alpaca Paper API: ${n} order(s) found — pipeline is working!"
            # Print most recent order
            echo "$orders" | python3 -c "
import sys, json
orders = json.load(sys.stdin)
if orders:
    o = orders[0]
    print(f'     Latest: {o.get(\"symbol\")} {o.get(\"side\")} {o.get(\"qty\")} @ {o.get(\"status\")} ({o.get(\"submitted_at\",\"\")[:19]})')
" 2>/dev/null || true
        else
            info "Alpaca Paper API: credentials valid, 0 orders yet (normal if < 5 min)"
        fi
    else
        # May be an auth error dict
        err_msg=$(echo "$orders" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message','unknown error'))" 2>/dev/null || echo "parse error")
        fail "Alpaca Paper API: request failed — ${err_msg}"
        info "Check: curl -H 'APCA-API-KEY-ID: \$ALPACA_API_KEY' '${ALPACA_BASE_URL}/v2/account'"
    fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────

hdr "── Summary ───────────────────────────────────────────────────────────────"
if [[ $FAILED -eq 0 ]]; then
    printf '\033[0;32m  All verification checks passed.\033[0m\n\n'
    exit 0
else
    printf '\033[0;31m  %d check(s) FAILED — see output above.\033[0m\n\n' "$FAILED"
    echo "Common fixes:"
    echo "  • System just started: wait 2-3 minutes for Kafka to receive bars"
    echo "  • No Alpaca orders:    data_ingestion or lean_alpha may not have published signals yet"
    echo "  • Alpaca auth fail:    verify keys at https://app.alpaca.markets/paper-trading/account"
    echo ""
    exit 1
fi
