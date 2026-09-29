from __future__ import annotations
import os
from pathlib import Path

_MODELS_DIR = Path(__file__).resolve().parent / "models"

PORT = int(os.environ.get("PLATE_RECOGNITION_PORT", "5021"))

PLATE_DETECTOR_PATH = os.environ.get(
    "PLATE_DETECTOR_PATH", str(_MODELS_DIR / "best_matricule.pt")
)
WORD_DETECTOR_PATH = os.environ.get(
    "WORD_DETECTOR_PATH", str(_MODELS_DIR / "tounes_word_detector.pt")
)

OCR_REC_MODEL = "PP-OCRv6_medium_rec"

PLATE_CONF   = float(os.environ.get("PLATE_CONF",   "0.25"))
DETECT_IMGSZ = int(os.environ.get("DETECT_IMGSZ",   "960"))
WORD_CONF    = float(os.environ.get("WORD_CONF",     "0.5"))
WORD_IMGSZ   = int(os.environ.get("WORD_IMGSZ",     "512"))

# Keep width aligned with live_feed.config.MIN_PLATE_W. The 20px height floor
# is the corresponding minimum usable text-line height for this 60px-wide gate.
MIN_PLATE_W  = int(os.environ.get("MIN_PLATE_W",     "60"))
MIN_PLATE_H  = int(os.environ.get("MIN_PLATE_H",     "20"))
