"""Adapter for Meta's official DINOv3 torch.hub local-checkpoint interface."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch
from PIL import Image


DINOV3_CHECKPOINTS = {
    "dinov3_vits16": "dinov3_vits16_pretrain_lvd1689m-08c60483.pth",
    "dinov3_vitb16": "dinov3_vitb16_pretrain_lvd1689m-73cec8be.pth",
}
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


class MetaDinoV3Processor:
    """Minimal geometry-free processor using the official inference normalization."""

    do_convert_rgb = True
    do_resize = False
    size = None
    do_center_crop = False
    crop_size = None
    do_rescale = True
    rescale_factor = 1.0 / 255.0
    do_normalize = True
    image_mean = IMAGENET_MEAN
    image_std = IMAGENET_STD

    def __call__(self, images: Image.Image, return_tensors: str, **_: Any) -> dict[str, torch.Tensor]:
        if return_tensors != "pt":
            raise ValueError("MetaDinoV3Processor supports return_tensors='pt' only.")
        rgb = np.asarray(images.convert("RGB"), dtype=np.float32).copy()
        tensor = torch.from_numpy(rgb).permute(2, 0, 1) * self.rescale_factor
        mean = torch.tensor(self.image_mean, dtype=tensor.dtype)[:, None, None]
        std = torch.tensor(self.image_std, dtype=tensor.dtype)[:, None, None]
        return {"pixel_values": ((tensor - mean) / std).unsqueeze(0)}


class _RectangularSupport:
    @staticmethod
    def interpolate_pos_encoding(*_: Any, **__: Any) -> None:
        """Compatibility marker; the real interpolation stays inside official DINOv3."""


class OfficialDinoV3Wrapper(torch.nn.Module):
    """Expose explicit official CLS/spatial outputs in the existing evaluator shape."""

    def __init__(self, model: torch.nn.Module, patch_size: int, hidden_size: int):
        super().__init__()
        self.model = model
        self.config = SimpleNamespace(
            patch_size=patch_size, hidden_size=hidden_size, num_channels=3,
            _commit_hash=None,
        )
        self.embeddings = _RectangularSupport()
        self.last_register_count: int | None = None

    def forward(self, pixel_values: torch.Tensor) -> Any:
        features = self.model.forward_features(pixel_values)
        required = {"x_norm_clstoken", "x_norm_patchtokens"}
        if not isinstance(features, dict) or not required.issubset(features):
            raise RuntimeError(
                "Official DINOv3 forward_features did not return explicit "
                "x_norm_clstoken and x_norm_patchtokens outputs."
            )
        cls = features["x_norm_clstoken"]
        patches = features["x_norm_patchtokens"]
        if cls.ndim == 2:
            cls = cls[:, None, :]
        registers = features.get("x_norm_regtokens")
        self.last_register_count = 0 if registers is None else int(registers.shape[1])
        # Registers/storage tokens are deliberately not included.
        return SimpleNamespace(last_hidden_state=torch.cat([cls, patches], dim=1))


def load_official_dinov3(
    repo: Path, checkpoint: Path, entrypoint: str, device: torch.device
) -> tuple[MetaDinoV3Processor, OfficialDinoV3Wrapper]:
    if entrypoint not in DINOV3_CHECKPOINTS:
        raise ValueError(f"Unsupported official entrypoint: {entrypoint}")
    if checkpoint.name != DINOV3_CHECKPOINTS[entrypoint]:
        raise ValueError(
            f"{entrypoint} requires exact checkpoint filename "
            f"{DINOV3_CHECKPOINTS[entrypoint]!r}; got {checkpoint.name!r}."
        )
    if not (repo / "hubconf.py").is_file():
        raise FileNotFoundError(f"Official DINOv3 repository lacks hubconf.py: {repo}")
    if not checkpoint.is_file():
        raise FileNotFoundError(f"DINOv3 checkpoint is missing: {checkpoint}")
    base = torch.hub.load(
        str(repo.resolve()), entrypoint, source="local", weights=str(checkpoint.resolve())
    )
    patch = getattr(getattr(base, "patch_embed", None), "patch_size", None)
    patch_size = int(patch[0] if isinstance(patch, (tuple, list)) else patch)
    if patch_size != 16:
        raise RuntimeError(f"Expected official DINOv3 patch size 16, got {patch!r}.")
    hidden = int(getattr(base, "embed_dim"))
    wrapper = OfficialDinoV3Wrapper(base, patch_size, hidden).to(device).eval()
    return MetaDinoV3Processor(), wrapper
