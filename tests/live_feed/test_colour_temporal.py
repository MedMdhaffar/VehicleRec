from __future__ import annotations

import numpy as np
import pytest

import live_feed.config as cfg
from live_feed.colour_temporal import (
    aggregate_colour_observations,
    compute_colour_crop_quality,
    compute_colour_crop_quality_details,
    decide_colour,
    make_colour_observation,
)
from live_feed.perf import PerfMeter
from live_feed.pipeline import CameraPipeline


class _Values:
    def __init__(self, values):
        self.values = values

    def int(self):
        return self

    def tolist(self):
        return self.values


class _Tracked:
    def __init__(self, bbox=(1, 1, 9, 9), cls_id=2):
        self.id = _Values([7])
        self.xyxy = _Values([list(bbox)])
        self.cls = _Values([cls_id])
        self.conf = _Values([0.9])

    def __len__(self):
        return 1


class _NoopWriter:
    def write(self, profile):
        return None


def _pipeline() -> CameraPipeline:
    return CameraPipeline("test", PerfMeter(), None, None, None, review_writer=_NoopWriter())


def _sample(label="black", confidence=1.0, quality=1.0):
    return make_colour_observation(label, confidence, quality)


def test_crop_quality_is_bounded_monotonic_and_saturating():
    tiny = compute_colour_crop_quality(100, 10_000)
    medium = compute_colour_crop_quality(500, 10_000)
    saturated = compute_colour_crop_quality(1_000, 10_000)
    huge = compute_colour_crop_quality(100_000, 10_000)

    assert 0.0 < tiny < medium < saturated
    assert saturated == 1.0
    assert huge == 1.0


def test_unknown_and_invalid_confidence_do_not_compete():
    unknown = _sample("unknown", 0.99, 1.0)
    invalid = _sample("black", float("nan"), 1.0)
    valid = _sample("blue", 0.5, 1.0)
    evidence = aggregate_colour_observations([unknown, invalid, valid])

    assert unknown.valid is False
    assert invalid.valid is False
    assert evidence.weights == {"blue": 0.5}
    assert evidence.winner == "blue"


def test_four_strong_samples_do_not_lock_but_five_do():
    samples = [_sample() for _ in range(4)]
    assert decide_colour(samples).finalized is False
    decision = decide_colour(samples + [_sample()])
    assert decision.finalized is True
    assert decision.reason == "early_lock"
    assert decision.label == "black"


def test_high_quality_high_confidence_samples_outweigh_weak_samples():
    samples = [
        _sample("white", 0.9, 1.0),
        _sample("white", 0.8, 0.9),
        _sample("black", 0.3, 0.2),
        _sample("black", 0.4, 0.2),
        _sample("black", 0.2, 0.3),
    ]
    evidence = aggregate_colour_observations(samples)
    assert evidence.winner == "white"
    assert evidence.weights["white"] > evidence.weights["black"]
    assert decide_colour(samples).label == "white"


def test_seventy_five_percent_support_locks_and_confidence_is_winner_share():
    samples = [_sample("black", 0.75) for _ in range(4)] + [_sample("blue", 1.0)]
    decision = decide_colour(samples)
    assert decision.reason == "early_lock"
    assert decision.label == "black"
    assert decision.confidence == pytest.approx(0.75)


def test_approximately_even_disagreement_does_not_lock():
    samples = [_sample("black") for _ in range(3)] + [_sample("white") for _ in range(3)]
    decision = decide_colour(samples)
    assert decision.finalized is False
    assert decision.evidence.winner_margin == 0.0


def test_pending_at_five_can_lock_at_seven():
    samples = [_sample("black") for _ in range(3)] + [_sample("white") for _ in range(2)]
    assert decide_colour(samples).finalized is False
    samples.append(_sample("black"))
    assert decide_colour(samples).finalized is False
    samples.append(_sample("black"))
    assert decide_colour(samples).reason == "early_lock"


