"""StreetDet-to-public vehicle taxonomy copied from common.vehicle_types."""
from __future__ import annotations

import os

PUBLIC_VEHICLE_TYPES = ("car", "van", "bus", "truck", "motorcycle", "bicycle", "unknown")
STREETDET_RAW_CLASSES_BY_ID = {
    0: "bus", 1: "camper", 2: "car", 3: "cyclist", 4: "motorcycle",
    5: "person", 6: "tractor", 7: "truck", 8: "van",
}
STREETDET_VEHICLE_CLASS_IDS = (0, 1, 2, 3, 4, 6, 7, 8)
_NORMALIZED = {
    "bicycle": "bicycle", "car": "car", "motorcycle": "motorcycle",
    "bus": "bus", "truck": "truck", "van": "van", "cyclist": "bicycle",
    "tractor": "truck", "camper": "van",
}
VEHICLE_TYPE_CONF_THRESHOLD = float(os.environ.get("VEHICLE_TYPE_CONF_THRESHOLD", os.environ.get("CONF_THRESHOLD", os.environ.get("VC_CONF_THRESHOLD", "0.35"))))


def normalize_streetdet_class_id(class_id: int, confidence: float | None = None) -> str | None:
    raw = STREETDET_RAW_CLASSES_BY_ID.get(int(class_id))
    if raw == "person":
        return None
    label = _NORMALIZED.get(raw or "", "unknown")
    if confidence is not None and confidence < VEHICLE_TYPE_CONF_THRESHOLD:
        return "unknown"
    return label
