from __future__ import annotations

import sys
import threading
import time
import types
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config import TAXONOMY, VEHICLE_LABELS, normalize_vehicle_label
from common.detector import VehicleDetector
from common.device import resolve_device
from common.schemas import BoxPrediction, Detection, LabelConfidence
from plate_recognition.plate_detector import PlateDetector
from plate_recognition import plate_type


def test_canonical_taxonomy_and_required_mappings():
    assert VEHICLE_LABELS == {
        "bus", "car", "van", "truck", "motorcycle", "bicycle", "unknown",
    }
    assert normalize_vehicle_label("cyclist") == "bicycle"
    assert normalize_vehicle_label("camper") == "van"
    assert normalize_vehicle_label("car") == "car"
    assert normalize_vehicle_label("van") == "van"
    assert set(TAXONOMY.values()) <= VEHICLE_LABELS
    metadata = json.loads(
        (Path(__file__).parents[2] / "common/models/class_map.json").read_text()
    )
    assert set(metadata["service_taxonomy"].values()) <= VEHICLE_LABELS
    assert metadata["service_taxonomy"]["1"] == "van"
    assert metadata["service_taxonomy"]["3"] == "bicycle"


def test_bbox_is_canonical_xyxy_and_serializes_compatibly():
    detection = Detection(id=1, bbox=[1, 2, 10, 20], type="car", type_confidence=.8)
    assert detection.bbox == [1, 2, 10, 20]
    assert detection.model_dump()["bbox"] == [1, 2, 10, 20]
    prediction = BoxPrediction(bbox=[1, 2, 10, 20], confidence=.7)
    assert prediction.model_dump() == {"bbox": [1, 2, 10, 20], "confidence": .7}


@pytest.mark.parametrize("bbox", ([1, 2, 0, 20], [1, 2, 10, 1], [1, 2, 3]))
def test_invalid_bbox_is_rejected(bbox):
    with pytest.raises(ValidationError):
        Detection(id=1, bbox=bbox, type="car", type_confidence=.8)


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_confidence_accepts_unit_interval(confidence):
    assert LabelConfidence(label="car", confidence=confidence).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_rejects_out_of_range_values(confidence):
    with pytest.raises(ValidationError):
        LabelConfidence(label="car", confidence=confidence)


def test_device_resolution_cpu_and_auto():
    assert resolve_device("cpu", cuda_available=lambda: True) == "cpu"
    assert resolve_device("auto", cuda_available=lambda: False) == "cpu"
    assert resolve_device("auto", cuda_available=lambda: True) == "cuda:0"


def test_cuda_selection_and_safe_fallback():
    assert resolve_device("cuda", cuda_available=lambda: True) == "cuda:0"
    assert resolve_device("cuda:2", cuda_available=lambda: True) == "cuda:2"
    assert resolve_device("cuda:2", cuda_available=lambda: False) == "cpu"


def test_invalid_device_is_rejected():
    with pytest.raises(ValueError):
        resolve_device("gpu")


@pytest.mark.parametrize("detector_cls", [VehicleDetector, PlateDetector])
def test_detector_concurrent_first_load_happens_once(detector_cls, tmp_path, monkeypatch):
    weights = tmp_path / "model.pt"
    weights.touch()
    calls = []

    class FakeYOLO:
        def __init__(self, path):
            calls.append(path)
            time.sleep(0.02)

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))
    detector = detector_cls(str(weights), device="cpu")
    threads = [threading.Thread(target=detector.load) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert detector.model_loaded
    detector.load()
    assert len(calls) == 1


def test_plate_type_concurrent_first_load_happens_once(monkeypatch):
    calls = []

    class FakeYOLO:
        def __init__(self, path):
            calls.append(path)
            time.sleep(0.02)

    monkeypatch.setattr(plate_type, "_model", None)
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))
    threads = [threading.Thread(target=plate_type._load) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert plate_type.is_loaded()
    plate_type._load()
    assert len(calls) == 1
