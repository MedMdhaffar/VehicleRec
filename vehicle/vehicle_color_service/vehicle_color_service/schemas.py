from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PlateInfo:
    number: str | None = None
    digits_conf: float = 0.0
    type: str | None = None       # "tounes" | "other"
    type_conf: float = 0.0
    locked: bool = False
    ocr_attempts: int = 0


@dataclass(frozen=True)
class ColourObservation:
    label: str
    confidence: float
    quality: float
    weight: float
    valid: bool = True
    diagnostics: dict = field(default_factory=dict, compare=False)


@dataclass
class VehicleProfile:
    track_id: int
    bbox: list[int]               # [x1, y1, x2, y2]
    type: str = "unknown"
    type_conf: float = 0.0
    colour: str = "unknown"
    colour_conf: float = 0.0
    plate: PlateInfo = field(default_factory=PlateInfo)
    first_seen: float = 0.0       # monotonic timestamp
    last_seen: float = 0.0
    frames_seen: int = 0
    status: str = "active"        # "active" | "final"

    # ── Internal voting state — not part of the public schema ─────────────────
    _type_votes: list[str] = field(default_factory=list, repr=False)
    _type_locked: bool = field(default=False, repr=False)
    _colour_samples: list[ColourObservation] = field(default_factory=list, repr=False)
    _colour_attempts: int = field(default=0, repr=False)
    _unknown_colour_samples: int = field(default=0, repr=False)
    _last_colour_attempt: ColourObservation | None = field(default=None, repr=False)
    _colour_finalized: bool = field(default=False, repr=False)
    _colour_decision: str = field(default="pending", repr=False)
    _colour_candidate: str = field(default="unknown", repr=False)
    _colour_winner_share: float = field(default=0.0, repr=False)
    _colour_runner_up: str = field(default="unknown", repr=False)
    _colour_runner_up_share: float = field(default=0.0, repr=False)
    _colour_margin: float = field(default=0.0, repr=False)
    _colour_source: str = field(default="local_hsv", repr=False)
    _colour_agent_error: str | None = field(default=None, repr=False)
    _last_ocr_frame: int = field(default=0, repr=False)
    _last_seen_frame: int = field(default=0, repr=False)
    _plate_early_emitted: bool = field(default=False, repr=False)
    # motion state (plate/OCR state machine)
    _last_centroid: tuple[float, float] | None = field(default=None, repr=False)
    _stationary_frames: int = field(default=0, repr=False)

    # ── Best-crop capture (runtime only, not serialised) ──────────────────────
    _best_score: float = field(default=-1.0, repr=False)
    _best_crop: "np.ndarray | None" = field(default=None, repr=False)
    _best_plate_crop: "np.ndarray | None" = field(default=None, repr=False)
    _best_plate_w: float = field(default=0.0, repr=False)
