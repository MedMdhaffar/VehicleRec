from __future__ import annotations
import json
import os
from pathlib import Path

_COMMON_DIR = Path(__file__).parent

RESULTS_DIR = Path(os.environ.get("VC_RESULTS_DIR", str(_COMMON_DIR / "results")))
MODELS_DIR  = Path(os.environ.get("VC_MODELS_DIR",  str(_COMMON_DIR / "models")))

VEHICLE_MODEL_PATH = os.environ.get("VEHICLE_MODEL_PATH", str(MODELS_DIR / "vehicle_yolo26.pt"))

CONF_THRESHOLD    = float(os.environ.get("CONF_THRESHOLD",    "0.35"))
MIN_BOX_AREA_FRAC = float(os.environ.get("MIN_BOX_AREA_FRAC", "0.001"))

VEHICLE_CLASSIFICATION_PORT = int(os.environ.get("VEHICLE_CLASSIFICATION_PORT", "5020"))

RESULTS_MAX_FILES = int(os.environ.get("RESULTS_MAX_FILES", "50"))

INCLUDE_PERSON_DEFAULT = os.environ.get("INCLUDE_PERSON_DEFAULT", "false").lower() == "true"

# Per-class confidence overrides — JSON dict mapping class-id string to float.
# Example: VC_PER_CLASS_CONF='{"8": 0.45}' raises the bar for van (class 8).
_per_class_raw = os.environ.get("VC_PER_CLASS_CONF", "")
PER_CLASS_CONF: dict[int, float] = (
    {int(k): float(v) for k, v in json.loads(_per_class_raw).items()}
    if _per_class_raw
    else {}
)

# Canonical application taxonomy. Raw model/training labels are normalized only
# at the inference boundary; training metadata remains unchanged.
VEHICLE_LABELS = frozenset({
    "bus", "car", "van", "truck", "motorcycle", "bicycle", "unknown",
})

LABEL_ALIASES: dict[str, str] = {
    "bus": "bus",
    "camper": "van",
    "car": "car",
    "cyclist": "bicycle",
    "bicycle": "bicycle",
    "motorcycle": "motorcycle",
    "tractor": "truck",
    "truck": "truck",
    "van": "van",
}


def normalize_vehicle_label(label: str) -> str:
    return LABEL_ALIASES.get(str(label).strip().lower(), "unknown")


# streetdet v1 — 9 raw model classes → canonical application labels
# classes: bus(0) camper(1) car(2) cyclist(3) motorcycle(4) person(5) tractor(6) truck(7) van(8)
TAXONOMY: dict[int, str] = {
    0: "bus",
    1: "van",
    2: "car",
    3: "bicycle",
    4: "motorcycle",
    5: "unknown",
    6: "truck",
    7: "truck",
    8: "van",
}

# Colour estimation (HSV baseline, Sprint 3)
COLOUR_REGION_FRAC = float(os.environ.get("COLOUR_REGION_FRAC", "0.5"))   # central fraction of crop to sample
COLOUR_TOP_TRIM    = float(os.environ.get("COLOUR_TOP_TRIM",   "0.25"))   # skip top fraction (windshield/sign)
COLOUR_CONF_FLOOR  = float(os.environ.get("COLOUR_CONF_FLOOR",  "0.25"))  # below this → "unknown"
COLOUR_SAT_THRESH  = int(os.environ.get("COLOUR_SAT_THRESH",   "60"))     # saturation < this → achromatic (0-255)
COLOUR_VAL_WHITE   = int(os.environ.get("COLOUR_VAL_WHITE",    "180"))    # value ≥ this → white (0-255)
COLOUR_VAL_BLACK   = int(os.environ.get("COLOUR_VAL_BLACK",    "60"))     # value < this → black (0-255)
