#!/usr/bin/env bash
# =============================================================================
# scripts/run_and_report.sh
#
# Single-command workflow:
#   1. Build Lean solution (only if DLLs are missing or source is newer)
#   2. Run LEAN backtest (headless)
#   3. Generate HTML report
#   4. Serve on localhost and open the URL
#
# Usage (from any directory):
#   bash /path/to/QuantConnect.VS/scripts/run_and_report.sh [--port 8766] [--force-build]
#
# Options:
#   --port N        HTTP port for the local server (default: 8766)
#   --force-build   Rebuild even if DLLs are up-to-date
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve workspace root relative to this script (works from any cwd)
# ---------------------------------------------------------------------------
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$SCRIPTS_DIR")"
LEAN="$WORKSPACE/Lean"
SLN="$LEAN/QuantConnect.Lean.sln"
LAUNCHER="$LEAN/Launcher/bin/Debug"
LAUNCHER_DLL="$LAUNCHER/QuantConnect.Lean.Launcher.dll"
LAUNCHER_CFG="$LAUNCHER/config.json"
REPORT_DIR="$LEAN/Report/bin/Debug"
REPORT_DLL="$REPORT_DIR/QuantConnect.Report.dll"
REPORT_CFG="$REPORT_DIR/config.json"
REPORT_HTML="$REPORT_DIR/report.html"

# Python.NET requires the CPython shared library
export PYTHONNET_PYDLL="/home/kironix/miniconda3/envs/NanocloudEnv/lib/libpython3.11.so"

# Defaults
PORT=8766
FORCE_BUILD=0

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)       PORT="$2";    shift 2 ;;
    --force-build) FORCE_BUILD=1; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
step()  { echo; echo "──────────────────────────────────────────────"; echo "  $*"; echo "──────────────────────────────────────────────"; }
ok()    { echo "  ✓ $*"; }
fail()  { echo; echo "  ✗ ERROR: $*" >&2; exit 1; }
check_file() { [[ -f "$1" ]] || fail "Expected file not found: $1"; }

# ---------------------------------------------------------------------------
# Step 1 — Build (skip if DLLs exist and sources haven't changed)
# ---------------------------------------------------------------------------
needs_build() {
  [[ "$FORCE_BUILD" -eq 1 ]]                         && return 0
  [[ ! -f "$LAUNCHER_DLL" ]]                         && return 0
  [[ ! -f "$REPORT_DLL"   ]]                         && return 0
  # If any .cs or .py algo source is newer than the Launcher DLL, rebuild
  if find "$LEAN" -name "*.cs" -newer "$LAUNCHER_DLL" -not -path "*/obj/*" \
                               -not -path "*/bin/*" | grep -q .; then
    return 0
  fi
  return 1
}

step "Step 1/4 — Build"
if needs_build; then
  echo "  Building QuantConnect.Lean.sln ..."
  check_file "$SLN"
  dotnet build "$SLN" -c Debug --nologo 2>&1 | grep -E "error|warning|Build succeeded|FAILED" || true
  check_file "$LAUNCHER_DLL" || fail "Build completed but Launcher DLL not found: $LAUNCHER_DLL"
  check_file "$REPORT_DLL"   || fail "Build completed but Report DLL not found: $REPORT_DLL"
  ok "Build succeeded"
else
  ok "DLLs are up-to-date — skipping build (use --force-build to override)"
fi

# ---------------------------------------------------------------------------
# Step 2 — Run LEAN backtest (headless)
# ---------------------------------------------------------------------------
step "Step 2/4 — LEAN Backtest"
check_file "$LAUNCHER_DLL"
check_file "$LAUNCHER_CFG"