def test_clear_winner_at_max_budget_finalizes():
    samples = [_sample("black") for _ in range(12)] + [_sample("white", 0.8) for _ in range(8)]
    decision = decide_colour(samples)
    assert decision.reason == "max_lock"
    assert decision.label == "black"
    assert decision.confidence > 0.60


def test_near_tie_at_max_budget_finalizes_unknown():
    samples = [_sample("black") for _ in range(10)] + [_sample("white") for _ in range(10)]
    decision = decide_colour(samples)
    assert decision.reason == "max_unknown"
    assert decision.label == "unknown"


def test_pending_track_with_five_samples_uses_final_threshold_when_lost():
    samples = [_sample("black") for _ in range(3)] + [_sample("white") for _ in range(2)]
    assert decide_colour(samples).finalized is False
    decision = decide_colour(samples, finalizing=True)
    assert decision.reason == "final_lock"
    assert decision.label == "black"
    assert decision.confidence == pytest.approx(0.6)


@pytest.mark.parametrize("count", [1, 2])
def test_one_or_two_sample_short_tracks_finalize_unknown(count):
    decision = decide_colour([_sample() for _ in range(count)], finalizing=True)
    assert decision.label == "unknown"
    assert decision.reason == "short_unknown"


@pytest.mark.parametrize("count", [3, 4])
def test_three_or_four_consistent_samples_may_finalize(count):
    decision = decide_colour([_sample() for _ in range(count)], finalizing=True)
    assert decision.label == "black"
    assert decision.reason == "short_lock"


@pytest.mark.parametrize("count", [3, 4])
def test_three_or_four_ambiguous_samples_finalize_unknown(count):
    samples = [_sample("black") if index % 2 == 0 else _sample("white") for index in range(count)]
    decision = decide_colour(samples, finalizing=True)
    assert decision.label == "unknown"


def test_invalid_crop_does_not_advance_samples_or_trigger_lock(monkeypatch):
    calls = []
    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", lambda crop: type(
        "Estimate", (), {"label": "black", "confidence": 1.0,
                          "diagnostics": lambda self: calls.append(1) or {}})())
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    for frame_idx in range(10):
        pipeline.ingest(frame, _Tracked((20, 20, 30, 30)), frame_idx)
    profile = pipeline.active_profiles[7]
    assert calls == []
    assert profile._colour_samples == []
    assert profile._colour_finalized is False

    pipeline.ingest(frame, _Tracked(), 10)
    assert len(calls) == 1
    assert len(profile._colour_samples) == 1
    assert profile._colour_finalized is False


def test_unknown_attempts_do_not_advance_valid_sample_budget(monkeypatch):
    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", lambda crop: type(
        "Estimate", (), {"label": "unknown", "confidence": 0.2,
                          "diagnostics": lambda self: {}})())
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for frame_idx in range(8):
        pipeline.ingest(frame, _Tracked(), frame_idx)

    profile = pipeline.active_profiles[7]
    assert profile._colour_attempts == 8
    assert profile._unknown_colour_samples == 8
    assert profile._colour_samples == []
    assert profile._colour_finalized is False


def test_pipeline_collects_at_most_configured_max_and_locked_colour_cannot_fluctuate(monkeypatch):
    labels = iter(["black", "white"] * 10)
    calls = []

    def estimate(crop):
        calls.append(1)
        return type("Estimate", (), {"label": next(labels), "confidence": 1.0,
                    "diagnostics": lambda self: {}})()

    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", estimate)
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for frame_idx in range(20):
        pipeline.ingest(frame, _Tracked(), frame_idx)

    profile = pipeline.active_profiles[7]
    assert len(calls) == cfg.MAX_VALID_COLOR_SAMPLES
    assert len(profile._colour_samples) == cfg.MAX_VALID_COLOR_SAMPLES
    assert profile._colour_finalized is True
    assert profile.colour == "unknown"


