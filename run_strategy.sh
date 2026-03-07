#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# APEX Enhanced Strategy Runner
#
# 1. Runs LEAN backtest (supports multiple algorithms: SMA, Ensemble, Framework)
# 2. Copies results JSON to the Report tool directory
# 3. Updates Report/config.json with the algorithm name
# 4. Generates enhanced metrics report via APEX Backtest Reporter
# 5. Pushes metrics to Signal Provider API (if running)
# 6. Generates standard report.html
# 7. Serves report.html in the background on http://localhost:8766
# 
# Usage:
#   ./run_strategy.sh [sma|ensemble|framework] [--start-api] [--start-ui]
# ─────────────────────────────────────────────────────────────────────────────
set -e

# Always resolve workspace root first (safe to do before argument parsing)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WORKSPACE="$SCRIPT_DIR"

# Parse command line arguments
ALGORITHM_TYPE="${1:-ensemble}"  # Default to ensemble
START_API=false
START_UI=false

for arg in "${@:2}"; do
  case "$arg" in
    --start-api) START_API=true ;;
    --start-ui)  START_UI=true  ;;
    *) echo "WARNING: Unknown flag '$arg'"; ;;
  esac
done

# Validate algorithm type
case "$ALGORITHM_TYPE" in
    "sma")
        ALGO_CLASS="SMACrossoverAlgorithm"
        ALGO_DESC="SPY 50/200 SMA Golden Cross strategy"
        ;;
    "test")
        ALGO_CLASS="SimplePythonTest"
        ALGO_DESC="Simple Python test without pandas"
        ;;
    "ensemble")
        ALGO_CLASS="APEXEnsembleAlgorithm"
        ALGO_DESC="APEX Multi-indicator ensemble with ATR sizing and regime detection"
        ;;
    "ensemble-cs")
        ALGO_CLASS="QuantConnect.Algorithm.CSharp.APEXEnsembleAlgorithm"
        ALGO_DESC="APEX Multi-indicator ensemble (C#) with ATR sizing and regime detection"
        ;;
    "simple-cs")
        ALGO_CLASS="QuantConnect.Algorithm.CSharp.SimpleCSAlgorithm"
        ALGO_DESC="Simple C# test algorithm"
        ;;
    "framework")
        ALGO_CLASS="APEXFrameworkAlgorithm"
        ALGO_DESC="APEX Framework with Universe Selection, Alpha Models, VWAP Execution"
        ;;
    "paper-trading")
        ALGO_CLASS="paper-trading"
        ALGO_DESC="Full APEX microservice stack in paper trading mode"
        ;;
    *)
        echo "ERROR: Invalid algorithm type '$ALGORITHM_TYPE'"
        echo "Usage: $0 [sma|test|ensemble|ensemble-cs|framework|paper-trading] [--start-api] [--start-ui]"
        exit 1
        ;;
esac

# ─── PAPER TRADING MODE — starts all 8 microservices; never runs LEAN ─────────
if [[ "$ALGORITHM_TYPE" == "paper-trading" ]]; then
    export COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
    export APEX_CONFIG="configs/paper_trading.yaml"

    echo ""
    echo "╔══════════════════════════════════════════════════╗"
    echo "║   APEX PAPER TRADING MODE                        ║"
    echo "║   Config: configs/paper_trading.yaml             ║"
    echo "║   Alpaca: paper-api.alpaca.markets               ║"
    echo "╚══════════════════════════════════════════════════╝"
    echo ""

    # Verify paper environment guard
    if python3 -c "
import yaml, sys
cfg = yaml.safe_load(open('configs/paper_trading.yaml'))
env = cfg.get('app', {}).get('environment', '')
if env != 'paper':
    print(f'ABORT: environment={env!r} — must be paper', file=sys.stderr)
    sys.exit(1)
base = cfg.get('alpaca', {}).get('base_url', '')
if 'api.alpaca.markets' not in 'paper-api.alpaca.markets':
    print('ABORT: alpaca.base_url does not point to paper endpoint', file=sys.stderr)
    sys.exit(1)
