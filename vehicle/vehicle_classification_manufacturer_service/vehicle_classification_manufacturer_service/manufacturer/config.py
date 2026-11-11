"""Bounded local inference configuration."""
import json
import math
import os
from dataclasses import dataclass

from pathlib import Path
from .gguf_assets import MODEL_FILENAME, PROJECTOR_FILENAME

MODEL_ID = "LiquidAI/LFM2.5-VL-450M"

MODEL_PARAMETERS = (
    {"key": "temperature", "label": "Temperature", "min": 0, "max": 2, "step": 0.1,
     "description": "0 selects the most likely answer. Higher values add variation."},
    {"key": "top_p", "label": "Top-p", "min": 0.05, "max": 1, "step": 0.05,
     "description": "Limits token choices when temperature is above 0."},
    {"key": "max_new_tokens", "label": "Output token limit", "min": 1, "max": 128, "step": 1,
     "description": "Maximum generated tokens per recognition attempt."},
    {"key": "max_attempts", "label": "Attempts per vehicle", "min": 1, "max": 5, "step": 1,
     "description": "Maximum recognition calls for each track, including retries."},
)


def parse_model_settings(raw):
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("model_settings must be a JSON object") from exc
    if not isinstance(raw, dict):
        raise ValueError("model_settings must be a JSON object")
    fields = {field["key"]: field for field in MODEL_PARAMETERS}
    for key, value in raw.items():
        if key not in fields:
            raise ValueError(f"Unknown model parameter: {key}")
        field = fields[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not field["min"] <= value <= field["max"] or not math.isfinite(value)
                or (field["step"] == 1 and not isinstance(value, int))):
            raise ValueError(f"{key} must be {'an integer' if field['step'] == 1 else 'a number'} between {field['min']} and {field['max']}")
    return dict(raw)


@dataclass(frozen=True)
class RecognitionConfig:
    model_path: str = ""
    projector_path: str = ""
    context_size: int = 4096
    device: str = "cuda:0"
    temperature: float = 0.0
    top_p: float = 1.0
    max_new_tokens: int = 128
    max_attempts: int = 3
    crop_gap: int = 8
    initial_frames: int = 8
    max_crop_side: int = 0

    max_pending_crop_mb: int = 256

    def __post_init__(self):
        parse_model_settings(self.model_settings())
        if isinstance(self.context_size, bool) or not isinstance(self.context_size, int) or self.context_size < 512:
            raise ValueError("context_size must be an integer of at least 512 tokens")
        for name, maximum in (("max_new_tokens", 128), ("max_attempts", 5),
                              ("crop_gap", 300), ("initial_frames", 300),
                              ("max_pending_crop_mb", 1024)):
            if not 1 <= getattr(self, name) <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")
        if self.max_crop_side != 0 and not 48 <= self.max_crop_side <= 8192:
            raise ValueError("max_crop_side must be 0 (original resolution) or between 48 and 8192")

    def model_settings(self):
        return {field["key"]: getattr(self, field["key"]) for field in MODEL_PARAMETERS}

    @classmethod
    def from_env(cls):
        directory = Path(__file__).resolve().parents[1] / "models"
        return cls(model_path=os.getenv("MANUFACTURER_MODEL_PATH", str(directory / MODEL_FILENAME)),
                   projector_path=os.getenv("MANUFACTURER_PROJECTOR_PATH", str(directory / PROJECTOR_FILENAME)),
                   context_size=int(os.getenv("MANUFACTURER_CONTEXT_SIZE", "4096")),
                   device=os.getenv("MANUFACTURER_DEVICE", "cuda:0"),
                   temperature=float(os.getenv("MANUFACTURER_TEMPERATURE", "0")),
                   top_p=float(os.getenv("MANUFACTURER_TOP_P", "1")),
                   **{name: int(os.getenv("MANUFACTURER_" + name.upper(), str(default)))
                      for name, default in (("max_new_tokens", 128), ("max_attempts", 3),
                          ("crop_gap", 8), ("initial_frames", 8), ("max_crop_side", 0), ("max_pending_crop_mb", 256))})

def recognition_enabled():
    value = os.getenv("MANUFACTURER_ENABLED", "1").lower()
    if value not in {"0", "1", "true", "false"}:
        raise ValueError("MANUFACTURER_ENABLED must be 0, 1, true or false")
    return value in {"1", "true"}
