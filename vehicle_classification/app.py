from __future__ import annotations
import base64
import logging
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# vehicle_services/ must be on sys.path so 'common' is importable
_VS_DIR = Path(__file__).parent.parent
if str(_VS_DIR) not in sys.path:
    sys.path.insert(0, str(_VS_DIR))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import numpy as np

from common import annotate, config, image_io
from common.colour import estimate_colour
from common.detector import VehicleDetector
from common.schemas import ClassifyResponse, Detection
import vehicle_classification.config as vc_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="vehicle_classification", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_detector = VehicleDetector(config.VEHICLE_MODEL_PATH, config.CONF_THRESHOLD)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "vehicle_classification",
        "model_loaded": _detector.model_loaded,
    }


async def _decode_image(file: Optional[UploadFile], image_b64: Optional[str]):
    """Decode multipart file or base64 field into a numpy BGR array."""
    if file is None and image_b64 is None:
        raise HTTPException(status_code=400, detail="Provide 'file' (multipart) or 'image_b64' (form field)")
    source = await file.read() if file is not None else image_b64
    try:
        return image_io.load_image(source)
    except image_io.InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _encode_inline(ann_path: Optional[Path]) -> Optional[str]:
    if ann_path and ann_path.exists():
        import cv2
        buf = cv2.imread(str(ann_path))
        if buf is not None:
            _, encoded = cv2.imencode(".jpg", buf, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return base64.b64encode(encoded.tobytes()).decode()
    return None


@app.post("/classify", response_model=ClassifyResponse)
async def classify(
    file: Optional[UploadFile] = File(default=None),
    image_b64: Optional[str]   = Form(default=None),
    return_image_inline: bool  = Form(default=False),
    include_person: bool        = Form(default=False),
    conf: Optional[float]       = Form(default=None),
):
    request_id = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    image = await _decode_image(file, image_b64)
    h, w = image.shape[:2]

    detections = _detector.predict(image, include_person=include_person, conf_override=conf)
    _enrich_colours(image, detections)

    ann_path = annotate.annotate_and_save(image, detections, request_id)

    ann_b64: Optional[str] = None
    if return_image_inline:
        ann_b64 = _encode_inline(ann_path)

    return ClassifyResponse(
        request_id=request_id,
        timestamp=timestamp,
        image_size=[w, h],
        vehicle_count=len(detections),
        detections=detections,
        annotated_image=str(ann_path) if ann_path else None,
        annotated_image_b64=ann_b64,
    )


def _enrich_colours(image: np.ndarray, detections: list[Detection]) -> None:
    """Populate colour + colour_confidence on each detection in-place."""
    for det in detections:
        x1, y1, x2, y2 = det.bbox
        crop = image[y1:y2, x1:x2]
        det.colour, det.colour_confidence = estimate_colour(crop)


def _pick_best_crop_detection(detections: list[Detection], w: int, h: int) -> Detection:
    """Return the most-central, highest-confidence detection from a crop.

    Score = conf * (1 - 0.4 * normalised_dist_from_centre), where dist is in [0, 1].
    """
    best = detections[0]
    best_score = -1.0
    for det in detections:
        x1, y1, x2, y2 = det.bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        # Normalised Euclidean distance from image centre (max possible = 0.707)
        dist = math.sqrt(((cx / w) - 0.5) ** 2 + ((cy / h) - 0.5) ** 2) / 0.707
        score = det.type_confidence * (1.0 - 0.4 * dist)
        if score > best_score:
            best_score = score
            best = det
    return best


@app.post("/classify_crop", response_model=ClassifyResponse)
async def classify_crop(
    file: Optional[UploadFile] = File(default=None),
    image_b64: Optional[str]   = Form(default=None),
    return_image_inline: bool  = Form(default=False),
    include_person: bool        = Form(default=False),
    conf: Optional[float]       = Form(default=None),
):
    """Classify a pre-cropped vehicle image.

    Uses a lower confidence threshold than /classify because the caller asserts
    this is a vehicle. Never returns an empty detections list — falls back to
    a single 'unknown' detection if nothing passes the threshold.
    """
    request_id = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    image = await _decode_image(file, image_b64)
    h, w = image.shape[:2]

    crop_conf = conf if conf is not None else vc_config.CROP_CONF_THRESHOLD
    raw_detections = _detector.predict(image, include_person=include_person, conf_override=crop_conf)

    if raw_detections:
        best = _pick_best_crop_detection(raw_detections, w, h)
        best = Detection(id=0, bbox=best.bbox, type=best.type, type_confidence=best.type_confidence)
        detections = [best]
    else:
        detections = [Detection(id=0, bbox=[0, 0, w, h], type="unknown", type_confidence=0.0)]

    _enrich_colours(image, detections)

    ann_path = annotate.annotate_and_save(image, detections, request_id)

    ann_b64: Optional[str] = None
    if return_image_inline:
        ann_b64 = _encode_inline(ann_path)

    return ClassifyResponse(
        request_id=request_id,
        timestamp=timestamp,
        image_size=[w, h],
        vehicle_count=1,
        detections=detections,
        annotated_image=str(ann_path) if ann_path else None,
        annotated_image_b64=ann_b64,
    )


if __name__ == "__main__":
    import uvicorn
    log.info("Starting vehicle_classification on port %d", vc_config.PORT)
    uvicorn.run("vehicle_classification.app:app", host="0.0.0.0", port=vc_config.PORT, reload=False)
