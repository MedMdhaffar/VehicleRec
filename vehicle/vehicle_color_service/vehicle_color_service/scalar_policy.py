"""Canonical scalar Colour policy; no pixel or provider dependencies."""
import math
from dataclasses import dataclass
try:
    from . import config as cfg
    from .schemas import ColourObservation
except ImportError:
    import config as cfg
    from schemas import ColourObservation

SUPPORTED_COLOURS = frozenset({
    "white", "black", "silver/gray", "red", "orange", "yellow",
    "green", "blue", "purple", "brown/beige",
})


@dataclass(frozen=True)
class ColourEvidence:
    weights: dict[str, float]
    total_weight: float
    winner: str
    winner_share: float
    runner_up: str
    runner_up_share: float
    winner_margin: float


@dataclass(frozen=True)
class ColourDecision:
    finalized: bool
    label: str
    confidence: float
    reason: str
    evidence: ColourEvidence


def make_colour_observation(
    label: str,
    confidence: float,
    quality: float,
    diagnostics: dict | None = None,
) -> ColourObservation:
    valid = (
        label in SUPPORTED_COLOURS
        and math.isfinite(confidence)
        and 0.0 < confidence <= 1.0
        and math.isfinite(quality)
        and 0.0 < quality <= 1.0
    )
    weight = confidence * quality if valid else 0.0
    return ColourObservation(
        label=label,
        confidence=round(float(confidence), 4) if math.isfinite(confidence) else 0.0,
        quality=round(float(quality), 4) if math.isfinite(quality) else 0.0,
        weight=round(float(weight), 6),
        valid=valid,
        diagnostics=diagnostics or {},
    )


def aggregate_colour_observations(samples: list[ColourObservation]) -> ColourEvidence:
    weights: dict[str, float] = {}
    for sample in samples:
        if sample.valid:
            weights[sample.label] = weights.get(sample.label, 0.0) + sample.weight

    total = sum(weights.values())
    if total <= 0:
        return ColourEvidence({}, 0.0, "unknown", 0.0, "unknown", 0.0, 0.0)

    ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    winner, winner_weight = ranked[0]
    runner_up, runner_weight = ranked[1] if len(ranked) > 1 else ("unknown", 0.0)
    winner_share = winner_weight / total
    runner_up_share = runner_weight / total
    return ColourEvidence(
        weights={label: round(weight, 6) for label, weight in weights.items()},
        total_weight=round(total, 6),
        winner=winner,
        winner_share=round(winner_share, 6),
        runner_up=runner_up,
        runner_up_share=round(runner_up_share, 6),
        winner_margin=round(winner_share - runner_up_share, 6),
    )


def decide_colour(
    samples: list[ColourObservation],
    *,
    finalizing: bool = False,
) -> ColourDecision:
    """Evaluate online locking or track-finalization rules for valid samples."""
    evidence = aggregate_colour_observations(samples)
    count = len(samples)

    if finalizing:
        if count <= 2:
            return ColourDecision(True, "unknown", evidence.winner_share, "short_unknown", evidence)
        if count <= 4:
            passed = (
                evidence.winner_share >= cfg.SHORT_TRACK_WINNER_SHARE
                and evidence.winner_margin >= cfg.SHORT_TRACK_WINNER_MARGIN
            )
            reason = "short_lock" if passed else "short_unknown"
            return ColourDecision(True, evidence.winner if passed else "unknown",
                                  evidence.winner_share, reason, evidence)
        passed = (
            evidence.winner_share >= cfg.FINAL_WINNER_SHARE
            and evidence.winner_margin >= cfg.FINAL_WINNER_MARGIN
        )
        reason = "final_lock" if passed else "final_unknown"
        return ColourDecision(True, evidence.winner if passed else "unknown",
                              evidence.winner_share, reason, evidence)

    if count >= cfg.MAX_VALID_COLOR_SAMPLES:
        passed = (
            evidence.winner_share >= cfg.FINAL_WINNER_SHARE
            and evidence.winner_margin >= cfg.FINAL_WINNER_MARGIN
        )
        reason = "max_lock" if passed else "max_unknown"
        return ColourDecision(True, evidence.winner if passed else "unknown",
                              evidence.winner_share, reason, evidence)

    if count >= cfg.MIN_VALID_COLOR_SAMPLES:
        passed = (
            evidence.winner_share >= cfg.EARLY_LOCK_WINNER_SHARE
            and evidence.winner_margin >= cfg.EARLY_LOCK_MARGIN
        )
        if passed:
            return ColourDecision(True, evidence.winner, evidence.winner_share,
                                  "early_lock", evidence)

    return ColourDecision(False, "unknown", evidence.winner_share, "pending", evidence)
