from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

import plate_recognition.config as config
from common.device import resolve_device

log = logging.getLogger(__name__)

_model = None
_device = resolve_device()
_load_lock = threading.Lock()
_lock  = threading.Lock()


def _load() -> None:
    global _model
    if _model is not None:
        return
    with _load_lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(str(config.WORD_DETECTOR_PATH))
            log.info("Loaded تونس word detector on %s: %s", _device, config.WORD_DETECTOR_PATH)


def is_loaded() -> bool:
    return _model is not None


def classify_type(crop: np.ndarray) -> tuple[str, float]:
    """Stretch crop to 512×512, word-spot تونس.

    Returns ('tounes', conf) if detected, ('other', 0.0) otherwise.
    Stretching (not letterboxing) is CRITICAL — the detector was trained on
    Roboflow 'Resize 512×512 Stretch'. Letterboxed input drops recall to ~43%.
    """
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return "other", 0.0

    global _model
    if _model is None:
        _load()

    stretched = cv2.resize(crop, (config.WORD_IMGSZ, config.WORD_IMGSZ))
    with _lock:
        results = _model.predict(
            stretched, conf=config.WORD_CONF, imgsz=config.WORD_IMGSZ,
            device=_device, verbose=False
        )[0]

    if len(results.boxes):
        return "tounes", float(results.boxes.conf.max())
    return "other", 0.0
