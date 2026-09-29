from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np
import pytest

from plate_recognition import app as plate_app
from plate_recognition import config, ocr, plate_type
from common.schemas import BoxPrediction


class _Reader:
    def read_with_raw(self, crop):
        assert crop.size
        return "١٢٣4 ٥٦٧", "1234 567".replace(" ", ""), 0.9


def _image(width=200, height=100):
    return np.full((height, width, 3), 127, dtype=np.uint8)


@pytest.fixture
def successful_models(monkeypatch):
    monkeypatch.setattr(ocr, "get_reader", lambda: _Reader())
    monkeypatch.setattr(plate_type, "classify_type", lambda crop: ("tounes", 0.8))
    monkeypatch.setattr(plate_app, "_maybe_collect", lambda *args: None)


def test_normal_valid_plate_box(successful_models):
    fixture = cv2.imread(str(Path(plate_app.__file__).parent / "image1.jpg"))
    assert fixture is not None
    h, w = fixture.shape[:2]
    plate = plate_app._read_one_plate(fixture, [0, 0, w, h], "req")
    assert plate is not None
    assert plate.bbox == [0, 0, w, h]
    assert plate.raw_number == "١٢٣4 ٥٦٧"
    assert plate.number == "1234567"


def test_box_partially_outside_is_clamped(successful_models):
    plate = plate_app._read_one_plate(_image(), [-10, -5, 100, 40], "req")
    assert plate is not None
    assert plate.bbox == [0, 0, 100, 40]


@pytest.mark.parametrize("box", ([20, 10, 20, 50], [20, 10, 100, 10]))
def test_zero_sized_boxes_are_rejected(box, successful_models):
    assert plate_app._read_one_plate(_image(), box, "req") is None


def test_very_small_plate_is_rejected(successful_models):
    assert plate_app._read_one_plate(_image(), [0, 0, 59, 19], "req") is None


@pytest.mark.parametrize("bad", [np.empty((0, 0, 3), dtype=np.uint8), None, b"corrupt"])
def test_empty_or_invalid_crop_never_reaches_models(bad, monkeypatch):
    monkeypatch.setattr(ocr, "get_reader", lambda: pytest.fail("OCR called"))
    monkeypatch.setattr(plate_type, "classify_type", lambda crop: pytest.fail("type called"))
    assert plate_app._read_one_plate(bad, [0, 0, 100, 40], "req") is None


def test_arabic_indic_digit_normalization():
    assert ocr.normalize_digits("١٢٣٤ ٥٦٧") == "1234 567"


def test_western_digit_normalization_is_unchanged():
    assert ocr.normalize_digits("1234 567") == "1234 567"


def test_ocr_parse_preserves_raw_and_normalized_values():
    reader = object.__new__(ocr.PlateReader)
    raw, normalized, confidence = reader._parse([
        {"rec_text": "١٢٣٤ ٥٦٧", "rec_score": 0.8},
    ])
    assert raw == "١٢٣٤ ٥٦٧"
    assert normalized == "1234567"
    assert confidence == 0.8


def test_ocr_initialization_is_lazy_cached_and_controlled(monkeypatch):
    calls = []

    def fail(_model):
        calls.append(1)
        raise RuntimeError("no OCR runtime")

    monkeypatch.setattr(ocr, "_reader", None)
    monkeypatch.setattr(ocr, "_reader_error", None)
    monkeypatch.setattr(ocr, "PlateReader", fail)
    assert not ocr.is_loaded()
    with pytest.raises(ocr.OCRUnavailableError):
        ocr.get_reader()
    with pytest.raises(ocr.OCRUnavailableError):
        ocr.get_reader()
    assert len(calls) == 1


def test_ocr_reader_is_initialized_once_and_reused(monkeypatch):
    instance = object()
    calls = []

    def create(_model):
        calls.append(1)
        return instance

    monkeypatch.setattr(ocr, "_reader", None)
    monkeypatch.setattr(ocr, "_reader_error", None)
    monkeypatch.setattr(ocr, "PlateReader", create)
    assert ocr.get_reader() is instance
    assert ocr.get_reader() is instance
    assert len(calls) == 1


def test_ocr_initialization_failure_returns_plate_warning(monkeypatch):
    monkeypatch.setattr(ocr, "get_reader", lambda: (_ for _ in ()).throw(ocr.OCRUnavailableError("offline")))
    monkeypatch.setattr(plate_type, "classify_type", lambda crop: ("tounes", 0.75))
    monkeypatch.setattr(plate_app, "_maybe_collect", lambda *args: None)
    plate = plate_app._read_one_plate(_image(), [0, 0, 100, 40], "req")
    assert plate is not None
    assert plate.number == ""
    assert plate.type == "tounes"
    assert "OCR unavailable" in plate.warning


def test_multiple_plates_continue_after_one_failure(monkeypatch):
    image = _image()
    monkeypatch.setattr(
        plate_app._detector,
        "predict",
        lambda *_args, **_kwargs: [
            BoxPrediction(bbox=[0, 0, 100, 40], confidence=.9),
            BoxPrediction(bbox=[100, 0, 200, 40], confidence=.8),
        ],
    )
    calls = []

    def read_one(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bad plate")
        return plate_app.PlateDetection(
            bbox=[100, 0, 200, 40], number="123", raw_number="123",
            digits_conf=.9, type="other", type_conf=0,
        )

    monkeypatch.setattr(plate_app, "_read_one_plate", read_one)
    monkeypatch.setattr(plate_app, "_decode_image", lambda *_: asyncio.sleep(0, result=image))
    monkeypatch.setattr(plate_app, "_annotate_and_save", lambda *_: None)
    response = asyncio.run(plate_app.read_plate())
    assert response.plate_count == 1
    assert response.plates[0].number == "123"


def test_read_plate_crop_safely_handles_unusable_crop(monkeypatch):
    image = np.empty((0, 0, 3), dtype=np.uint8)
    monkeypatch.setattr(plate_app, "_decode_image", lambda *_: asyncio.sleep(0, result=image))
    monkeypatch.setattr(plate_app, "_annotate_and_save", lambda *_: None)
    monkeypatch.setattr(ocr, "get_reader", lambda: pytest.fail("OCR called"))
    monkeypatch.setattr(plate_type, "classify_type", lambda crop: pytest.fail("type called"))

    response = asyncio.run(plate_app.read_plate_crop())

    assert response.plate_count == 0
    assert response.plates == []


def test_type_classifier_keeps_direct_512_stretch(monkeypatch):
    seen = {}

    class Model:
        def predict(self, image, **kwargs):
            seen["shape"] = image.shape
            seen["kwargs"] = kwargs
            return [type("Result", (), {"boxes": []})()]

    monkeypatch.setattr(plate_type, "_model", Model())
    assert plate_type.classify_type(_image(120, 30)) == ("other", 0.0)
    assert seen["shape"][:2] == (512, 512)
    assert seen["kwargs"]["imgsz"] == 512


def test_validated_word_conf_is_unchanged():
    assert config.WORD_CONF == 0.5
