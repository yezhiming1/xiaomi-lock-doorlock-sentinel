#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/smoke.py"
