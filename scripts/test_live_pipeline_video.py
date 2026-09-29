#!/usr/bin/env python3
"""Run the production live tracking + HSV colour pipeline on a local MP4."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import os
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_feed.config as cfg
from common.annotate import draw_overlay
from common.device import resolve_device
from live_feed.perf import PerfMeter
from live_feed.pipeline import CameraPipeline
from live_feed.colour_temporal import aggregate_colour_observations
from live_feed.vehicle_runtime import (
    IMGSZ,
    TrackBoxes,
    create_vehicle_tracker,
    load_vehicle_model,
)


class RunnerError(RuntimeError):
    """Expected command-line/runtime failure with a user-facing message."""


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    width: int
    height: int
    frame_count: int


class DiagnosticPerfMeter(PerfMeter):
    """Keep full-run timing totals while preserving PerfMeter behavior."""

    def __init__(self) -> None:
        super().__init__()
        self.stage_total_ms: dict[str, float] = {}
        self.stage_calls: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        with super().stage(name):
            yield
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stage_total_ms[name] = self.stage_total_ms.get(name, 0.0) + elapsed_ms
        self.stage_calls[name] = self.stage_calls.get(name, 0) + 1

    def average_ms(self, name: str) -> float:
        calls = self.stage_calls.get(name, 0)
        return self.stage_total_ms.get(name, 0.0) / calls if calls else 0.0


class JsonlDiagnostics:
    def __init__(self, path: Path, echo: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("w", encoding="utf-8")
        self._echo = echo
        self._lock = threading.Lock()
        self.final_records: list[dict] = []

    def write(self, event: str, **fields) -> None:
        record = {"event": event, **fields}
        with self._lock:
            self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._handle.flush()
            if event == "final":
                self.final_records.append(record)
        if self._echo and event in {"sample", "lock", "final"}:
            print(json.dumps(record, ensure_ascii=False))

    def close(self) -> None:
        self._handle.close()


class _NoopReviewWriter:
    def write(self, _profile):
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path, help="input MP4 path")
    parser.add_argument("--output", required=True, type=Path, help="annotated output MP4 path")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N")
    parser.add_argument("--max-frames", type=int, default=None, help="optional representative-run limit")
    parser.add_argument("--display", action="store_true", help="show annotated frames while processing")
    parser.add_argument("--log-tracks", action="store_true", help="also print sample/lock/final events")
    parser.add_argument(
        "--baseline-tracks",
        type=Path,
        default=None,
        help="optional baseline JSONL used to write a lifecycle-aligned comparison CSV",
    )
    parser.add_argument(
        "--colour-features-output", type=Path, default=None,
        help="optional CSV export with one flattened feature row per valid colour observation",
    )
    parser.add_argument("--roi-mode", choices=("legacy", "multi"), default=None)
    parser.add_argument("--neutral-mode", choices=("legacy", "adaptive"), default=None)
    parser.add_argument("--normalization", choices=("none", "gray_world", "gamma"), default=None)
    parser.add_argument("--max-colour-samples", type=int, default=None)
    parser.add_argument("--quality-mode", choices=("area", "enhanced"), default=None)
    parser.add_argument(
        "--colour-feature-mode", choices=("hsv", "hsv_gray", "hsv_lab"), default=None,
        help="auxiliary local-colour feature fusion mode (default: production hsv)",
    )
    return parser.parse_args(argv)


def resolve_requested_device(requested: str) -> str:
    value = requested.strip().lower()
    if value.startswith("cuda"):
        if not re.fullmatch(r"cuda(?::\d+)?", value):
            raise RunnerError("device must be one of: auto, cpu, cuda, cuda:N")
        import torch

        if not torch.cuda.is_available():
            raise RunnerError(f"CUDA was requested ({requested}) but is unavailable")
        index = int(value.split(":", 1)[1]) if ":" in value else 0
        if index >= torch.cuda.device_count():
            raise RunnerError(
                f"CUDA device {index} does not exist; available device count is {torch.cuda.device_count()}"
            )
    try:
        return resolve_device(value)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc


def open_video(path: Path) -> tuple[cv2.VideoCapture, VideoInfo]:
    if not path.is_file():
        raise RunnerError(f"video file does not exist: {path}")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise RunnerError(f"VideoCapture cannot open: {path}")

    info = VideoInfo(
        fps=float(capture.get(cv2.CAP_PROP_FPS)),
        width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        frame_count=int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    )
    if info.fps <= 0 or info.width <= 0 or info.height <= 0:
        capture.release()
        raise RunnerError(f"video has invalid metadata: fps={info.fps}, size={info.width}x{info.height}")
    return capture, info


def open_writer(path: Path, info: VideoInfo) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), info.fps, (info.width, info.height)
    )
    if not writer.isOpened():
        writer.release()
        raise RunnerError(f"output video writer cannot open: {path}")
    return writer


def _diagnostics_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_tracks.jsonl")


def _comparison_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_comparison.csv")


def _prepare_vehicle_model(device: str, info: VideoInfo):
    """Probe the production engine and visibly fall back to its .pt weights."""
    model, model_path = load_vehicle_model(device=device)
    probe = np.zeros((info.height, info.width, 3), dtype=np.uint8)
    try:
        model.predict(
            [probe], conf=cfg.TRACK_CONF, imgsz=IMGSZ, verbose=False, device=device,
        )
        return model, model_path
    except Exception as engine_exc:
        if model_path.suffix != ".engine":
            raise RunnerError(f"vehicle model initialization failed: {engine_exc}") from engine_exc
        print(
            f"warning: TensorRT engine is unusable on this VM ({engine_exc}); "
            "falling back to the production .pt weights",
            file=sys.stderr,
        )

    model, model_path = load_vehicle_model(device=device, prefer_engine=False)
    try:
        model.predict(
            [probe], conf=cfg.TRACK_CONF, imgsz=IMGSZ, verbose=False, device=device,
        )
    except Exception as pt_exc:
        raise RunnerError(f"PyTorch vehicle model initialization failed: {pt_exc}") from pt_exc
    return model, model_path


def _record_colour_transitions(
    diagnostics: JsonlDiagnostics,
    profiles: dict,
    previous: dict[int, tuple[int, int, bool]],
    frame_idx: int,
) -> None:
    for track_id, profile in profiles.items():
        old_attempts, _old_sample_count, was_finalized = previous.get(track_id, (0, 0, False))
        if profile._colour_attempts > old_attempts and profile._last_colour_attempt is not None:
            sample = profile._last_colour_attempt
            evidence = aggregate_colour_observations(profile._colour_samples)
            diagnostics.write(
                "sample",
                frame_index=frame_idx,
                track_id=track_id,
                attempt_index=profile._colour_attempts,
                sample_index=len(profile._colour_samples) if sample.valid else None,
                valid=sample.valid,
                label=sample.label,
                hsv_confidence=round(sample.confidence, 4),
                crop_quality=round(sample.quality, 4),
                observation_weight=round(sample.weight, 6),
                accumulated_weights=evidence.weights,
                winner=evidence.winner,
                winner_share=round(evidence.winner_share, 6),
                runner_up=evidence.runner_up,
                runner_up_share=round(evidence.runner_up_share, 6),
                winner_margin=round(evidence.winner_margin, 6),
                decision=profile._colour_decision,
                bbox=list(profile.bbox),
                colour_diagnostics=sample.diagnostics,
            )
        if profile._colour_finalized and not was_finalized:
            diagnostics.write(
                "lock",
                frame_index=frame_idx,
                track_id=track_id,
                frames_seen=profile.frames_seen,
                sample_count=len(profile._colour_samples),
                colour=profile.colour,
                colour_confidence=round(profile.colour_conf, 4),
                decision=profile._colour_decision,
            )


def _record_final(diagnostics: JsonlDiagnostics, profile, frame_idx: int, reason: str) -> None:
    diagnostics.write(
        "final",
        frame_index=frame_idx,
        reason=reason,
        track_id=profile.track_id,
        frames_seen=profile.frames_seen,
        colour_attempts=profile._colour_attempts,
        unknown_samples=profile._unknown_colour_samples,
        sample_count=len(profile._colour_samples),
        samples=[
            {
                "label": sample.label,
                "confidence": round(sample.confidence, 4),
                "quality": round(sample.quality, 4),
                "weight": round(sample.weight, 6),
                "diagnostics": sample.diagnostics,
            }
            for sample in profile._colour_samples
        ],
        locked=profile._colour_finalized and profile.colour != "unknown",
        decision=profile._colour_decision,
        colour=profile.colour,
        colour_confidence=round(profile.colour_conf, 4),
    )


def _process_frame(model, device: str, tracker, pipeline: CameraPipeline, frame, frame_idx: int,
                   diagnostics: JsonlDiagnostics) -> tuple[list, float, float]:
    detector_started = time.perf_counter()
    results = model.predict(
        [frame], conf=cfg.TRACK_CONF, imgsz=IMGSZ, verbose=False, device=device,
    )
    detector_ms = (time.perf_counter() - detector_started) * 1000
    if not results:
        raise RunnerError(f"vehicle detector returned no result object for frame {frame_idx}")

    tracker_started = time.perf_counter()
    rows = tracker.update(results[0].boxes.cpu().numpy(), frame)
    tracked = TrackBoxes(rows)
    tracker_ms = (time.perf_counter() - tracker_started) * 1000

    previous = {
        track_id: (
            profile._colour_attempts,
            len(profile._colour_samples),
            profile._colour_finalized,
        )
        for track_id, profile in pipeline.active_profiles.items()
    }
    pipeline.ingest(frame, tracked, frame_idx)
    _record_colour_transitions(diagnostics, pipeline.active_profiles, previous, frame_idx)
    emitted, _ = pipeline.apply_plate_results({}, frame_idx)
    for profile in emitted:
        if profile.status == "final":
            _record_final(diagnostics, profile, frame_idx, "lost")
    return emitted, detector_ms, tracker_ms


def _drain_tracks(pipeline: CameraPipeline, frame: np.ndarray, first_frame_idx: int,
                  diagnostics: JsonlDiagnostics) -> int:
    """Advance production LOST_FRAMES logic with empty tracker results at EOF."""
    drain_frames = 0
    for offset in range(cfg.LOST_FRAMES + 1):
        frame_idx = first_frame_idx + offset
        pipeline.ingest(frame, TrackBoxes.empty(), frame_idx)
        emitted, _ = pipeline.apply_plate_results({}, frame_idx)
        for profile in emitted:
            if profile.status == "final":
                _record_final(diagnostics, profile, frame_idx, "eof_drain")
        drain_frames += 1
    if pipeline.active_profiles:
        remaining = sorted(pipeline.active_profiles)
        raise RunnerError(f"EOF drain did not finalize tracks: {remaining}")
    return drain_frames


def write_baseline_comparison(baseline_path: Path, temporal_path: Path, output_path: Path) -> dict:
    if not baseline_path.is_file():
        raise RunnerError(f"baseline diagnostics do not exist: {baseline_path}")

    def finals_by_track(path: Path) -> dict[int, list[dict]]:
        grouped: dict[int, list[dict]] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record.get("event") == "final":
                    grouped.setdefault(int(record["track_id"]), []).append(record)
        return grouped

    baseline = finals_by_track(baseline_path)
    temporal = finals_by_track(temporal_path)
    rows = []
    for track_id in sorted(set(baseline) | set(temporal)):
        baseline_lifecycles = baseline.get(track_id, [])
        temporal_lifecycles = temporal.get(track_id, [])
        count = max(len(baseline_lifecycles), len(temporal_lifecycles))
        for occurrence in range(count):
            old = baseline_lifecycles[occurrence] if occurrence < len(baseline_lifecycles) else None
            new = temporal_lifecycles[occurrence] if occurrence < len(temporal_lifecycles) else None
            aligned = bool(old and new and old.get("frame_index") == new.get("frame_index"))
            rows.append({
                "track_id": track_id,
                "lifecycle": occurrence + 1,
                "aligned": aligned,
                "baseline_final_frame": old.get("frame_index") if old else "",
                "baseline_final_colour": old.get("colour") if old else "",
                "baseline_confidence": old.get("colour_confidence") if old else "",
                "baseline_sample_count": old.get("sample_count") if old else "",
                "temporal_final_frame": new.get("frame_index") if new else "",
                "temporal_final_colour": new.get("colour") if new else "",
                "temporal_confidence": new.get("colour_confidence") if new else "",
                "temporal_sample_count": new.get("sample_count") if new else "",
                "changed": (old.get("colour") != new.get("colour")) if aligned else "",
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["track_id"])
        writer.writeheader()
        writer.writerows(rows)

    aligned_rows = [row for row in rows if row["aligned"]]
    changed_rows = [row for row in aligned_rows if row["changed"]]
    return {
        "comparison": str(output_path),
        "comparison_rows": len(rows),
        "aligned_rows": len(aligned_rows),
        "unchanged": len(aligned_rows) - len(changed_rows),
        "changed": len(changed_rows),
        "changed_to_unknown": sum(
            row["temporal_final_colour"] == "unknown" for row in changed_rows
        ),
    }


def write_multistage_comparison(stage_paths: dict[str, Path], output_path: Path) -> dict:
    """Write lifecycle/frame-aligned results without assuming track IDs are unique."""
    def finals(path: Path) -> dict[tuple[int, int], dict]:
        grouped: dict[int, list[dict]] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record.get("event") == "final":
                    grouped.setdefault(int(record["track_id"]), []).append(record)
        return {
            (track_id, occurrence): record
            for track_id, records in grouped.items()
            for occurrence, record in enumerate(records, start=1)
        }

    stages = {name: finals(path) for name, path in stage_paths.items()}
    keys = sorted(set().union(*(set(records) for records in stages.values())))
    rows = []
    for track_id, lifecycle in keys:
        records = {name: stage.get((track_id, lifecycle)) for name, stage in stages.items()}
        frames = {record.get("frame_index") for record in records.values() if record}
        aligned = len(records) == len(stage_paths) and all(records.values()) and len(frames) == 1
        row = {"track_id": track_id, "lifecycle": lifecycle, "aligned": bool(aligned)}
        for name, record in records.items():
            row[f"{name}_colour"] = record.get("colour", "") if record else ""
            row[f"{name}_confidence"] = record.get("colour_confidence", "") if record else ""
            row[f"{name}_sample_count"] = record.get("sample_count", "") if record else ""
        rows.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["track_id"])
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "aligned_rows": sum(row["aligned"] for row in rows)}


def write_colour_feature_export(diagnostics_path: Path, output_path: Path) -> int:
    """Flatten per-ROI diagnostics into one training-ready row per observation."""
    rows: list[dict] = []

    def median(rois: list[dict], section: str, field: str):
        values = [
            roi.get(section, {}).get(field)
            for roi in rois
            if isinstance(roi.get(section), dict)
            and isinstance(roi.get(section, {}).get(field), (int, float))
        ]
        return round(float(np.median(values)), 4) if values else ""

    with diagnostics_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("event") != "sample" or not record.get("valid"):
                continue
            diagnostics = record.get("colour_diagnostics") or {}
            rois = [
                roi for roi in diagnostics.get("rois", [])
                if isinstance(roi, dict) and roi.get("accepted")
            ]
            labels = [str(roi.get("label", "unknown")) for roi in rois]
            raw_labels = [str(roi.get("hsv_label", roi.get("label", "unknown"))) for roi in rois]
            agreement = max(Counter(labels).values()) / len(labels) if labels else 0.0
            crop_quality = diagnostics.get("crop_quality") or {}
            rows.append({
                "frame_index": record.get("frame_index"),
                "track_id": record.get("track_id"),
                "feature_mode": diagnostics.get("feature_mode", "hsv"),
                "current_prediction": record.get("label"),
                "prediction_confidence": record.get("hsv_confidence"),
                "temporal_winner": record.get("winner"),
                "temporal_winner_share": record.get("winner_share"),
                "temporal_margin": record.get("winner_margin"),
                "crop_quality": crop_quality.get("combined", record.get("crop_quality")),
                "accepted_roi_count": len(rois),
                "roi_agreement": round(agreement, 4),
                "roi_labels": "|".join(labels),
                "raw_hsv_roi_labels": "|".join(raw_labels),
                "hsv_median_h": median(rois, "hsv_statistics", "median_h"),
                "hsv_median_s": median(rois, "hsv_statistics", "median_s"),
                "hsv_median_v": median(rois, "hsv_statistics", "median_v"),
                "hsv_mean_s": median(rois, "hsv_statistics", "mean_s"),
                "hsv_mean_v": median(rois, "hsv_statistics", "mean_v"),
                "hsv_low_saturation_ratio": median(
                    rois, "hsv_statistics", "low_saturation_ratio"
                ),
                "gray_mean": median(rois, "grayscale_statistics", "mean_intensity"),
                "gray_median": median(rois, "grayscale_statistics", "median_intensity"),
                "gray_p10": median(rois, "grayscale_statistics", "p10"),
                "gray_p25": median(rois, "grayscale_statistics", "p25"),
                "gray_p50": median(rois, "grayscale_statistics", "p50"),
                "gray_p75": median(rois, "grayscale_statistics", "p75"),
                "gray_p90": median(rois, "grayscale_statistics", "p90"),
                "gray_dark_pixel_ratio": median(
                    rois, "grayscale_statistics", "dark_pixel_ratio"
                ),
                "gray_bright_pixel_ratio": median(
                    rois, "grayscale_statistics", "bright_pixel_ratio"
                ),
                "lab_median_l": median(rois, "lab_statistics", "median_l"),
                "lab_median_a": median(rois, "lab_statistics", "median_a"),
                "lab_median_b": median(rois, "lab_statistics", "median_b"),
                "lab_mean_l": median(rois, "lab_statistics", "mean_l"),
                "lab_mean_a": median(rois, "lab_statistics", "mean_a"),
                "lab_mean_b": median(rois, "lab_statistics", "mean_b"),
                "lab_l_p10": median(rois, "lab_statistics", "l_p10"),
                "lab_l_p50": median(rois, "lab_statistics", "l_p50"),
                "lab_l_p90": median(rois, "lab_statistics", "l_p90"),
            })

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(rows[0]) if rows else ["frame_index", "track_id"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def run(args: argparse.Namespace) -> dict:
    video_path = args.video.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    protected_outputs = {
        (PROJECT_ROOT / "hsv_baseline.mp4").resolve(),
        (PROJECT_ROOT / "hsv_baseline_tracks.jsonl").resolve(),
        (PROJECT_ROOT / "hsv_temporal_v1.mp4").resolve(),
        (PROJECT_ROOT / "hsv_temporal_v1_tracks.jsonl").resolve(),
    }
    if output_path in protected_outputs or _diagnostics_path(output_path) in protected_outputs:
        raise RunnerError("refusing to overwrite preserved HSV baseline artifacts")
    if video_path == output_path:
        raise RunnerError("input and output video paths must differ")
    if args.max_frames is not None and args.max_frames <= 0:
        raise RunnerError("--max-frames must be greater than zero")

    roi_mode = getattr(args, "roi_mode", None) or cfg.COLOR_ROI_MODE
    neutral_mode = getattr(args, "neutral_mode", None) or cfg.COLOR_NEUTRAL_MODE
    normalization = getattr(args, "normalization", None) or cfg.COLOR_NORMALIZATION
    quality_mode = getattr(args, "quality_mode", None) or cfg.COLOR_QUALITY_MODE
    feature_mode = getattr(args, "colour_feature_mode", None) or cfg.COLOR_FEATURE_MODE
    max_samples = getattr(args, "max_colour_samples", None)
    if max_samples is not None:
        if max_samples < cfg.MIN_VALID_COLOR_SAMPLES:
            raise RunnerError("--max-colour-samples must be at least the minimum sample count")
        cfg.MAX_VALID_COLOR_SAMPLES = max_samples
    os.environ["COLOR_ROI_MODE"] = roi_mode
    os.environ["COLOR_NEUTRAL_MODE"] = neutral_mode
    os.environ["COLOR_NORMALIZATION"] = normalization
    os.environ["COLOR_QUALITY_MODE"] = quality_mode
    os.environ["COLOR_FEATURE_MODE"] = feature_mode

    device = resolve_requested_device(args.device)
    capture, info = open_video(video_path)
    writer = None
    diagnostics = None
    try:
        model, model_path = _prepare_vehicle_model(device, info)
        tracker = create_vehicle_tracker(frame_rate=cfg.FPS)
        perf = DiagnosticPerfMeter()
        pipeline = CameraPipeline(
            cam_id="local_mp4",
            perf=perf,
            plate_model=None,
            ocr_reader=None,
            tounes_model=None,
            review_writer=_NoopReviewWriter(),
        )
        writer = open_writer(output_path, info)
        diagnostics_path = _diagnostics_path(output_path)
        diagnostics = JsonlDiagnostics(diagnostics_path, echo=args.log_tracks)
        diagnostics.write(
            "run_start",
            video=str(video_path),
            output=str(output_path),
            device=device,
            model=str(model_path),
            source_fps=info.fps,
            width=info.width,
            height=info.height,
            source_frame_count=info.frame_count,
            k_classify=cfg.K_CLASSIFY,
            min_valid_color_samples=cfg.MIN_VALID_COLOR_SAMPLES,
            max_valid_color_samples=cfg.MAX_VALID_COLOR_SAMPLES,
            lost_frames=cfg.LOST_FRAMES,
            roi_mode=roi_mode,
            neutral_mode=neutral_mode,
            normalization=normalization,
            quality_mode=quality_mode,
            colour_feature_mode=feature_mode,
        )

        started = time.perf_counter()
        frames_processed = 0
        detector_total_ms = 0.0
        tracker_total_ms = 0.0
        last_frame = None

        while args.max_frames is None or frames_processed < args.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[1] != info.width or frame.shape[0] != info.height:
                raise RunnerError(
                    f"frame {frames_processed} size changed to {frame.shape[1]}x{frame.shape[0]}"
                )

            _, detector_ms, tracker_ms = _process_frame(
                model, device, tracker, pipeline, frame, frames_processed, diagnostics
            )
            detector_total_ms += detector_ms
            tracker_total_ms += tracker_ms

            annotated = frame.copy()
            draw_overlay(
                annotated,
                pipeline.active_profiles.values(),
                show_track_details=True,
            )
            writer.write(annotated)
            if args.display:
                cv2.imshow("live HSV baseline", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    last_frame = frame
                    frames_processed += 1
                    break

            last_frame = frame
            frames_processed += 1

        if last_frame is None:
            raise RunnerError("input video contains no decodable frames")

        drain_frames = _drain_tracks(pipeline, last_frame, frames_processed, diagnostics)
        elapsed = time.perf_counter() - started
        summary = {
            "video": str(video_path),
            "output": str(output_path),
            "diagnostics": str(diagnostics_path),
            "device": device,
            "model": str(model_path),
            "source_fps": info.fps,
            "source_frame_count": info.frame_count,
            "frames_processed": frames_processed,
            "frames_rendered": frames_processed,
            "drain_frames": drain_frames,
            "processing_seconds": round(elapsed, 3),
            "processing_fps": round(frames_processed / elapsed, 3) if elapsed else 0.0,
            "average_detector_ms": round(detector_total_ms / frames_processed, 3),
            "average_tracker_ms": round(tracker_total_ms / frames_processed, 3),
            "average_hsv_ms": round(perf.average_ms("colour"), 3),
            "hsv_calls": perf.stage_calls.get("colour", 0),
            "k_classify": cfg.K_CLASSIFY,
            "min_valid_color_samples": cfg.MIN_VALID_COLOR_SAMPLES,
            "max_valid_color_samples": cfg.MAX_VALID_COLOR_SAMPLES,
            "finalized_tracks": len(diagnostics.final_records),
            "early_locked_tracks": sum(
                record["decision"] == "early_lock" for record in diagnostics.final_records
            ),
            "tracks_reaching_max_samples": sum(
                record["sample_count"] == cfg.MAX_VALID_COLOR_SAMPLES
                for record in diagnostics.final_records
            ),
            "unknown_final_tracks": sum(
                record["colour"] == "unknown" for record in diagnostics.final_records
            ),
            "average_valid_samples_per_track": round(
                sum(record["sample_count"] for record in diagnostics.final_records)
                / len(diagnostics.final_records), 3
            ) if diagnostics.final_records else 0.0,
            "roi_mode": roi_mode,
            "neutral_mode": neutral_mode,
            "normalization": normalization,
            "quality_mode": quality_mode,
            "colour_feature_mode": feature_mode,
        }
        baseline_tracks = getattr(args, "baseline_tracks", None)
        if baseline_tracks is not None:
            comparison = write_baseline_comparison(
                baseline_tracks.expanduser().resolve(),
                diagnostics_path,
                _comparison_path(output_path),
            )
            summary.update(comparison)
        feature_output = getattr(args, "colour_features_output", None)
        if feature_output is not None:
            feature_rows = write_colour_feature_export(diagnostics_path, feature_output)
            summary.update({
                "colour_features_output": str(feature_output.expanduser().resolve()),
                "colour_feature_rows": feature_rows,
            })
        diagnostics.write("run_summary", **summary)
        print(json.dumps(summary, indent=2))
        return summary
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if diagnostics is not None:
            diagnostics.close()
        if args.display:
            cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        run(args)
        return 0
    except (RunnerError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
