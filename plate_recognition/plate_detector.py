from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

import plate_recognition.config as config
from common.device import resolve_device
from common.schemas import BoxPrediction

log = logging.getLogger(__name__)


class PlateDetector:
    def __init__(self, model_path: str = config.PLATE_DETECTOR_PATH, device: str | None = None):
        self.model_path = model_path
        self._model = None
        self.device = resolve_device(device)
        self._load_lock = threading.Lock()
        self._lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is None:
                from ultralytics import YOLO
                self._model = YOLO(self.model_path)
                log.info("Loaded plate detector on %s: %s", self.device, self.model_path)

    def predict(
        self,
        image: np.ndarray,
        conf: Optional[float] = None,
        imgsz: Optional[int] = None,
    ) -> list[BoxPrediction]:
        if self._model is None:
            if not Path(self.model_path).exists():
                log.warning("Plate detector weights not found at %s — returning []", self.model_path)
                return []
            self.load()

        effective_conf  = conf  if conf  is not None else config.PLATE_CONF
        effective_imgsz = imgsz if imgsz is not None else config.DETECT_IMGSZ

        with self._lock:
            results = self._model.predict(
                image, conf=effective_conf, imgsz=effective_imgsz,
                device=self.device, verbose=False
            )[0]

        out: list[BoxPrediction] = []
        for b in results.boxes:
            x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
            out.append(BoxPrediction(
                bbox=[x1, y1, x2, y2],
                confidence=float(b.conf[0]),
            ))
        return out
