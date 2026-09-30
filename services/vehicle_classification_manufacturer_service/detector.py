"""YOLO vehicle detector and ByteTrack adapter extracted from live_feed.multistream."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from .classification import STREETDET_VEHICLE_CLASS_IDS
from .config import MODEL_PATH, TRACKER_CONFIG, TRACK_CONF

log = logging.getLogger(__name__)
IMGSZ = 640


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


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
        self.device = resolve_device(device)
        log.info("Loading vehicle detector from %s on %s", self.model_path, self.device)
        self.model = YOLO(str(self.model_path), task="detect")
        with self.tracker_path.open(encoding="utf-8") as stream:
            tracker_args = SimpleNamespace(**yaml.safe_load(stream))
        self.tracker = BYTETracker(tracker_args, frame_rate=frame_rate)
        log.info("Created ByteTrack tracker from %s", self.tracker_path)

    def process(self, frame: np.ndarray) -> np.ndarray:
        prediction = self.model.predict(
            frame,
            conf=TRACK_CONF,
            imgsz=IMGSZ,
            classes=list(STREETDET_VEHICLE_CLASS_IDS),
            device=self.device,
            verbose=False,
        )[0]
        return self.tracker.update(prediction.boxes.cpu().numpy(), frame)
