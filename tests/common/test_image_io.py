from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest
import requests

from common import image_io


def _png_bytes() -> bytes:
    ok, encoded = cv2.imencode(".png", np.full((12, 16, 3), 80, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def test_valid_image_bytes_decode():
    assert image_io.load_image(_png_bytes()).shape == (12, 16, 3)


@pytest.mark.parametrize("payload", [b"", b"corrupt image"])
def test_empty_and_corrupt_bytes_fail_cleanly(payload):
    with pytest.raises(image_io.InvalidImageError):
        image_io.load_image(payload)


def test_plain_base64_is_strict():
    with pytest.raises(image_io.InvalidImageError):
        image_io.load_image("%%%")


@pytest.mark.parametrize("uri", ["data:image/png;base64", "data:image/png,abc", "data:image/png;base64,"])
def test_malformed_data_uri_fails_cleanly(uri):
    with pytest.raises(image_io.InvalidImageError):
        image_io.load_image(uri)


def test_valid_data_uri_decodes():
    value = "data:image/png;base64," + base64.b64encode(_png_bytes()).decode()
    assert image_io.load_image(value).shape == (12, 16, 3)


def test_unsupported_source_type_fails_cleanly():
    with pytest.raises(image_io.InvalidImageError):
        image_io.load_image(object())


def test_valid_local_file_decodes(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(_png_bytes())
    assert image_io.load_image(str(path)).shape == (12, 16, 3)


def test_url_failure_is_normalized(monkeypatch):
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(image_io.requests, "get", fail)
    with pytest.raises(image_io.InvalidImageError, match="Could not fetch"):
        image_io.load_image("https://example.invalid/image.jpg")

