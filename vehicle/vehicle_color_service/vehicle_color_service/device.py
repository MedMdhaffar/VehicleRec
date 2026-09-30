from __future__ import annotations

import logging
import os
import re
from typing import Callable

log = logging.getLogger(__name__)

_CUDA_DEVICE = re.compile(r"^cuda(?::(\d+))?$")


def _torch_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except (ImportError, RuntimeError):
        return False


def resolve_device(
    requested: str | None = None,
    *,
    cuda_available: Callable[[], bool] | None = None,
) -> str:
    """Return ``cpu`` or a normalized ``cuda:N`` device.

    ``auto`` selects CUDA 0 when available. Explicit CUDA requests safely fall
    back to CPU when CUDA is unavailable.
    """
    value = (requested or os.environ.get("INFERENCE_DEVICE", "auto")).strip().lower()
    available = (cuda_available or _torch_cuda_available)()

    if value == "auto":
        return "cuda:0" if available else "cpu"
    if value == "cpu":
        return "cpu"
    match = _CUDA_DEVICE.fullmatch(value)
    if match:
        if not available:
            log.warning("CUDA device %s requested but CUDA is unavailable; using CPU", value)
            return "cpu"
        return f"cuda:{match.group(1) or '0'}"
    raise ValueError("device must be one of: auto, cpu, cuda, cuda:N")
