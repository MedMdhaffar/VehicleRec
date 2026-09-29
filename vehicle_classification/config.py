from __future__ import annotations
import os

PORT                = int(os.environ.get("VEHICLE_CLASSIFICATION_PORT", "5020"))
CONF_THRESHOLD      = float(os.environ.get("VC_CONF_THRESHOLD",      "0.35"))
IOU_THRESHOLD       = float(os.environ.get("VC_IOU_THRESHOLD",       "0.45"))
CROP_CONF_THRESHOLD = float(os.environ.get("CROP_CONF_THRESHOLD",    "0.25"))
