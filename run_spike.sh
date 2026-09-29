#!/usr/bin/env bash
# run_spike.sh — launch the batched multi-camera prototype + live grid UI.
# Bakes the VMS creds (same as start_live_feed.sh) so you never type them.
#
#   bash /home/malek/workflow/vehicle_services/run_spike.sh            # ch4 ch5
#   CHANNELS="4 5 6 7" bash .../run_spike.sh                           # 4 cameras
#   bash .../run_spike.sh --no-export                                  # skip TensorRT export
set -euo pipefail

ROOT="/home/malek/workflow"
VS_ROOT="$ROOT/vehicle_services"
PYTHON="$ROOT/.venv_linux/bin/python"
PORT="${PORT:-5050}"

# ── Windows host IP (for the VMS on the host) ─────────────────────────────────
WINDOWS_HOST=$(ip route show | awk '/^default/ {print $3; exit}')
[ -z "$WINDOWS_HOST" ] && { echo "ERROR: could not detect Windows host IP" >&2; exit 1; }
echo "  Windows host: $WINDOWS_HOST"

# ── VMS credentials (mirrors start_live_feed.sh) ──────────────────────────────
export VMS_URL="http://$WINDOWS_HOST:5057"
export VMS_DEVICE_IP="192.168.40.200"
export VMS_DEVICE_PORT="37777"
export VMS_USERNAME="admin"
export VMS_PASSWORD="broNO_WoRk-2026"
export VMS_CHANNEL_OFFSET="-1"
export CHANNELS="${CHANNELS:-4 1}"
export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1

echo "  channels: $CHANNELS   port: $PORT   VMS: $VMS_URL"
echo "  → open http://localhost:$PORT once it boots"

# open the browser shortly after boot (best-effort; works from WSL via cmd.exe)
( sleep 6; cmd.exe /c start "http://localhost:$PORT" >/dev/null 2>&1 || true ) &

cd "$VS_ROOT"
exec "$PYTHON" spike_batched_cam.py --port "$PORT" "$@"