# Read algorithm name from the runtime config
ALGO_NAME=$(grep '"algorithm-type-name"' "$LAUNCHER_CFG" \
  | sed 's/.*"algorithm-type-name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
[[ -n "$ALGO_NAME" ]] || fail "Could not read algorithm-type-name from $LAUNCHER_CFG"

RESULTS_JSON="$LAUNCHER/${ALGO_NAME}.json"
echo "  Algorithm : $ALGO_NAME"
echo "  Results   : $RESULTS_JSON"
echo ""

cd "$LAUNCHER"
# <<< '' satisfies the "Press any key to continue" prompt non-interactively
dotnet QuantConnect.Lean.Launcher.dll <<< '' || fail "LEAN Launcher exited with errors. Check output above."

check_file "$RESULTS_JSON" \
  || fail "Backtest finished but results JSON not found: $RESULTS_JSON
  Possible causes:
    - Algorithm threw an exception (check output above for ERROR lines)
    - algorithm-type-name in config.json does not match your class name
    - Algorithm file path in algorithm-location is wrong"

ok "Backtest complete — results written to $RESULTS_JSON"

# ---------------------------------------------------------------------------
# Step 3 — Generate HTML report
# ---------------------------------------------------------------------------
step "Step 3/4 — Generate Report"
check_file "$REPORT_DLL"
check_file "$REPORT_CFG"

# Copy backtest JSON into the Report working directory
cp "$RESULTS_JSON" "$REPORT_DIR/${ALGO_NAME}.json"
ok "Copied results JSON to Report directory"

# Update strategy metadata in Report/config.json using Python (preserves JSONC comments)
python3 - "$REPORT_CFG" "$ALGO_NAME" << 'PYEOF'
import sys, re

cfg_path   = sys.argv[1]
algo_name  = sys.argv[2]

with open(cfg_path) as f:
    raw = f.read()

def set_val(text, key, val):
    return re.sub(
        r'("' + key + r'"\s*:\s*)"[^"]*"',
        r'\g<1>"' + val + '"',
        text
    )

raw = set_val(raw, "strategy-name",             algo_name)
raw = set_val(raw, "strategy-description",      algo_name + " backtest")
raw = set_val(raw, "backtest-data-source-file", algo_name + ".json")
raw = set_val(raw, "report-destination",        "report.html")

with open(cfg_path, "w") as f:
    f.write(raw)

print(f"  Report config updated → strategy: {algo_name}")
PYEOF

cd "$REPORT_DIR"
dotnet QuantConnect.Report.dll <<< '' || fail "Report generator exited with errors."

check_file "$REPORT_HTML" \
  || fail "Report tool finished but report.html was not written.
  Check that PYTHONNET_PYDLL is set correctly:
    export PYTHONNET_PYDLL=$PYTHONNET_PYDLL"

REPORT_SIZE=$(du -h "$REPORT_HTML" | cut -f1)
ok "report.html written (${REPORT_SIZE}) → $REPORT_HTML"

# ---------------------------------------------------------------------------
# Step 4 — Serve and open
# ---------------------------------------------------------------------------
step "Step 4/4 — Serve report"

# Kill any existing server on the chosen port
fuser -k "${PORT}/tcp" 2>/dev/null || true
sleep 0.3

python3 -m http.server "$PORT" --directory "$REPORT_DIR" \
  --bind 127.0.0.1 >/dev/null 2>&1 &
SERVER_PID=$!

# Brief wait to confirm it started
sleep 0.8
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  fail "HTTP server failed to start on port $PORT (is the port already in use?)"
fi

URL="http://localhost:${PORT}/report.html"
ok "Server PID $SERVER_PID running on port $PORT"

echo ""
echo "════════════════════════════════════════════════════════"
echo ""
echo "  All done."
echo ""
echo "  Open in browser:"
echo "    $URL"
echo ""
echo "  Strategy   : $ALGO_NAME"
echo "  Report     : $REPORT_HTML"
echo "  Server PID : $SERVER_PID  (kill $SERVER_PID to stop)"
echo ""
echo "════════════════════════════════════════════════════════"
