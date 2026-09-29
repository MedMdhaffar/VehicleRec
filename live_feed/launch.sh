#!/usr/bin/env bash
# STALE: per-channel single-camera launcher — superseded by start_live_feed.sh (batched multi-cam).
# Not migrated in F6; Hik EHome and vms_client changes are in start_live_feed.sh only.
#
# Start one live_feed uvicorn process per channel.
# Run from vehicle_services/ with VMS env vars exported.
#
# Usage:
#   CHANNELS="4 5" ./live_feed/launch.sh
#
# Requires CUDA MPS enabled for >1 camera:
#   nvidia-cuda-mps-control -d

set -euo pipefail

CHANNELS="${CHANNELS:-4}"
LOG_DIR="${LOG_DIR:-logs}"
mkdir -p "$LOG_DIR"

pids=()

for ch in $CHANNELS; do
    port=$((5030 + ch))
    log_file="$LOG_DIR/lf_ch${ch}.log"
    echo "Starting channel $ch on port $port (log: $log_file) …"
    LF_CHANNEL=$ch python -m uvicorn live_feed.app:app \
        --host 0.0.0.0 --port "$port" \
        --log-level info \
        >> "$log_file" 2>&1 &
    pids+=($!)
done

echo "All instances started. PIDs: ${pids[*]}"
echo "Press Ctrl-C to stop all."

wait
