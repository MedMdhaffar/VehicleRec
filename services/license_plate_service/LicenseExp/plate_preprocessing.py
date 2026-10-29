"""Reproducible DINOv2 preprocessing for complete rectangular plate crops."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal, Sequence

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


PreprocessingMode = Literal["original", "native", "budget"]


class PreprocessingError(RuntimeError):
    """A user-actionable preprocessing or model-compatibility failure."""


@dataclass(frozen=True)
class ResolutionPolicy:
    """Experimental variable-resolution policy; values are not known optima."""

    target_area: int = 50_176
    max_long_edge: int = 1_008
    max_patch_count: int = 512
    padding_rgb: tuple[int, int, int] = (124, 116, 104)
    interpolation: str = "bicubic"


@dataclass
class PreparedInput:
    mode: PreprocessingMode
    resized_bgr: np.ndarray
    padded_bgr: np.ndarray
    pixel_values: torch.Tensor
    display_bgr: np.ndarray
    metadata: dict[str, Any]


INTERPOLATIONS = {
    "nearest": cv2.INTER_NEAREST,
    "bilinear": cv2.INTER_LINEAR,
    "bicubic": cv2.INTER_CUBIC,
    "area": cv2.INTER_AREA,
    "lanczos": cv2.INTER_LANCZOS4,
}


def round_half_up(value: float) -> int:
    """Round a non-negative dimension to nearest integer, with .5 rounded up."""
    if not math.isfinite(value) or value < 0:
        raise PreprocessingError(f"Cannot round invalid image dimension: {value}")
    return int(math.floor(value + 0.5))


def validate_policy(policy: ResolutionPolicy) -> None:
    if policy.target_area <= 0:
        raise PreprocessingError("target_area must be a positive integer.")
    if policy.max_long_edge <= 0:
        raise PreprocessingError("max_long_edge must be a positive integer.")
    if policy.max_patch_count <= 0:
        raise PreprocessingError("max_patch_count must be a positive integer.")
    if policy.interpolation not in INTERPOLATIONS:
        raise PreprocessingError(
            f"Unknown interpolation '{policy.interpolation}'; choose from "
            f"{', '.join(sorted(INTERPOLATIONS))}."
        )
    if len(policy.padding_rgb) != 3 or any(
        not isinstance(value, int) or not 0 <= value <= 255
        for value in policy.padding_rgb
    ):
        raise PreprocessingError("padding_rgb must contain three integers from 0 to 255.")


def derive_patch_size(model: Any) -> tuple[int, int]:
    configured = getattr(getattr(model, "config", None), "patch_size", None)
    if isinstance(configured, int):
        patch_height = patch_width = configured
    elif isinstance(configured, Sequence) and len(configured) == 2:
        patch_height, patch_width = (int(configured[0]), int(configured[1]))
    else:
        raise PreprocessingError(
            f"Model configuration has unsupported patch_size={configured!r}."
        )
    if patch_height <= 0 or patch_width <= 0:
        raise PreprocessingError("Model patch dimensions must both be positive.")
    return patch_height, patch_width


def verify_rectangular_model_support(
    model: Any, device: torch.device, patch_size: tuple[int, int]
) -> dict[str, Any]:
    embeddings = getattr(model, "embeddings", None)
    interpolation = getattr(embeddings, "interpolate_pos_encoding", None)
    if not callable(interpolation):
        raise PreprocessingError(
            "Installed model has no callable positional-embedding interpolation; "
            "rectangular inputs cannot be used safely."
        )
    patch_height, patch_width = patch_size
    probe_height, probe_width = patch_height * 2, patch_width * 3
    probe = torch.zeros(
        (1, int(model.config.num_channels), probe_height, probe_width),
        dtype=torch.float32,
        device=device,
    )
    try:
        with torch.no_grad():
            output = model(pixel_values=probe).last_hidden_state
    except Exception as exc:
        raise PreprocessingError(
            "Installed DINOv2 implementation failed a rectangular positional-"
            f"interpolation probe ({probe_width}x{probe_height}): {exc}"
        ) from exc
    expected_tokens = 1 + (probe_height // patch_height) * (probe_width // patch_width)
    expected_hidden = int(model.config.hidden_size)
    if list(output.shape) != [1, expected_tokens, expected_hidden]:
        raise PreprocessingError(
            "Rectangular interpolation probe returned unexpected shape "
            f"{list(output.shape)}; expected [1, {expected_tokens}, {expected_hidden}]."
        )
    return {
        "method": f"{type(embeddings).__module__}.{type(embeddings).__name__}.interpolate_pos_encoding",
        "implementation": (
            "Built-in learned patch positional embeddings are reshaped to their square "
            "training grid and interpolated to (height // patch_height, width // patch_width)."
        ),
        "interpolation_mode": "bicubic",
        "align_corners": False,
        "probe_tensor_shape": list(probe.shape),
        "probe_output_shape": list(output.shape),
        "successful": True,
    }


def aligned_dimension(dimension: int, patch_dimension: int) -> int:
    if dimension <= 0 or patch_dimension <= 0:
        raise PreprocessingError("Image and patch dimensions must be positive.")
    return ((dimension + patch_dimension - 1) // patch_dimension) * patch_dimension


def dimensions_for_scale(height: int, width: int, scale: float) -> tuple[int, int]:
    return (
        max(1, round_half_up(height * scale)),
        max(1, round_half_up(width * scale)),
    )


def aligned_patch_count(
    height: int, width: int, patch_size: tuple[int, int]
) -> int:
    patch_height, patch_width = patch_size
    return (
        aligned_dimension(height, patch_height) // patch_height
        * (aligned_dimension(width, patch_width) // patch_width)
    )


def largest_scale_within_patch_budget(
    height: int,
    width: int,
    upper_scale: float,
    patch_size: tuple[int, int],
    max_patch_count: int,
) -> float:
    """Find the largest scale no greater than upper_scale within a discrete budget."""
    upper_dimensions = dimensions_for_scale(height, width, upper_scale)
    if aligned_patch_count(*upper_dimensions, patch_size) <= max_patch_count:
        return upper_scale
    low = 0.0
    high = upper_scale
    for _ in range(80):
        midpoint = (low + high) / 2.0
        dimensions = dimensions_for_scale(height, width, midpoint)
        if aligned_patch_count(*dimensions, patch_size) <= max_patch_count:
            low = midpoint
        else:
            high = midpoint
    if low <= 0:
        raise PreprocessingError(
            "No positive resize scale satisfies the configured patch-count limit."
        )
    return low


def calculate_geometry(
    image_shape: Sequence[int],
    mode: Literal["native", "budget"],
    patch_size: tuple[int, int],
    policy: ResolutionPolicy,
) -> dict[str, Any]:
    validate_policy(policy)
    height, width = int(image_shape[0]), int(image_shape[1])
    if height <= 0 or width <= 0:
        raise PreprocessingError(f"Invalid input image dimensions: {width}x{height}.")
    patch_height, patch_width = patch_size

    requested_scale = 1.0
    max_edge_limited_scale = 1.0
    patch_limited = False
    if mode == "budget":
        requested_scale = math.sqrt(policy.target_area / float(height * width))
        max_edge_limited_scale = min(
            requested_scale, policy.max_long_edge / float(max(height, width))
        )
        scale = largest_scale_within_patch_budget(
            height,
            width,
            max_edge_limited_scale,
            patch_size,
            policy.max_patch_count,
        )
        patch_limited = scale < max_edge_limited_scale - 1e-12
        resized_height, resized_width = dimensions_for_scale(height, width, scale)
    elif mode == "native":
        scale = 1.0
        resized_height, resized_width = height, width
    else:
        raise PreprocessingError(f"Unsupported variable-size mode: {mode}")

    final_height = aligned_dimension(resized_height, patch_height)
    final_width = aligned_dimension(resized_width, patch_width)
    pad_height = final_height - resized_height
    pad_width = final_width - resized_width
    top = pad_height // 2
    bottom = pad_height - top
    left = pad_width // 2
    right = pad_width - left
    patch_grid = [final_height // patch_height, final_width // patch_width]
    patch_count = patch_grid[0] * patch_grid[1]

    if mode == "budget":
        if max(resized_height, resized_width) > policy.max_long_edge:
            raise PreprocessingError("Internal error: resized image exceeds max_long_edge.")
        if patch_count > policy.max_patch_count:
            raise PreprocessingError("Internal error: padded image exceeds max_patch_count.")

    return {
        "mode": mode,
        "original_dimensions_hw": [height, width],
        "requested_scale": requested_scale,
        "scale_after_long_edge_limit": max_edge_limited_scale,
        "scale_used_before_integer_rounding": scale,
        "effective_scale_xy": [resized_width / width, resized_height / height],
        "resized_dimensions_hw": [resized_height, resized_width],
        "rounding_rule": "round-half-up; each resized dimension is at least 1 pixel",
        "padding_ltrb": [left, top, right, bottom],
        "odd_padding_rule": "extra pixel goes to right or bottom",
        "final_dimensions_hw": [final_height, final_width],
        "patch_size_hw": [patch_height, patch_width],
        "patch_grid_hw": patch_grid,
        "patch_count": patch_count,
        "long_edge_limit_applied": max_edge_limited_scale < requested_scale - 1e-12,
        "patch_count_limit_applied": patch_limited,
        "complete_resized_image_region_preserved": True,
        "pixels_cropped": 0,
    }


def resize_and_pad(
    crop_bgr: np.ndarray,
    geometry: dict[str, Any],
    policy: ResolutionPolicy,
) -> tuple[np.ndarray, np.ndarray]:
    resized_height, resized_width = geometry["resized_dimensions_hw"]
    source_height, source_width = crop_bgr.shape[:2]
    if (resized_height, resized_width) == (source_height, source_width):
        resized = crop_bgr.copy()
    else:
        resized = cv2.resize(
            crop_bgr,
            (resized_width, resized_height),
            interpolation=INTERPOLATIONS[policy.interpolation],
        )
    left, top, right, bottom = geometry["padding_ltrb"]
    red, green, blue = policy.padding_rgb
    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(blue, green, red),
    )
    expected_height, expected_width = geometry["final_dimensions_hw"]
    if padded.shape[:2] != (expected_height, expected_width):
        raise PreprocessingError("Internal error: padded output has unexpected dimensions.")
    return resized, padded


def reconstruct_tensor_display(
    pixel_values: torch.Tensor, processor: Any
) -> tuple[np.ndarray, dict[str, Any]]:
    if pixel_values.ndim != 4 or list(pixel_values.shape[:2]) != [1, 3]:
        raise PreprocessingError(
            f"Expected pixel_values shaped [1, 3, H, W], got {list(pixel_values.shape)}."
        )
    display = pixel_values[0].detach().cpu().float().clone()
    if processor.do_normalize:
        mean = torch.tensor(processor.image_mean, dtype=display.dtype)[:, None, None]
        std = torch.tensor(processor.image_std, dtype=display.dtype)[:, None, None]
        display = display * std + mean
    if processor.do_rescale:
        display = display / float(processor.rescale_factor)
    pre_clip_min = float(display.min().item())
    pre_clip_max = float(display.max().item())
    clipped = display.clamp(0, 255)
    clipped_count = int(torch.count_nonzero(display != clipped).item())
    rgb = clipped.round().to(torch.uint8).permute(1, 2, 0).numpy()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), {
        "description": (
            "Reconstructed from the exact inference tensor by reversing normalization "
            "and rescaling; clipped to [0,255], rounded to nearest integer, and displayed as uint8."
        ),
        "pre_clip_range": [pre_clip_min, pre_clip_max],
        "clipped_value_count": clipped_count,
        "rounded_for_display": True,
    }


def processor_tensor(
    prepared_bgr: np.ndarray, processor: Any, disable_geometry: bool
) -> torch.Tensor:
    rgb = cv2.cvtColor(prepared_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    kwargs: dict[str, Any] = {"images": pil_image, "return_tensors": "pt"}
    if disable_geometry:
        kwargs.update({"do_resize": False, "do_center_crop": False})
    inputs = processor(**kwargs)
    if set(inputs.keys()) != {"pixel_values"}:
        raise PreprocessingError(
            f"Unexpected processor outputs: {sorted(inputs.keys())}; expected only pixel_values."
        )
    return inputs["pixel_values"]


def prepare_variable_input(
    crop_bgr: np.ndarray,
    processor: Any,
    mode: Literal["native", "budget"],
    patch_size: tuple[int, int],
    policy: ResolutionPolicy,
) -> PreparedInput:
    geometry = calculate_geometry(crop_bgr.shape, mode, patch_size, policy)
    resized, padded = resize_and_pad(crop_bgr, geometry, policy)
    pixel_values = processor_tensor(padded, processor, disable_geometry=True)
    expected_height, expected_width = geometry["final_dimensions_hw"]
    if list(pixel_values.shape) != [1, 3, expected_height, expected_width]:
        raise PreprocessingError(
            "Processor changed variable-size geometry despite resize/crop being disabled: "
            f"got {list(pixel_values.shape)}, expected [1, 3, {expected_height}, {expected_width}]."
        )
    patch_height, patch_width = patch_size
    if expected_height % patch_height or expected_width % patch_width:
        raise PreprocessingError("Prepared tensor dimensions are not patch-aligned.")
    display, display_metadata = reconstruct_tensor_display(pixel_values, processor)
    difference = np.abs(display.astype(np.int16) - padded.astype(np.int16))
    geometry.update(
        {
            "padding_rgb": list(policy.padding_rgb),
            "resize_interpolation": policy.interpolation,
            "processor_geometry_disabled": {"do_resize": False, "do_center_crop": False},
            "tensor_shape": list(pixel_values.shape),
            "tensor_dtype": str(pixel_values.dtype),
            "tensor_sha256": tensor_sha256(pixel_values),
            "display_reconstruction": {
                **display_metadata,
                "matches_prepared_uint8_exactly": bool(np.array_equal(display, padded)),
                "maximum_absolute_uint8_difference": int(difference.max()),
            },
        }
    )
    return PreparedInput(mode, resized, padded, pixel_values, display, geometry)


def original_retained_geometry(
    crop_shape: Sequence[int], resized_shape: Sequence[int], crop_shape_after: Sequence[int]
) -> dict[str, Any]:
    source_height, source_width = int(crop_shape[0]), int(crop_shape[1])
    resized_height, resized_width = int(resized_shape[0]), int(resized_shape[1])
    target_height, target_width = int(crop_shape_after[0]), int(crop_shape_after[1])
    top = int((resized_height - target_height) / 2.0)
    left = int((resized_width - target_width) / 2.0)
    bottom, right = top + target_height, left + target_width
    scale_x, scale_y = resized_width / source_width, resized_height / source_height
    retained = [left / scale_x, top / scale_y, right / scale_x, bottom / scale_y]
    clipped = [
        max(0.0, min(source_width, retained[0])),
        max(0.0, min(source_height, retained[1])),
        max(0.0, min(source_width, retained[2])),
        max(0.0, min(source_height, retained[3])),
    ]
    return {
        "resized_dimensions_hw": [resized_height, resized_width],
        "center_crop_window_xyxy_in_resized_pixels": [left, top, right, bottom],
        "mapped_retained_region_xyxy_in_plate_crop": retained,
        "mapped_retained_region_clipped_xyxy_in_plate_crop": clipped,
        "mapping_note": (
            "Resize dimensions and integer crop window are exact. The inverse region "
            "is approximate because bicubic sampling can use neighboring pixels."
        ),
    }


def prepare_original_input(crop_bgr: np.ndarray, processor: Any) -> PreparedInput:
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    pixel_values = processor_tensor(crop_bgr, processor, disable_geometry=False)
    try:
        prepared = processor.process_image(pil_image, do_convert_rgb=processor.do_convert_rgb)
        resized = processor.resize(prepared, size=processor.size, resample=processor.resample)
        center_cropped = processor.center_crop(resized, processor.crop_size)
    except Exception as exc:
        raise PreprocessingError(
            f"Could not replay installed processor geometry for original mode: {exc}"
        ) from exc
    display, display_metadata = reconstruct_tensor_display(pixel_values, processor)
    center_bgr = center_bgr_from_tensor(center_cropped)
    display_difference = np.abs(
        display.astype(np.int16) - center_bgr.astype(np.int16)
    )
    display_metadata.update(
        {
            "matches_pre_normalized_center_crop_exactly": bool(
                np.array_equal(display, center_bgr)
            ),
            "maximum_absolute_uint8_difference": int(display_difference.max()),
        }
    )
    geometry = original_retained_geometry(
        crop_bgr.shape[:2], resized.shape[-2:], center_cropped.shape[-2:]
    )
    geometry.update(
        {
            "mode": "original",
            "processor_defaults_unchanged": True,
            "tensor_shape": list(pixel_values.shape),
            "tensor_dtype": str(pixel_values.dtype),
            "tensor_sha256": tensor_sha256(pixel_values),
            "patch_size_hw": None,
            "patch_grid_hw": None,
            "patch_count": None,
            "display_reconstruction": display_metadata,
        }
    )
    resized_bgr = cv2.cvtColor(
        resized.permute(1, 2, 0).cpu().numpy(), cv2.COLOR_RGB2BGR
    )
    return PreparedInput(
        "original", resized_bgr, center_bgr, pixel_values, display, geometry
    )


def center_bgr_from_tensor(tensor: torch.Tensor) -> np.ndarray:
    """Convert a channels-first RGB uint8 stage tensor to an OpenCV BGR image."""
    return cv2.cvtColor(
        tensor.permute(1, 2, 0).detach().cpu().numpy(), cv2.COLOR_RGB2BGR
    )


def add_original_patch_metadata(
    prepared: PreparedInput, patch_size: tuple[int, int]
) -> None:
    height, width = prepared.pixel_values.shape[-2:]
    patch_height, patch_width = patch_size
    if height % patch_height or width % patch_width:
        raise PreprocessingError("Original processor output is not patch-aligned.")
    prepared.metadata["patch_size_hw"] = [patch_height, patch_width]
    prepared.metadata["patch_grid_hw"] = [
        height // patch_height,
        width // patch_width,
    ]
    prepared.metadata["patch_count"] = (
        height // patch_height * (width // patch_width)
    )


def extract_embedding_from_tensor(
    pixel_values: torch.Tensor, model: Any, device: torch.device
) -> tuple[torch.Tensor, dict[str, Any]]:
    model_tensor = pixel_values.to(device)
    try:
        with torch.no_grad():
            output = model(pixel_values=model_tensor)
            embedding = output.last_hidden_state[:, 0, :]
            embedding = F.normalize(embedding.float(), p=2, dim=1)
    except Exception as exc:
        raise PreprocessingError(
            f"DINOv2 inference failed for tensor {list(pixel_values.shape)}: {exc}"
        ) from exc
    if embedding.shape[0] != 1 or not torch.isfinite(embedding).all():
        raise PreprocessingError("DINOv2 returned an invalid CLS embedding.")
    cpu_embedding = embedding.squeeze(0).cpu()
    return cpu_embedding, {
        "dimension": int(cpu_embedding.numel()),
        "dtype": str(cpu_embedding.dtype),
        "l2_norm": float(torch.linalg.vector_norm(cpu_embedding).item()),
        "logic": "last_hidden_state[:, 0, :], float32, L2-normalized",
    }


def tensor_sha256(tensor: torch.Tensor) -> str:
    data = tensor.detach().cpu().contiguous().numpy().tobytes()
    return hashlib.sha256(data).hexdigest()


def policy_record(policy: ResolutionPolicy) -> dict[str, Any]:
    return {
        **asdict(policy),
        "status": "experimental choices; not established optimal values",
    }
