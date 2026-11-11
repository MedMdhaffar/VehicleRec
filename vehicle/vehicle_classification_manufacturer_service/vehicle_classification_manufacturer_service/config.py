"""Pipeline settings copied from the production live-feed configuration."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.environ.get("VEHICLE_PIPELINE_MODEL", PACKAGE_DIR / "models" / "vehicle_yolo26.pt"))
TRACKER_CONFIG = Path(os.environ.get("VEHICLE_PIPELINE_TRACKER", PACKAGE_DIR / "trackers" / "bytetrack_vehicles.yaml"))

TRACK_CONF = float(os.environ.get("TRACK_CONF", "0.10"))
LOST_FRAMES = 15

IOU_MATCH = float(os.environ.get("IOU_MATCH", "0.5"))
MIN_OCCUPY_FRAMES = int(os.environ.get("MIN_OCCUPY_FRAMES", "15"))
FREE_FRAMES = int(os.environ.get("FREE_FRAMES", "15"))
OVERSTAY_SEC = int(os.environ.get("OVERSTAY_SEC", "300"))
RESTRICTED_MIN_SEC = int(os.environ.get("RESTRICTED_MIN_SEC", "5"))
OCCUPANCY_MAX = int(os.environ.get("OCCUPANCY_MAX", "10"))

SCENE_CUT_THRESHOLD = float(os.environ.get("VEHICLE_SCENE_CUT_THRESHOLD", "0.12"))

# Proposal classes only: exclude people; attribute recognition belongs to the VLM.
VEHICLE_DETECTION_CLASS_IDS = (0, 1, 2, 3, 4, 6, 7, 8)
