"""VMS-independent vehicle detection and ByteTrack runtime helpers.

Both the live multi-camera driver and offline test harness use these helpers so
model selection, detector input size, tracker configuration, and tracker-output
adaptation stay identical.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

import live_feed.config as cfg

log = logging.getLogger(__name__)

IMGSZ = 640


def _trt_available() -> bool:
    return any(importlib.util.find_spec(name) for name in ("tensorrt", "tensorrt_cu12"))


def load_vehicle_model(device: str | None = None, *, prefer_engine: bool = True):
    """Load the production vehicle detector and return ``(model, model_path)``.

    ``device=None`` preserves the live driver's existing engine-first behavior.
    An explicit CPU device uses the equivalent PyTorch weights because a
    TensorRT engine cannot execute on CPU. CUDA uses/creates the production
    TensorRT engine exactly as the live driver does.
    """
    from ultralytics import YOLO

    pt_path = Path(cfg.VEHICLE_MODEL_PATH)
    engine_path = Path(cfg.VEHICLE_ENGINE_PATH)
    wants_engine = prefer_engine and (device is None or device.startswith("cuda"))

    if not pt_path.is_file() and not (wants_engine and engine_path.is_file()):
        raise FileNotFoundError(f"vehicle model weights not found: {pt_path}")

    if wants_engine and not engine_path.exists() and _trt_available():
        log.info("[models] exporting TRT engine from %s …", pt_path.name)
        try:
            YOLO(str(pt_path)).export(
                format="engine", half=True, dynamic=True,
                batch=cfg.BATCH_MAX, imgsz=IMGSZ, device=0,
            )
        except Exception as exc:
            log.warning("[models] engine export failed (%s); using .pt", exc)
    elif wants_engine and not engine_path.exists():
        log.info("[models] tensorrt not installed → using .pt (engine exports on first TRT install)")

    model_path = engine_path if wants_engine and engine_path.exists() else pt_path
    if not model_path.is_file():
        raise FileNotFoundError(f"vehicle model weights not found: {model_path}")
    log.info("[models] vehicle: %s", model_path.name)
    return YOLO(str(model_path), task="detect"), model_path


def create_vehicle_tracker(frame_rate: int = cfg.FPS):
    """Create ByteTrack from the production vehicle tracker YAML."""
    tracker_path = Path(cfg.TRACKER_CONFIG)
    if not tracker_path.is_file():
        raise FileNotFoundError(f"vehicle tracker config not found: {tracker_path}")
    tracker_data = yaml.safe_load(tracker_path.read_text(encoding="utf-8"))
    if not isinstance(tracker_data, dict):
        raise ValueError(f"invalid vehicle tracker config: {tracker_path}")

    from ultralytics.trackers.byte_tracker import BYTETracker

    return BYTETracker(SimpleNamespace(**tracker_data), frame_rate=frame_rate)


class TrackBoxes:
    """Adapt BYTETracker rows to the tensor-like input CameraPipeline expects."""

    def __init__(self, rows: np.ndarray):
        import torch

        self._t = (
            torch.as_tensor(rows.reshape(-1, rows.shape[-1]))
            if rows.size else torch.zeros((0, 8))
        )
        self.id = self._t[:, 4] if len(self._t) else None
        self.xyxy = self._t[:, :4]
        self.cls = self._t[:, 6] if self._t.shape[1] > 6 else torch.zeros(len(self._t))
        self.conf = self._t[:, 5] if len(self._t) else torch.zeros(0)

    def __len__(self) -> int:
        return len(self._t)

    @classmethod
    def empty(cls) -> "TrackBoxes":
        return cls(np.zeros((0, 8), dtype=np.float32))