print(f'✓ Paper safety guard: environment=paper, Alpaca=paper endpoint')
"; then
        echo ""
    else
        echo "ERROR: Paper trading safety check failed — aborting."
        exit 1
    fi

    echo "Step 1: Starting infrastructure services (Redis, Kafka, TimescaleDB, MLflow)..."
    docker compose up -d redis kafka timescaledb mlflow schema-registry
    echo "  Waiting 30s for infrastructure to initialize..."
    sleep 30

    echo ""
    echo "Step 2: Running health checks on infrastructure..."
    if bash "$WORKSPACE/scripts/health_check.sh" 2>/dev/null | grep -q '✗'; then
        echo "  WARNING: Some infrastructure checks failed — continuing anyway."
        echo "  Run './scripts/health_check.sh' for details."
    else
        echo "  ✓ Infrastructure healthy"
    fi

    echo ""
    echo "Step 3: Starting all APEX microservices in paper mode..."
    docker compose up -d \
        signal-provider \
        prometheus grafana
    echo "  ✓ Services started"

    # Optionally start dashboard
    if [[ "$START_UI" == true ]]; then
        echo ""
        echo "Step 4: Starting APEX Dashboard..."
        DASHBOARD_DIR="${WORKSPACE}/apex-dashboard"
        if [[ ! -d "$DASHBOARD_DIR/node_modules" ]]; then
            echo "  Installing npm dependencies..."
            (cd "$DASHBOARD_DIR" && npm install --prefer-offline 2>&1 | tail -3)
        fi
        pkill -f "next.*3001" 2>/dev/null || true
        nohup bash -c "cd \"$DASHBOARD_DIR\" && npm run dev -- -p 3001" \
            > "${DASHBOARD_DIR}/dashboard.log" 2>&1 &
        DASH_PID=$!
        sleep 5
        kill -0 $DASH_PID 2>/dev/null \
            && echo "  ✓ Dashboard started on http://localhost:3001" \
            || echo "  ⚠ Dashboard failed to start — see apex-dashboard/dashboard.log"
    fi

    echo ""
    echo "╔══════════════════════════════════════════════════╗"
    echo "║   PAPER TRADING STACK IS RUNNING                 ║"
    echo "╠══════════════════════════════════════════════════╣"
    echo "║   Signal API   http://localhost:8000/docs        ║"
    echo "║   Grafana      http://localhost:3000             ║"
    echo "║   MLflow       http://localhost:5000             ║"
    [[ "$START_UI" == true ]] && \
    echo "║   Dashboard    http://localhost:3001             ║"
    echo "╠══════════════════════════════════════════════════╣"
    echo "║   Monitor:  python scripts/paper_trading_monitor.py  ║"
    echo "║   Health:   ./scripts/health_check.sh            ║"
    echo "║   Kill Svc: docker compose down                  ║"
    echo "╚══════════════════════════════════════════════════╝"
    echo ""
    exit 0
fi

# Workspace vars for LEAN backtest modes (non-paper)
LEAN="$WORKSPACE/Lean"
LAUNCHER="$LEAN/Launcher/bin/Debug"
REPORT="$LEAN/Report/bin/Debug"
MYPROJECT="$WORKSPACE/MyProject"
PY_VENV="$WORKSPACE/lean311_env"

# Python.NET 2.0.53 in this LEAN build requires Python 3.11 runtime symbols.
export PYTHONNET_PYDLL="/usr/lib/x86_64-linux-gnu/libpython3.11.so.1.0"

# Set algorithm in launcher config before running
echo "Configuring LEAN to run: $ALGO_CLASS"
python3 - <<CONFIGEOF
import json
import re

config_path = "$LAUNCHER/config.json"
with open(config_path, "r") as f:
    content = f.read()

# Update algorithm-type-name using regex to preserve formatting
def update_config_value(text, key, value):
    pattern = r'("' + re.escape(key) + r'"\s*:\s*")([^"]*)(")'    
    replacement = r'\1' + value + r'\3'
    return re.sub(pattern, replacement, text)

content = update_config_value(content, "algorithm-type-name", "$ALGO_CLASS")
content = update_config_value(content, "python-venv", "$PY_VENV")

