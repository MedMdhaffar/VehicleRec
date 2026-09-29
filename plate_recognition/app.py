from __future__ import annotations

import base64
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# vehicle_services/ must be on sys.path so 'common' is importable
_VS_DIR = Path(__file__).parent.parent
if str(_VS_DIR) not in sys.path:
    sys.path.insert(0, str(_VS_DIR))

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from common import config as common_config, image_io
from plate_recognition import ocr as ocr_module
from plate_recognition import plate_type
from plate_recognition.plate_detector import PlateDetector
from plate_recognition.schemas import PlateDetection, PlateRecognitionResponse
import plate_recognition.config as config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="plate_recognition", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Detector is lazy internally. OCR is obtained only when a valid crop needs it.
_detector = PlateDetector(config.PLATE_DETECTOR_PATH)

# Results directory (shared with vehicle_classification)
_RESULTS_DIR = common_config.RESULTS_DIR
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Deployment collection dir: accumulates rare/uncertain plates for future retraining
_REVIEW_DIR = Path(__file__).resolve().parent / "review"
_REVIEW_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _decode_image(file: Optional[UploadFile], image_b64: Optional[str]) -> np.ndarray:
    if file is None and image_b64 is None:
        raise HTTPException(status_code=400, detail="Provide 'file' (multipart) or 'image_b64' (form field)")
    source = await file.read() if file is not None else image_b64
    try:
        return image_io.load_image(source)
    except image_io.InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _encode_inline(ann_path: Optional[Path]) -> Optional[str]:
    if ann_path and ann_path.exists():
        buf = cv2.imread(str(ann_path))
        if buf is not None:
            _, encoded = cv2.imencode(".jpg", buf, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return base64.b64encode(encoded.tobytes()).decode()
    return None


def _annotate_and_save(image: np.ndarray, plates: list[PlateDetection], request_id: str) -> Optional[Path]:
    try:
        ann = image.copy()
        color = (0, 200, 80)
        for p in plates:
            x1, y1, x2, y2 = p.bbox
            cv2.rectangle(ann, (x1, y1), (x2, y2), color, 2)
            label = f"{p.number} | {p.type} {p.type_conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            ty = max(y1 - 4, th + 4)
            cv2.rectangle(ann, (x1, ty - th - 4), (x1 + tw + 4, ty + 2), color, -1)
            cv2.putText(ann, label, (x1 + 2, ty - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"plate_{ts}_{request_id[:8]}.jpg"
        path = _RESULTS_DIR / fname
        cv2.imwrite(str(path), ann, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Evict oldest files (keep last 50)
        files = sorted(_RESULTS_DIR.glob("plate_*.jpg"))
        for old in files[:-50]:
            try:
                old.unlink()
            except OSError:
                pass

        return path
    except Exception as exc:
        log.warning("annotate_and_save failed: %s", exc)
        return None


def _maybe_collect(crop: np.ndarray, plate: PlateDetection, request_id: str) -> None:
    """Save uncertain/other-type crops for future classifier retraining."""
    try:
        if plate.type == "other" or plate.type_conf < config.WORD_CONF + 0.1:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            stem = f"{ts}_{request_id[:8]}"
            cv2.imwrite(str(_REVIEW_DIR / f"{stem}.jpg"), crop)
            meta = {
                "request_id": request_id,
                "number": plate.number,
                "type": plate.type,
                "type_conf": plate.type_conf,
                "digits_conf": plate.digits_conf,
            }
            (_REVIEW_DIR / f"{stem}.json").write_text(json.dumps(meta, indent=2))
    except Exception as exc:
        log.warning("review collection failed: %s", exc)


def _clamp_plate_box(image: np.ndarray, bbox: list[int]) -> Optional[list[int]]:
    """Clamp a box to the image and reject degenerate or OCR-unusable boxes."""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0 or image.ndim < 2:
        return None
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, x2 = min(max(x1, 0), w), min(max(x2, 0), w)
    y1, y2 = min(max(y1, 0), h), min(max(y2, 0), h)
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    if x2 - x1 < config.MIN_PLATE_W or y2 - y1 < config.MIN_PLATE_H:
        return None
    return [x1, y1, x2, y2]


def _read_one_plate(image: np.ndarray, bbox: list[int], request_id: str) -> Optional[PlateDetection]:
    bbox = _clamp_plate_box(image, bbox)
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    warnings: list[str] = []
    raw_number, number, digits_conf = "", "", 0.0
    try:
        raw_number, number, digits_conf = ocr_module.get_reader().read_with_raw(crop)
    except Exception as exc:
        warnings.append(f"OCR unavailable: {exc}")
        log.warning("plate OCR failed: %s", exc)

    ptype, type_conf = "other", 0.0
    try:
        ptype, type_conf = plate_type.classify_type(crop)
    except Exception as exc:
        warnings.append(f"plate type unavailable: {exc}")
        log.warning("plate type classification failed: %s", exc)
    plate = PlateDetection(
        bbox=bbox,
        number=number,
        raw_number=raw_number,
        digits_conf=round(digits_conf, 4),
        type=ptype,
        type_conf=round(type_conf, 4),
        warning="; ".join(warnings) or None,
    )
    _maybe_collect(crop, plate, request_id)
    return plate


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "plate_recognition",
        "models_loaded": {
            "detector": _detector.model_loaded,
            "word": plate_type.is_loaded(),
            "ocr": ocr_module.is_loaded(),
        },
    }


@app.post("/read_plate", response_model=PlateRecognitionResponse)
async def read_plate(
    file: Optional[UploadFile] = File(default=None),
    image_b64: Optional[str]   = Form(default=None),
    return_image_inline: bool  = Form(default=False),
    conf: Optional[float]      = Form(default=None),
):
    """Detect all plates in a frame, then OCR + classify each one."""
    request_id = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    image = await _decode_image(file, image_b64)
    h, w = image.shape[:2]

    boxes = _detector.predict(image, conf=conf)
    plates = []
    for box in boxes:
        x1, y1, x2, y2 = box.bbox
        try:
            plate = _read_one_plate(image, [x1, y1, x2, y2], request_id)
            if plate is not None:
                plates.append(plate)
        except Exception as exc:
            log.warning("skipping failed plate box %s: %s", (x1, y1, x2, y2), exc)

    ann_path = _annotate_and_save(image, plates, request_id)

    ann_b64: Optional[str] = None
    if return_image_inline:
        ann_b64 = _encode_inline(ann_path)

    return PlateRecognitionResponse(
        request_id=request_id,
        timestamp=timestamp,
        image_size=[w, h],
        plate_count=len(plates),
        plates=plates,
        annotated_image=str(ann_path) if ann_path else None,
        annotated_image_b64=ann_b64,
    )


@app.post("/read_plate_crop", response_model=PlateRecognitionResponse)
async def read_plate_crop(
    file: Optional[UploadFile] = File(default=None),
    image_b64: Optional[str]   = Form(default=None),
    return_image_inline: bool  = Form(default=False),
):
    """OCR + classify a tight plate crop — skip plate detection."""
    request_id = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    image = await _decode_image(file, image_b64)
    h, w = image.shape[:2]

    plate = _read_one_plate(image, [0, 0, w, h], request_id)
    plates = [plate] if plate is not None else []

    ann_path = _annotate_and_save(image, plates, request_id)

    ann_b64: Optional[str] = None
    if return_image_inline:
        ann_b64 = _encode_inline(ann_path)

    return PlateRecognitionResponse(
        request_id=request_id,
        timestamp=timestamp,
        image_size=[w, h],
        plate_count=len(plates),
        plates=plates,
        annotated_image=str(ann_path) if ann_path else None,
        annotated_image_b64=ann_b64,
    )


if __name__ == "__main__":
    import uvicorn
    log.info("Starting plate_recognition on port %d", config.PORT)
    uvicorn.run("plate_recognition.app:app", host="0.0.0.0", port=config.PORT, reload=False)
