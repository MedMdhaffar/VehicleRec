from __future__ import annotations

from pathlib import Path

import numpy as np

from common.colour import estimate_colour, estimate_colour_detailed
from common.schemas import Detection
from live_feed.perf import PerfMeter
from live_feed.pipeline import CameraPipeline
from vehicle_classification import app as vehicle_app


class _Values:
    def __init__(self, values):
        self._values = values

    def int(self):
        return self

    def tolist(self):
        return self._values


class _TrackedBoxes:
    id = _Values([42])
    xyxy = _Values([[1, 1, 5, 5]])
    cls = _Values([2])
    conf = _Values([0.9])

    def __len__(self):
        return 1


def _bare_pipeline() -> CameraPipeline:
    pipeline = CameraPipeline.__new__(CameraPipeline)
    pipeline._cam_id = "test"
    pipeline._perf = PerfMeter()
    pipeline._active = {}
    pipeline._roi = None
    pipeline._anchors = []
    pipeline._last_frame = None
    pipeline._last_seen_ids = set()
    pipeline._last_ts = 0.0
    pipeline._eligible_parked = {}
    return pipeline


def test_hsv_estimator_preserves_dominant_colour_and_confidence():
    black = np.zeros((20, 20, 3), dtype=np.uint8)
    red = np.full((20, 20, 3), (0, 0, 255), dtype=np.uint8)

    assert estimate_colour_detailed(black, roi_mode="legacy", neutral_mode="legacy").label == "black"
    assert estimate_colour_detailed(red, roi_mode="legacy", neutral_mode="legacy").label == "red"
    assert estimate_colour(np.empty((0, 0, 3), dtype=np.uint8)) == ("unknown", 0.0)


def test_image_enrichment_passes_the_detection_bbox_crop_to_hsv(monkeypatch):
    image = np.arange(8 * 9 * 3, dtype=np.uint8).reshape(8, 9, 3)
    detection = Detection(id=0, bbox=[2, 1, 7, 6], type="car", type_confidence=0.9)
    received = []

    def fake_estimate(crop):
        received.append(crop.copy())
        return "blue", 0.75

    monkeypatch.setattr(vehicle_app, "estimate_colour", fake_estimate)
    vehicle_app._enrich_colours(image, [detection])

    assert np.array_equal(received[0], image[1:6, 2:7])
    assert detection.colour == "blue"
    assert detection.colour_confidence == 0.75


def test_live_colour_collects_five_valid_samples_before_early_lock(monkeypatch):
    calls = []

    def fake_estimate(crop):
        calls.append(crop.shape)
        return "black", 0.8

    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", lambda crop: type(
        "Estimate", (), {"label": fake_estimate(crop)[0], "confidence": 0.8,
                          "diagnostics": lambda self: {}})())
    pipeline = _bare_pipeline()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    for frame_idx in range(6):
        pipeline.ingest(frame, _TrackedBoxes(), frame_idx=frame_idx)

    profile = pipeline.active_profiles[42]
    assert len(calls) == 5
    assert [sample.label for sample in profile._colour_samples] == ["black"] * 5
    assert profile.colour == "black"
    assert profile.colour_conf == 1.0
    assert profile._colour_finalized is True


def test_vehicle_type_still_locks_at_two_frames(monkeypatch):
    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", lambda crop: type(
        "Estimate", (), {"label": "black", "confidence": 0.8,
                          "diagnostics": lambda self: {}})())
    pipeline = _bare_pipeline()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    pipeline.ingest(frame, _TrackedBoxes(), frame_idx=0)
    assert pipeline.active_profiles[42]._type_locked is False
    pipeline.ingest(frame, _TrackedBoxes(), frame_idx=1)
    assert pipeline.active_profiles[42]._type_locked is True
    assert len(pipeline.active_profiles[42]._type_votes) == 2


def test_standalone_colour_runtime_has_no_learned_colour_dependencies():
    source = (Path(__file__).parents[2] / "scripts/evaluate_vehicle_colour_hsv.py").read_text(encoding="utf-8")
    lowered = source.lower()

    assert "torchvision" not in lowered
    assert "import torch" not in lowered
