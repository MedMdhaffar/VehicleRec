#!/usr/bin/env bash
set -euo pipefail
umask 077
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
console_python="${T1_CONSOLE_PYTHON:-}"
if [[ -z "$console_python" ]]; then
  # Only bootstrap the stdlib launcher here; its private configuration may
  # select another interpreter before any project dependencies are imported.
  for candidate in "$repo_root/.venv/bin/python" "${VIRTUAL_ENV:-/nonexistent}/bin/python" python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      console_python="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$console_python" ]]; then
  echo 'Missing Python: install Python 3.11 or set T1_CONSOLE_PYTHON to an existing interpreter.' >&2
  exit 1
fi
exec "$console_python" -B "$repo_root/scripts/vehicle_console_launcher.py" "$@"
