"""Package-relative model/projector validation before native initialization."""
from pathlib import Path


MODEL_FILENAME = "LFM2.5-VL-450M-BF16.gguf"
PROJECTOR_FILENAME = "mmproj-LFM2.5-VL-450m-BF16.gguf"


def resolve_gguf_assets(model_path=None, projector_path=None):
    """Resolve package defaults or explicit overrides and check both GGUF headers.

    This does not initialize native code or establish model/projector compatibility.
    The binding must perform full loading and multimodal compatibility checks.
    """
    directory = Path(__file__).resolve().parents[1] / "models"
    model = Path(model_path).expanduser().resolve() if model_path else directory / MODEL_FILENAME
    projector = Path(projector_path).expanduser().resolve() if projector_path else directory / PROJECTOR_FILENAME
    for role, path in (("model", model), ("vision projector", projector)):
        if not path.is_file():
            raise RuntimeError(f"Local manufacturer {role} GGUF asset missing: {path}")
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:4] != b"GGUF":
            raise RuntimeError(f"Local manufacturer {role} is not a complete GGUF header: {path}")
        version = int.from_bytes(header[4:8], "little")
        if version not in (2, 3):
            raise RuntimeError(f"Unsupported manufacturer {role} GGUF version {version}: {path}")
    return model, projector
