#!/usr/bin/env bash
# start_live_feed.sh — Launch the batched multi-camera live_feed (F3 + F4 + F6).
#
# ONE backend process serves ALL cameras off a shared TensorRT engine (batched).
# Cameras = Dahua NVR channels via VMS (CHANNELS) + Hikvision EHome via VMS (HIK_CAMERAS).
#
# Prereq: Dahua SDK web running (start.bat in C:\...\dahuasdkweb) for Dahua cameras.
#         Hik EHome CMS (:5200) reachable on $VMS_HOST for Hikvision cameras.
# Run:    bash /home/malek/workflow/vehicle_services/start_live_feed.sh

ROOT="/home/malek/workflow"
VS_ROOT="$ROOT/vehicle_services"
PYTHON="$ROOT/.venv_linux/bin/python"

# ── Windows host IP (Dahua VMS lives on the host) ─────────────────────────────
WINDOWS_HOST=$(ip route show | awk '/^default/ {print $3; exit}')
if [ -z "$WINDOWS_HOST" ]; then
    echo "ERROR: could not detect Windows host IP" >&2
    exit 1
fi
echo "  Windows host: $WINDOWS_HOST"

# ── Cameras ───────────────────────────────────────────────────────────────────
# Dahua NVR channels (space/comma separated). Leave empty to auto-discover from VMS.
export CHANNELS="${CHANNELS:-}"
# Hikvision EHome cameras via VMS — "id=ip", semicolon-separated:
export HIK_CAMERAS="${HIK_CAMERAS:-103=10.0.0.103;104=10.0.0.104;78=10.0.0.78}"

# ── VMS connection (vms_client VMSConfig names; one host serves Hik :5200 + Dahua :5057) ──
# NOTE: VMS_HOST must serve BOTH gateways. If Hik EHome (SecureVision :5200) and Dahua SDK
# (:5057) run on different hosts, set VMS_HOST to the host that has BOTH, or adjust accordingly.
export VMS_HOST="$WINDOWS_HOST"
export VMS_HIK_PORT="5200"
export VMS_DAHUA_PORT="5057"
export DAHUA_NVR_IP="192.168.40.200"
export DAHUA_NVR_PORT="37777"
export DAHUA_NVR_USER="admin"
export DAHUA_NVR_PASS="broNO_WoRk-2026"
export DAHUA_CHANNEL_OFFSET="-1"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT"
export LF_PORT_BASE=5030
PORT=5050   # single backend port (LF_PORT_BASE + first Dahua channel, set by cfg.LF_PORT)

# ── Frontend camera list — ids MUST match the backend registry ────────────────
#   dahua ids = channel numbers; hik ids = the part before '=' in HIK_CAMERAS
DAHUA_IDS=$(echo "$CHANNELS" | tr ' ' ',' | sed 's/,,*/,/g; s/^,//; s/,$//')
HIK_IDS=$(echo "$HIK_CAMERAS" | tr ';' '\n' | sed 's/=.*//; /^$/d' | paste -sd, -)
export VITE_CAMERAS="${DAHUA_IDS}${HIK_IDS:+,$HIK_IDS}"
export VITE_API_URL="http://localhost:$PORT"

echo "  cameras: $VITE_CAMERAS   backend: http://localhost:$PORT"

# ── Shared env file (sourced by every tab) ────────────────────────────────────
ENV_FILE="/tmp/lf_env.sh"
cat > "$ENV_FILE" << ENVEOF
export PYTHONUNBUFFERED=1
export PYTHONPATH='$ROOT'
export CHANNELS='$CHANNELS'
export HIK_CAMERAS='$HIK_CAMERAS'
export VMS_HOST='$VMS_HOST'
export VMS_HIK_PORT='$VMS_HIK_PORT'
export VMS_DAHUA_PORT='$VMS_DAHUA_PORT'
export DAHUA_NVR_IP='$DAHUA_NVR_IP'
export DAHUA_NVR_PORT='$DAHUA_NVR_PORT'
export DAHUA_NVR_USER='$DAHUA_NVR_USER'
export DAHUA_NVR_PASS='$DAHUA_NVR_PASS'
export DAHUA_CHANNEL_OFFSET='$DAHUA_CHANNEL_OFFSET'
export LF_PORT_BASE='$LF_PORT_BASE'
export VITE_CAMERAS='$VITE_CAMERAS'
export VITE_API_URL='$VITE_API_URL'
ENVEOF

# ── open_tab helper (Windows Terminal / WSL) ──────────────────────────────────
open_tab() {
    local title="$1"
    local workdir="$2"
    local cmd="$3"
    local script="/tmp/waycon_${title}.sh"
    cat > "$script" << SCRIPT
#!/bin/bash --login
source '$ENV_FILE'
cd '$workdir'
echo ''
echo '=== $title ==='
echo ''
$cmd
echo ''
echo '=== EXITED — press Enter ==='
exec bash
SCRIPT
    chmod +x "$script"
    cmd.exe /c start wsl.exe -d Ubuntu-22.04 bash --login "$script"
    sleep 0.5
}

echo ""
echo "=============================================="
echo "  Launching live_feed (1 backend, all cameras)"
echo "=============================================="

echo "  [1/2] backend  -> :$PORT  (cameras: $VITE_CAMERAS)"
open_tab "lf_backend" "$VS_ROOT" \
    "$PYTHON -m uvicorn live_feed.app:app --host 0.0.0.0 --port $PORT"

echo "  [2/2] frontend -> :5173"
open_tab "lf_frontend" "$VS_ROOT/live_feed/frontend" \
    "npm run dev"

echo ""
echo "  Waiting 8s for the backend to boot (first run builds the TRT engine)…"
sleep 8

echo "  Starting pipeline (all cameras)…"
curl -s -X POST "http://localhost:$PORT/start" | $PYTHON -m json.tool 2>/dev/null \
    || echo "  (POST /start failed — check the lf_backend tab; engine build can take a few min)"

echo ""
echo "=============================================="
echo "  Live Feed ready."
echo "  Backend   http://localhost:$PORT/health"
echo "  Dashboard http://localhost:5173"
echo "  Cameras   $VITE_CAMERAS   (VMS=$VMS_HOST Dahua:$VMS_DAHUA_PORT Hik:$VMS_HIK_PORT)"
echo "=============================================="
