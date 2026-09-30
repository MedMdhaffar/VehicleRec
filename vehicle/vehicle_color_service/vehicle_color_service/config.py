from __future__ import annotations
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _local_env = Path(__file__).parent / ".env"
    if _local_env.is_file():
        load_dotenv(_local_env)
except ImportError:
    pass

_COMMON_DIR = Path(__file__).parent

RESULTS_DIR = Path(os.environ.get("VC_RESULTS_DIR", str(_COMMON_DIR / "results")))
MODELS_DIR  = Path(os.environ.get("VC_MODELS_DIR",  str(_COMMON_DIR / "models")))

VEHICLE_MODEL_PATH = os.environ.get("VEHICLE_MODEL_PATH", str(MODELS_DIR / "vehicle_yolo26.pt"))
TRACKER_CONFIG = os.environ.get(
    "TRACKER_CONFIG", str(_COMMON_DIR / "trackers" / "bytetrack_vehicles.yaml")
)
TRACK_CONF = float(os.environ.get("TRACK_CONF", "0.10"))
FPS = int(os.environ.get("FPS", "15"))
LOST_FRAMES = int(os.environ.get("LOST_FRAMES", "15"))

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

# Temporal colour aggregation. These are intentionally colocated with the
# single-frame HSV thresholds so this directory has no live_feed dependency.
MIN_VALID_COLOR_SAMPLES = int(os.environ.get("MIN_VALID_COLOR_SAMPLES", "5"))
MAX_VALID_COLOR_SAMPLES = int(os.environ.get("MAX_VALID_COLOR_SAMPLES", "20"))
EARLY_LOCK_WINNER_SHARE = float(os.environ.get("EARLY_LOCK_WINNER_SHARE", "0.68"))
EARLY_LOCK_MARGIN = float(os.environ.get("EARLY_LOCK_MARGIN", "0.20"))
FINAL_WINNER_SHARE = float(os.environ.get("FINAL_WINNER_SHARE", "0.55"))
FINAL_WINNER_MARGIN = float(os.environ.get("FINAL_WINNER_MARGIN", "0.10"))
SHORT_TRACK_WINNER_SHARE = float(os.environ.get("SHORT_TRACK_WINNER_SHARE", "0.70"))
SHORT_TRACK_WINNER_MARGIN = float(os.environ.get("SHORT_TRACK_WINNER_MARGIN", "0.25"))
COLOR_QUALITY_SATURATION_FRAC = float(os.environ.get("COLOR_QUALITY_SATURATION_FRAC", "0.10"))
COLOR_ROI_MODE = os.environ.get("COLOR_ROI_MODE", "multi")
COLOR_NEUTRAL_MODE = os.environ.get("COLOR_NEUTRAL_MODE", "adaptive")
COLOR_NORMALIZATION = os.environ.get("COLOR_NORMALIZATION", "none")
COLOR_QUALITY_MODE = os.environ.get("COLOR_QUALITY_MODE", "enhanced")
COLOR_FEATURE_MODE = os.environ.get("COLOR_FEATURE_MODE", "hsv")

# Optional VLM fallback. The network call is lazy and happens only after the
# local temporal result finalizes below the configured confidence threshold.
COLOR_AGENT_ENABLED = os.environ.get("COLOR_AGENT_ENABLED", "true").lower() == "true"
COLOR_AGENT_THRESHOLD = float(os.environ.get("COLOR_AGENT_THRESHOLD", "0.85"))
COLOR_AGENT_MODEL = os.environ.get(
    "COLOR_AGENT_MODEL", "google/gemini-3-flash-preview"
)
COLOR_AGENT_BASE_URL = os.environ.get(
    "COLOR_AGENT_BASE_URL", "https://openrouter.ai/api/v1"
)
COLOR_AGENT_API_KEY = os.environ.get(
    "COLOR_AGENT_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")
)
