from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from common.config import CONF_THRESHOLD, MIN_BOX_AREA_FRAC, PER_CLASS_CONF, TAXONOMY
from common.device import resolve_device
from common.schemas import Detection

log = logging.getLogger(__name__)


class VehicleDetector:
    def __init__(self, model_path: str, conf: float = CONF_THRESHOLD, device: str | None = None):
        self.model_path = model_path
        self.conf = conf
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
                log.info("Loaded vehicle model on %s: %s", self.device, self.model_path)

    def predict(
        self,
        image: np.ndarray,
        include_person: bool = False,
        conf_override: Optional[float] = None,
    ) -> list[Detection]:
        if self._model is None:
            if not Path(self.model_path).exists():
                log.warning("Model weights not found at %s — returning []", self.model_path)
                return []
            self.load()

        effective_conf = conf_override if conf_override is not None else self.conf
        h, w = image.shape[:2]
        image_area = h * w

        with self._lock:
            results = self._model(
                image, conf=effective_conf, device=self.device, verbose=False
            )[0]

        detections: list[Detection] = []
        det_id = 0
        for box in results.boxes:
            cls_id = int(box.cls)
            conf_val = round(float(box.conf), 4)

            # Per-class confidence override (post-filter)
            if cls_id in PER_CLASS_CONF and conf_val < PER_CLASS_CONF[cls_id]:
                continue

            mapped_type = TAXONOMY.get(cls_id, "unknown")

            # Person is outside the canonical vehicle taxonomy. Preserve the
            # include_person compatibility switch by exposing it as unknown.
            if cls_id == 5 and not include_person:
                continue

            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]

            # Minimum box size gate
            box_area = max(0, x2 - x1) * max(0, y2 - y1)
            if image_area > 0 and (box_area / image_area) < MIN_BOX_AREA_FRAC:
                continue

            detections.append(Detection(
                id=det_id,
                bbox=[x1, y1, x2, y2],
                type=mapped_type,
                type_confidence=conf_val,
            ))
            det_id += 1

        return detections