# Set language and location based on algorithm type
if "$ALGORITHM_TYPE" in ["ensemble-cs", "simple-cs"]:
    content = update_config_value(content, "algorithm-language", "CSharp")
    # For C# algorithms, comment out the algorithm-location if enabled
    content = re.sub(r'^(\s*)"algorithm-location"\s*:', r'\1//"algorithm-location":', content, flags=re.MULTILINE)
else:
    content = update_config_value(content, "algorithm-language", "Python")
    # For Python algorithms, ensure algorithm-location is enabled and updated
    content = re.sub(
        r'^(\s*)//+\s*"algorithm-location"\s*:\s*"[^"]*"\s*,?\s*$',
        r'\1"algorithm-location": "../../../Algorithm.Python/$ALGO_CLASS.py",',
        content,
        flags=re.MULTILINE,
    )
    content = update_config_value(content, "algorithm-location", "../../../Algorithm.Python/$ALGO_CLASS.py")

with open(config_path, "w") as f:
    f.write(content)

print(f"✓ Algorithm set to: $ALGO_CLASS")
CONFIGEOF

# Start Signal Provider API if requested
if [[ "$START_API" == true ]]; then
    echo "════════════════════════════════════════"
    echo "  Starting APEX Signal Provider API"
    echo "════════════════════════════════════════"
    cd "$MYPROJECT"
    
    # Kill existing API server if running
    pkill -f "signal_provider_api.py" 2>/dev/null || true
    sleep 1
    
    # Start API in background
    nohup python3 signal_provider_api.py > api.log 2>&1 &
    API_PID=$!
    sleep 3
    
    if kill -0 $API_PID 2>/dev/null; then
        echo "✓ Signal Provider API started on http://localhost:8000 (PID: $API_PID)"
        echo "  Logs: $MYPROJECT/api.log"
    else
        echo "⚠ Failed to start Signal Provider API"
    fi
    echo ""
fi

# Start APEX Dashboard UI if requested
if [[ "$START_UI" == true ]]; then
    echo "════════════════════════════════════════"
    echo "  Starting APEX Dashboard (Next.js)"
    echo "════════════════════════════════════════"
    DASHBOARD_DIR="${WORKSPACE}/apex-dashboard"
    if [[ ! -d "$DASHBOARD_DIR/node_modules" ]]; then
        echo "  Installing npm dependencies…"
        (cd "$DASHBOARD_DIR" && npm install --prefer-offline 2>&1 | tail -3)
    fi
    # Kill existing dashboard if running
    pkill -f "next.*3001" 2>/dev/null || true
    sleep 1
    # Start in background
    nohup bash -c "cd \"$DASHBOARD_DIR\" && npm run dev -- -p 3001" > "${DASHBOARD_DIR}/dashboard.log" 2>&1 &
    DASH_PID=$!
    sleep 5
    if kill -0 $DASH_PID 2>/dev/null; then
        echo "✓ APEX Dashboard started on http://localhost:3001 (PID: $DASH_PID)"
        echo "  Logs: $DASHBOARD_DIR/dashboard.log"
    else
        echo "⚠ Failed to start APEX Dashboard"
    fi
    echo ""
fi

echo "════════════════════════════════════════"
echo "  LEAN Backtest: $ALGO_CLASS"
echo "════════════════════════════════════════"
echo "Description: $ALGO_DESC"
echo ""
cd "$LAUNCHER"

# Run the backtest
echo "Starting LEAN engine..."
env -u PYTHONPATH \
    -u PYTHONHOME \
    -u PYTHONSTARTUP \
    -u PYTHON_NET_DLL_PATH \
    -u VIRTUAL_ENV \
    dotnet QuantConnect.Lean.Launcher.dll <<< ''

# Use the class name for results file
RESULTS_JSON="$LAUNCHER/${ALGO_CLASS}.json"
echo ""
echo "Algorithm: $ALGO_CLASS"
echo "Results file: $RESULTS_JSON"

