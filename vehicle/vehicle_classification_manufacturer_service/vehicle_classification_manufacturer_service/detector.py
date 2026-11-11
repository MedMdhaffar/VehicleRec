"""YOLO vehicle detector and ByteTrack adapter extracted from live_feed.multistream."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from .config import MODEL_PATH, TRACKER_CONFIG, TRACK_CONF, VEHICLE_DETECTION_CLASS_IDS

log = logging.getLogger(__name__)
IMGSZ = 640


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def suppress_duplicate_boxes(boxes, threshold=0.90):
    """Suppress almost identical proposals across detector classes before tracking."""
    kept = []
    coordinates = boxes.xyxy
    for index in np.argsort(-boxes.conf, kind="stable"):
        box = coordinates[index]
        duplicate = False
        for previous in kept:
            other = coordinates[previous]
            overlap = np.maximum(0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2]))
            intersection = float(np.prod(overlap))
            area = float(np.prod(np.maximum(0, box[2:] - box[:2])))
            other_area = float(np.prod(np.maximum(0, other[2:] - other[:2])))
            union = area + other_area - intersection
            if union > 0 and intersection / union >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(int(index))
    if len(kept) < len(coordinates):
        log.debug("Suppressed %d duplicate vehicle boxes", len(coordinates) - len(kept))
    return boxes[kept]


class VehicleDetectorTracker:
    """One model and one persistent ByteTrack instance per service."""

    def __init__(self, device: str = "auto", model_path: str | Path | None = None, tracker_path: str | Path | None = None, frame_rate: float = 15.0) -> None:
        from ultralytics import YOLO
        from ultralytics.trackers.byte_tracker import BYTETracker

        self.model_path = Path(model_path or MODEL_PATH).expanduser().resolve()
        self.tracker_path = Path(tracker_path or TRACKER_CONFIG).expanduser().resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Vehicle model not found: {self.model_path}")
        if not self.tracker_path.is_file():
            raise FileNotFoundError(f"ByteTrack configuration not found: {self.tracker_path}")
        self._id_offset = 0
        self._max_track_id = 0
        self.device = resolve_device(device)
        log.info("Loading vehicle detector from %s on %s", self.model_path, self.device)
        self.model = YOLO(str(self.model_path), task="detect")
        with self.tracker_path.open(encoding="utf-8") as stream:
            tracker_args = SimpleNamespace(**yaml.safe_load(stream))
        self.tracker = BYTETracker(tracker_args, frame_rate=frame_rate)
        log.info("Created ByteTrack tracker from %s", self.tracker_path)

    def reset(self):
        """Discard motion associations while preserving globally unique output IDs."""
        self._id_offset = self._max_track_id
        self.tracker.reset()

    def process(self, frame: np.ndarray) -> np.ndarray:
        prediction = self.model.predict(
            frame,
            conf=TRACK_CONF,
            imgsz=IMGSZ,
            classes=list(VEHICLE_DETECTION_CLASS_IDS),
            device=self.device,
            verbose=False,
        )[0]
        boxes = suppress_duplicate_boxes(prediction.boxes.cpu().numpy())
        rows = self.tracker.update(boxes, frame)
        if len(rows):
            rows = rows.copy()
            rows[:, 4] += self._id_offset
            self._max_track_id = max(self._max_track_id, int(rows[:, 4].max()))
        return rows
