#!/usr/bin/env bash
# APEX CLI wrapper script
# Usage: ./apex <command> [args]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment if it exists
if [[ -f "$SCRIPT_DIR/lean_venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/lean_venv/bin/activate"
fi

cd "$SCRIPT_DIR/MyProject"
python3 apex_cli.py "$@"