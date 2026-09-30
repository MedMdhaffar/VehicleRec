"""Pipeline settings copied from the production live-feed configuration."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.environ.get("VEHICLE_PIPELINE_MODEL", PACKAGE_DIR / "models" / "vehicle_yolo26.pt"))
TRACKER_CONFIG = Path(os.environ.get("VEHICLE_PIPELINE_TRACKER", PACKAGE_DIR / "trackers" / "bytetrack_vehicles.yaml"))

K_CLASSIFY = 2
TRACK_CONF = float(os.environ.get("TRACK_CONF", "0.10"))
LOST_FRAMES = 15
MANUFACTURER_CROP_POOL_SIZE = min(10, max(1, int(os.environ.get("MANUFACTURER_CROP_POOL_SIZE", "10"))))
MANUFACTURER_CROP_MIN_FRAME_GAP = max(1, int(os.environ.get("MANUFACTURER_CROP_MIN_FRAME_GAP", "8")))

IOU_MATCH = float(os.environ.get("IOU_MATCH", "0.5"))
MIN_OCCUPY_FRAMES = int(os.environ.get("MIN_OCCUPY_FRAMES", "15"))
FREE_FRAMES = int(os.environ.get("FREE_FRAMES", "15"))
OVERSTAY_SEC = int(os.environ.get("OVERSTAY_SEC", "300"))
RESTRICTED_MIN_SEC = int(os.environ.get("RESTRICTED_MIN_SEC", "5"))
OCCUPANCY_MAX = int(os.environ.get("OCCUPANCY_MAX", "10"))
