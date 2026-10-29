"""Configuration loading with paths resolved relative to the configuration file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PluginError


@dataclass(frozen=True)
class ResolutionPolicy:
    target_area: int = 50_176
    max_long_edge: int = 1_008
    max_patch_count: int = 512
    padding_rgb: tuple[int, int, int] = (124, 116, 104)
    interpolation: str = "bicubic"


@dataclass(frozen=True)
class PluginConfig:
    source_path: Path
    dinov3_repo: Path
    dinov3_checkpoint: Path
    paddle_model_dir: Path
    reference_manifest: Path
    reference_index: Path
    device: str
    ocr_device: str
    policy: ResolutionPolicy
    model_identifier: str = "dinov3_vits16"
    ocr_identifier: str = "arabic_PP-OCRv5_mobile_rec"
    tie_tolerance: float = 1e-12


def _resolve(base: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PluginError(f"Configuration field {field!r} must be a non-empty path string.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> PluginConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Cannot read configuration {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginError("Plugin configuration must be a JSON object.")
    base = config_path.parent
    policy_data = payload.get("resolution_policy", {})
    try:
        policy = ResolutionPolicy(
            target_area=int(policy_data.get("target_area", 50_176)),
            max_long_edge=int(policy_data.get("max_long_edge", 1_008)),
            max_patch_count=int(policy_data.get("max_patch_count", 512)),
            padding_rgb=tuple(int(value) for value in policy_data.get("padding_rgb", [124, 116, 104])),
            interpolation=str(policy_data.get("interpolation", "bicubic")),
        )
    except (TypeError, ValueError) as exc:
        raise PluginError(f"Invalid resolution_policy: {exc}") from exc
    return PluginConfig(
        source_path=config_path,
        dinov3_repo=_resolve(base, payload.get("dinov3_repo"), "dinov3_repo"),
        dinov3_checkpoint=_resolve(base, payload.get("dinov3_checkpoint"), "dinov3_checkpoint"),
        paddle_model_dir=_resolve(base, payload.get("paddle_model_dir"), "paddle_model_dir"),
        reference_manifest=_resolve(base, payload.get("reference_manifest"), "reference_manifest"),
        reference_index=_resolve(base, payload.get("reference_index"), "reference_index"),
        device=str(payload.get("device", "cpu")),
        ocr_device=str(payload.get("ocr_device", "cpu")),
        policy=policy,
        model_identifier=str(payload.get("model_identifier", "dinov3_vits16")),
        ocr_identifier=str(payload.get("ocr_identifier", "arabic_PP-OCRv5_mobile_rec")),
        tie_tolerance=float(payload.get("tie_tolerance", 1e-12)),
    )



def load_default_config() -> PluginConfig:
    """Load this source checkout's bundled configuration, independent of cwd.

    Install the plugin editable in each runtime environment and retain the
    complete plugin directory, including configuration and assets.
    """
    return load_config(Path(__file__).resolve().parents[1] / "configuration" / "local_reproduction.json")
