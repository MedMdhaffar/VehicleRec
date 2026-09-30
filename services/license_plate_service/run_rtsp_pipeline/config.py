from __future__ import annotations
import os
from pathlib import Path

_MODELS_DIR = Path(__file__).resolve().parent / "models"

PORT = int(os.environ.get("PLATE_RECOGNITION_PORT", "5021"))

PLATE_DETECTOR_PATH = os.environ.get(
    "PLATE_DETECTOR_PATH", str(_MODELS_DIR / "best.pt")
)
WORD_DETECTOR_PATH = os.environ.get(
    "WORD_DETECTOR_PATH", str(_MODELS_DIR / "tounes_word_detector.pt")
)

OCR_REC_MODEL = "PP-OCRv6_medium_rec"

PLATE_CONF   = float(os.environ.get("PLATE_CONF",   "0.25"))
DETECT_IMGSZ = int(os.environ.get("DETECT_IMGSZ",   "960"))
WORD_CONF    = float(os.environ.get("WORD_CONF",     "0.5"))
WORD_IMGSZ   = int(os.environ.get("WORD_IMGSZ",     "512"))

# Marker localization runs at a low floor so weak visual evidence remains
# observable, but only the validated reliability threshold may resolve OCR.
MARKER_INFERENCE_CONF = float(os.environ.get("MARKER_INFERENCE_CONF", "0.05"))
MARKER_RELIABLE_CONF = float(os.environ.get("MARKER_RELIABLE_CONF", str(WORD_CONF)))
MARKER_REGION_OCR_CONF = float(os.environ.get("MARKER_REGION_OCR_CONF", "0.5"))
PLATE_DEDUP_IOU = float(os.environ.get("PLATE_DEDUP_IOU", "0.8"))

# Conservative prototype router. These are intentionally separate from the
# experimental narrow-fallback thresholds.
PRIVATE_ROUTE_MIN_OCR_CONF = float(os.environ.get("PRIVATE_ROUTE_MIN_OCR_CONF", "0.90"))
PRIVATE_ROUTE_MIN_MARKER_CONF = float(
    os.environ.get("PRIVATE_ROUTE_MIN_MARKER_CONF", str(MARKER_RELIABLE_CONF))
)

# External agent calls require both an explicit enable flag and an API key.
OPENROUTER_ENABLED = os.environ.get("OPENROUTER_ENABLED", "false").lower() in {"1", "true", "yes"}
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
OPENROUTER_TIMEOUT_SECONDS = float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "20"))
OPENROUTER_RETRY_LIMIT = int(os.environ.get("OPENROUTER_RETRY_LIMIT", "1"))
AGENT_MIN_CONFIDENCE = float(os.environ.get("AGENT_MIN_CONFIDENCE", "0.70"))

CAPTURE_ROOT = Path(os.environ.get("PLATE_CAPTURE_DIR", str(_MODELS_DIR.parent / "data_capture")))