if [[ ! -f "$RESULTS_JSON" ]]; then
    echo "ERROR: Expected results file not found: $RESULTS_JSON"
    echo "Available files:"
    ls -la "$LAUNCHER"/*.json 2>/dev/null || echo "No JSON files found"
    exit 1
fi

echo "✓ Backtest completed successfully"

echo ""
echo "════════════════════════════════════════"
echo "  APEX Enhanced Reporting"
echo "════════════════════════════════════════"

# Copy backtest result into Report directory
cp "$RESULTS_JSON" "$REPORT/${ALGO_CLASS}.json"
echo "✓ Results copied to Report directory"

# Run APEX Backtest Reporter for enhanced metrics
# APEX-UPDATE: Step-5E 2026-02-27 — use venv Python (has pandas/numpy/requests)
echo ""
echo "Running APEX Backtest Reporter..."
cd "$MYPROJECT"
VENV_PYTHON="$WORKSPACE/.venv/bin/python3"
if [ ! -f "$VENV_PYTHON" ]; then VENV_PYTHON=python3; fi
"$VENV_PYTHON" backtest_reporter.py "$RESULTS_JSON" "$ALGO_CLASS"
echo ""
echo "✓ Enhanced metrics analysis completed"

# Update Report/config.json with algorithm metadata
echo "Updating QuantConnect Report configuration..."
python3 - <<PYEOF
import re

cfg_path = "$REPORT/config.json"
with open(cfg_path, "r") as f:
    raw = f.read()

# Replace values using regex (preserves JSONC comments)
def set_val(text, key, val):
    return re.sub(
        r'("' + key + r'"\s*:\s*)"[^"]*"',
        r'\g<1>"' + val + '"',
        text
    )

raw = set_val(raw, 'strategy-name', '${ALGO_CLASS}')
raw = set_val(raw, 'strategy-description', '${ALGO_DESC}')
raw = set_val(raw, 'live-data-source-file', '${ALGO_CLASS}.json')
raw = set_val(raw, 'backtest-data-source-file', '${ALGO_CLASS}.json')
raw = set_val(raw, 'report-destination', 'report.html')

with open(cfg_path, 'w') as f:
    f.write(raw)
print('✓ QuantConnect Report config updated.')
PYEOF

# Generate QuantConnect HTML report
echo ""
echo "Generating QuantConnect HTML Report..."
cd "$REPORT"
env -u PYTHONHOME \
    -u PYTHONSTARTUP \
    -u PYTHON_NET_DLL_PATH \
    -u VIRTUAL_ENV \
    PYTHONPATH="$PY_VENV/lib/python3.11/site-packages" \
    PYTHONNET_PYDLL="/usr/lib/x86_64-linux-gnu/libpython3.11.so.1.0" \
    MPLBACKEND="Agg" \
    dotnet QuantConnect.Report.dll
echo "✓ QuantConnect report.html generated"

echo ""
echo "════════════════════════════════════════"
echo "  🎯 APEX Strategy Complete!"
echo "════════════════════════════════════════"
echo "Algorithm:     $ALGO_CLASS"
echo "Type:          $ALGORITHM_TYPE"
echo "Report (QC):   $REPORT/report.html"
echo "Enhanced:      backtest_report_*.txt"
echo "Metrics DB:    $MYPROJECT/backtest_metrics.db"
if [[ "$START_API" == "--start-api" ]]; then
    echo "Signal API:    http://localhost:8000"
    echo "API Health:    http://localhost:8000/health"
fi
echo ""

# Kill any existing server on port 8766 and start a fresh one
fuser -k 8766/tcp 2>/dev/null || true
echo "📊 Starting report server..."
echo "   Serving on http://localhost:8766"
echo "   (Press Ctrl+C to stop)"
python3 -m http.server 8766 --directory "$REPORT" &
sleep 2

echo ""
echo "🚀 Ready! Open these URLs:"
echo "   📈 QuantConnect Report: http://localhost:8766/report.html"
if [[ "$START_API" == true ]]; then
    echo "   🔌 Signal API Docs:     http://localhost:8000/docs"
    echo "   💊 API Health Check:   http://localhost:8000/health"
fi
if [[ "$START_UI" == true ]]; then
    echo "   🖥️  APEX Dashboard:      http://localhost:3001"
fi
echo ""
echo "To run different algorithms:"
echo "   $0 sma              # Original SMA Crossover"
echo "   $0 ensemble         # Multi-indicator Ensemble (default)"
echo "   $0 framework        # Advanced Framework with Universe Selection"
echo "   $0 paper-trading    # Full APEX microservice stack (paper mode)"
