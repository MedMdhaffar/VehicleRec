"""Frozen reference-manifest validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import PluginError
from .utils import load_bgr, sha256_file


def load_reference_manifest(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = path.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Cannot read reference manifest {path}: {exc}") from exc
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, list) or not images:
        raise PluginError("Reference manifest must contain a non-empty images list.")
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: dict[str, str] = {}
    for item in images:
        if not isinstance(item, dict):
            raise PluginError("Every reference-manifest image must be an object.")
        image_id = str(item.get("id", ""))
        category = str(item.get("category", ""))
        if not image_id or image_id in seen_ids:
            raise PluginError(f"Missing or duplicate reference ID: {image_id!r}")
        if not category:
            raise PluginError(f"Reference {image_id} has no category.")
        image_path = Path(str(item.get("path", "")))
        image_path = image_path if image_path.is_absolute() else (path.parent / image_path).resolve()
        if not image_path.is_file():
            raise PluginError(f"Reference image is missing: {image_path}")
        image = load_bgr(image_path)
        expected = [int(item.get("expected_width", -1)), int(item.get("expected_height", -1))]
        actual = [image.shape[1], image.shape[0]]
        if expected != actual:
            raise PluginError(f"Reference {image_id} dimensions {actual} do not match {expected}.")
        digest = sha256_file(image_path)
        if digest != item.get("sha256"):
            raise PluginError(f"Reference {image_id} SHA-256 mismatch.")
        if digest in seen_hashes:
            raise PluginError(f"Exact duplicate references: {seen_hashes[digest]} and {image_id}.")
        seen_ids.add(image_id)
        seen_hashes[digest] = image_id
        records.append(
            {
                **item,
                "id": image_id,
                "category": category,
                "path": image_path,
                "image": image,
                "sha256": digest,
                "physical_plate_id": item.get("physical_plate_id"),
                "group_id": item.get("group_id"),
            }
        )
    return records, payload


def same_group(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return any(
        first.get(key) is not None and first.get(key) == second.get(key)
        for key in ("physical_plate_id", "group_id")
    )

