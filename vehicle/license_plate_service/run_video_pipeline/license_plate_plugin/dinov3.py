"""Official DINOv3 ViT-S/16 local-checkpoint loading and CLS extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from .errors import PluginError
from .utils import sha256_file


MODEL_IDENTIFIER = "dinov3_vits16"
CHECKPOINT_FILENAME = "dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
CHECKPOINT_SHA256 = "08c60483bc63c04f533611e34bf70b120eedb7240f469bc16e9e20bf344b941d"
PATCH_SIZE = (16, 16)
EMBEDDING_DIMENSION = 384


def repository_revision(repo: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class DinoV3Encoder:
    def __init__(self, repo: Path, checkpoint: Path, device: str = "cuda:0"):
        if torch.device(device).type != "cuda":
            raise PluginError("DINOv3 requires a CUDA device; CPU execution is disabled.")
        if not torch.cuda.is_available():
            raise PluginError(f"CUDA device requested but unavailable: {device}")
        repo, checkpoint = repo.resolve(), checkpoint.resolve()
        if not (repo / "hubconf.py").is_file():
            raise PluginError(f"Official DINOv3 repository lacks hubconf.py: {repo}")
        if not checkpoint.is_file():
            raise PluginError(f"DINOv3 checkpoint is missing: {checkpoint}")
        if checkpoint.name != CHECKPOINT_FILENAME:
            raise PluginError(f"Expected checkpoint filename {CHECKPOINT_FILENAME}, got {checkpoint.name}.")
        digest = sha256_file(checkpoint)
        if digest != CHECKPOINT_SHA256:
            raise PluginError(f"DINOv3 checkpoint hash mismatch: {digest}")
        self.device = torch.device(device)
        try:
            self.model = torch.hub.load(
                str(repo), MODEL_IDENTIFIER, source="local", weights=str(checkpoint)
            ).to(self.device).eval()
        except Exception as exc:
            raise PluginError(f"Could not load official DINOv3: {type(exc).__name__}: {exc}") from exc
        patch = getattr(getattr(self.model, "patch_embed", None), "patch_size", None)
        patch_value = int(patch[0] if isinstance(patch, (tuple, list)) else patch)
        hidden = int(getattr(self.model, "embed_dim", 0))
        storage = int(getattr(self.model, "n_storage_tokens", -1))
        if patch_value != 16 or hidden != EMBEDDING_DIMENSION or storage != 4:
            raise PluginError(
                f"Incompatible DINOv3 architecture: patch={patch_value}, hidden={hidden}, storage={storage}."
            )
        self.repo = repo
        self.checkpoint = checkpoint
        self.checkpoint_sha256 = digest
        self.repo_revision = repository_revision(repo)

    def synchronize(self) -> None:
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)

    def cls_embedding(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, dict[str, Any]]:
        tensor = pixel_values.to(self.device)
        with torch.no_grad():
            features = self.model.forward_features(tensor)
        if not isinstance(features, dict):
            raise PluginError("Official DINOv3 forward_features did not return a dictionary.")
        if "x_norm_clstoken" not in features or "x_norm_patchtokens" not in features:
            raise PluginError("DINOv3 output lacks explicit CLS or spatial patch tokens.")
        cls = features["x_norm_clstoken"]
        patches = features["x_norm_patchtokens"]
        expected_patches = (tensor.shape[-2] // 16) * (tensor.shape[-1] // 16)
        if list(cls.shape) != [1, EMBEDDING_DIMENSION]:
            raise PluginError(f"Unexpected CLS shape: {list(cls.shape)}")
        if list(patches.shape) != [1, expected_patches, EMBEDDING_DIMENSION]:
            raise PluginError(f"Unexpected spatial-token shape: {list(patches.shape)}")
        vector = F.normalize(cls.float(), p=2, dim=1)[0].cpu()
        if not torch.isfinite(vector).all():
            raise PluginError("DINOv3 returned a non-finite CLS embedding.")
        storage = features.get("x_storage_tokens")
        return vector, {
            "dimension": int(vector.numel()),
            "l2_norm": float(torch.linalg.vector_norm(vector)),
            "spatial_patch_tokens": expected_patches,
            "storage_tokens_excluded": 0 if storage is None else int(storage.shape[1]),
        }

