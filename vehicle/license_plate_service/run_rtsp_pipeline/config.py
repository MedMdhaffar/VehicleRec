from __future__ import annotations
import os
from pathlib import Path

_MODELS_DIR = Path(__file__).resolve().parent / "models"

PORT = int(os.environ.get("PLATE_RECOGNITION_PORT", "5021"))

PLATE_DETECTOR_PATH = os.environ.get(
    "PLATE_DETECTOR_PATH", str(Path.home() / ".cache/open-image-models/yolo-v9-t-640-license-plate-end2end/yolo-v9-t-640-license-plates-end2end.onnx")
)
WORD_DETECTOR_PATH = os.environ.get(
    "WORD_DETECTOR_PATH", str(_MODELS_DIR / "tounes_word_detector.pt")
)

OCR_REC_MODEL = "PP-OCRv6_medium_rec"

PLATE_CONF   = float(os.environ.get("PLATE_CONF",   "0.25"))
DETECT_IMGSZ = int(os.environ.get("DETECT_IMGSZ",   "640"))
WORD_CONF    = float(os.environ.get("WORD_CONF",     "0.5"))
WORD_IMGSZ   = int(os.environ.get("WORD_IMGSZ",     "512"))

# Marker localization runs at a low floor so weak visual evidence remains
# observable, but only the validated reliability threshold may resolve OCR.
MARKER_INFERENCE_CONF = float(os.environ.get("MARKER_INFERENCE_CONF", "0.05"))
MARKER_RELIABLE_CONF = float(os.environ.get("MARKER_RELIABLE_CONF", str(WORD_CONF)))
MARKER_REGION_OCR_CONF = float(os.environ.get("MARKER_REGION_OCR_CONF", "0.5"))
PLATE_DEDUP_IOU = float(os.environ.get("PLATE_DEDUP_IOU", "0.8"))

# Local private-plate acceptance thresholds.
PRIVATE_ROUTE_MIN_OCR_CONF = float(os.environ.get("PRIVATE_ROUTE_MIN_OCR_CONF", "0.90"))
PRIVATE_ROUTE_MIN_MARKER_CONF = float(
    os.environ.get("PRIVATE_ROUTE_MIN_MARKER_CONF", str(MARKER_RELIABLE_CONF))
)

CAPTURE_ROOT = Path(os.environ.get("PLATE_CAPTURE_DIR", str(_MODELS_DIR.parent / "data_capture")))

# Fixed t-640 ONNX contract. Providers are explicit; never infer GPU from an environment name.
PLATE_DETECTOR_PROVIDERS = os.environ.get("PLATE_DETECTOR_PROVIDERS", "CPUExecutionProvider")
PLATE_DETECTOR_ALLOW_CPU_FALLBACK = os.environ.get("PLATE_DETECTOR_ALLOW_CPU_FALLBACK", "0") == "1"
PLATE_DETECTOR_INTRA_OP_THREADS = int(os.environ.get("PLATE_DETECTOR_INTRA_OP_THREADS", "2"))
PLATE_DETECTOR_INTER_OP_THREADS = int(os.environ.get("PLATE_DETECTOR_INTER_OP_THREADS", "1"))
