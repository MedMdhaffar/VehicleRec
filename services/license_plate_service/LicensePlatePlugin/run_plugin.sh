#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PLUGIN_PYTHON:-python}"

export PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$PLUGIN_DIR/.runtime_cache/matplotlib}"

exec "$PYTHON_BIN" -m license_plate_plugin \
  --config "$PLUGIN_DIR/configuration/local_reproduction.json" \
  "$@"
