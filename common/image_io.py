from __future__ import annotations
import base64
import os
import logging

import cv2
import numpy as np
import requests

log = logging.getLogger(__name__)


class InvalidImageError(ValueError):
    """External image input is empty, malformed, unavailable, or undecodable."""


def _decode_bytes(raw: bytes | bytearray) -> np.ndarray:
    if not raw:
        raise InvalidImageError("Image payload is empty")
    try:
        arr = np.frombuffer(raw, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except (cv2.error, ValueError, TypeError) as exc:
        raise InvalidImageError("Could not decode image — unsupported format or corrupt data") from exc
    if img is None or img.size == 0:
        raise InvalidImageError("Could not decode image — unsupported format or corrupt data")
    return img


def _decode_base64(payload: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise InvalidImageError("Image data is not valid base64") from exc


def load_image(source: bytes | str) -> np.ndarray:
    """Decode an image from bytes, base64 string, URL, or local path.

    Callers handling a FastAPI UploadFile should read bytes first:
        data = await file.read()
        img  = load_image(data)

    Raises ValueError if the image cannot be decoded.
    """
    if isinstance(source, (bytes, bytearray)):
        return _decode_bytes(source)

    elif isinstance(source, str):
        if source.startswith("data:"):
            # data URI: data:image/jpeg;base64,<b64>
            try:
                header, payload = source.split(",", 1)
            except ValueError as exc:
                raise InvalidImageError("Malformed image data URI") from exc
            if ";base64" not in header.lower():
                raise InvalidImageError("Image data URI must use base64 encoding")
            return _decode_bytes(_decode_base64(payload))

        elif source.startswith(("http://", "https://")):
            try:
                resp = requests.get(source, timeout=10)
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise InvalidImageError(f"Could not fetch image URL: {exc}") from exc
            return _decode_bytes(resp.content)

        elif os.path.exists(source):
            try:
                img = cv2.imread(source)
            except cv2.error as exc:
                raise InvalidImageError("Could not decode image — unsupported format or corrupt data") from exc
            if img is None or img.size == 0:
                raise InvalidImageError("Could not decode image — unsupported format or corrupt data")
            return img

        else:
            return _decode_bytes(_decode_base64(source))

    else:
        raise InvalidImageError(f"Unsupported image source type: {type(source)}")