def test_once_early_locked_hsv_is_no_longer_called(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "live_feed.pipeline.estimate_colour_detailed",
        lambda crop: type("Estimate", (), {"label": "black", "confidence": 1.0,
            "diagnostics": lambda self: calls.append(1) or {}})(),
    )
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for frame_idx in range(20):
        pipeline.ingest(frame, _Tracked(), frame_idx)

    profile = pipeline.active_profiles[7]
    assert len(calls) == cfg.MIN_VALID_COLOR_SAMPLES
    assert profile.colour == "black"
    assert profile.colour_conf == 1.0


def test_vehicle_type_semantics_remain_two_frames(monkeypatch):
    monkeypatch.setattr("live_feed.pipeline.estimate_colour_detailed", lambda crop: type(
        "Estimate", (), {"label": "unknown", "confidence": 0.0,
                          "diagnostics": lambda self: {}})())
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    pipeline.ingest(frame, _Tracked(cls_id=2), 0)
    assert pipeline.active_profiles[7]._type_locked is False
    pipeline.ingest(frame, _Tracked(cls_id=2), 1)
    profile = pipeline.active_profiles[7]
    assert cfg.K_CLASSIFY == 2
    assert profile._type_locked is True
    assert len(profile._type_votes) == 2
    pipeline.ingest(frame, _Tracked(cls_id=3), 2)
    assert len(profile._type_votes) == 2


def test_enhanced_quality_components_are_bounded_and_explainable():
    sharp = np.indices((40, 40)).sum(axis=0) % 2 * 255
    sharp = np.repeat(sharp[:, :, None].astype(np.uint8), 3, axis=2)
    blurred = np.full((40, 40, 3), 100, dtype=np.uint8)
    frame_shape = (1000, 1000, 3)
    good = compute_colour_crop_quality_details(sharp, frame_shape, [10, 10, 50, 50], mode="enhanced")
    blur = compute_colour_crop_quality_details(blurred, frame_shape, [10, 10, 50, 50], mode="enhanced")
    clipped = compute_colour_crop_quality_details(sharp, frame_shape, [0, 0, 40, 40], mode="enhanced")
    for details in (good, blur, clipped):
        assert all(0 <= value <= 1 for value in (
            details.size, details.sharpness, details.exposure, details.clipping, details.combined
        ))
    assert good.sharpness > blur.sharpness
    assert clipped.clipping < good.clipping
    assert good.combined <= good.size
    assert blur.combined < good.combined

    larger = compute_colour_crop_quality_details(
        np.full((60, 60, 3), 100, dtype=np.uint8), frame_shape, [10, 10, 70, 70], mode="enhanced"
    )
    assert larger.size > blur.size


def test_pathological_exposure_is_penalized_but_ordinary_black_is_not():
    shape = (100, 100, 3)
    digital_black = np.zeros((30, 30, 3), dtype=np.uint8)
    black_paint = np.full((30, 30, 3), 25, dtype=np.uint8)
    overexposed = np.full((30, 30, 3), 255, dtype=np.uint8)
    under = compute_colour_crop_quality_details(digital_black, shape, [10, 10, 40, 40])
    paint = compute_colour_crop_quality_details(black_paint, shape, [10, 10, 40, 40])
    over = compute_colour_crop_quality_details(overexposed, shape, [10, 10, 40, 40])
    assert under.exposure < paint.exposure == 1.0
    assert over.exposure < paint.exposure


def test_twenty_sample_budget_continues_after_twelve_and_finalizes_at_twenty(monkeypatch):
    monkeypatch.setattr(cfg, "MAX_VALID_COLOR_SAMPLES", 20)
    samples = [_sample("black") if i % 2 == 0 else _sample("white") for i in range(12)]
    assert decide_colour(samples).finalized is False
    samples.extend(_sample("black") if i % 2 == 0 else _sample("white") for i in range(8))
    decision = decide_colour(samples)
    assert decision.finalized is True
    assert decision.reason == "max_unknown"
