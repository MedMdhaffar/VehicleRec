from __future__ import annotations

import csv
import json
from argparse import Namespace
from pathlib import Path

import cv2
import numpy as np
import pytest

import live_feed.config as cfg
from live_feed.pipeline import CameraPipeline
from live_feed.schemas import VehicleProfile
from scripts import test_live_pipeline_video as runner


class _Boxes:
    def cpu(self):
        return self

    def numpy(self):
        return self


class _Model:
    def predict(self, frames, **kwargs):
        assert len(frames) == 1
        assert kwargs["conf"] == cfg.TRACK_CONF
        return [type("Result", (), {"boxes": _Boxes()})()]


class _Tracker:
    def update(self, boxes, frame):
        assert isinstance(boxes, _Boxes)
        return np.array([[1, 1, 7, 7, 42, 0.9, 2, 0]], dtype=np.float32)


def _pipeline() -> CameraPipeline:
    return CameraPipeline(
        cam_id="test",
        perf=runner.DiagnosticPerfMeter(),
        plate_model=None,
        ocr_reader=None,
        tounes_model=None,
        review_writer=runner._NoopReviewWriter(),
    )


def _write_test_mp4(path: Path, frames: int = 2) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (16, 12))
    assert writer.isOpened()
    for value in range(frames):
        writer.write(np.full((12, 16, 3), value, dtype=np.uint8))
    writer.release()


def test_local_mp4_opens_without_vms(tmp_path):
    path = tmp_path / "input.mp4"
    _write_test_mp4(path)

    capture, info = runner.open_video(path)
    try:
        assert capture.isOpened()
        assert info.width == 16
        assert info.height == 12
        assert info.fps == pytest.approx(10.0)
    finally:
        capture.release()

    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "vms_client" not in source
    assert "live_feed.multistream" not in source


def test_missing_video_is_rejected(tmp_path):
    with pytest.raises(runner.RunnerError, match="does not exist"):
        runner.open_video(tmp_path / "missing.mp4")


def test_output_writer_is_created(tmp_path):
    output = tmp_path / "output.mp4"
    info = runner.VideoInfo(fps=10.0, width=16, height=12, frame_count=1)
    writer = runner.open_writer(output, info)
    writer.write(np.zeros((12, 16, 3), dtype=np.uint8))
    writer.release()

    assert output.is_file()
    assert output.stat().st_size > 0


def test_frame_loop_uses_camera_pipeline_and_eof_finalizes(tmp_path):
    diagnostics_path = tmp_path / "tracks.jsonl"
    diagnostics = runner.JsonlDiagnostics(diagnostics_path)
    pipeline = _pipeline()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    try:
        for frame_idx in range(5):
            runner._process_frame(_Model(), "cpu", _Tracker(), pipeline, frame, frame_idx, diagnostics)

        profile = pipeline.active_profiles[42]
        assert cfg.K_CLASSIFY == 2
        assert [sample.label for sample in profile._colour_samples] == ["black"] * 5
        assert profile._type_locked is True
        assert profile._colour_finalized is True
        assert profile.colour == "black"

        drained = runner._drain_tracks(pipeline, frame, 5, diagnostics)
        assert drained == cfg.LOST_FRAMES + 1
        assert pipeline.active_profiles == {}
    finally:
        diagnostics.close()

    records = [json.loads(line) for line in diagnostics_path.read_text().splitlines()]
    assert [record["event"] for record in records].count("sample") == 5
    assert [record["event"] for record in records].count("lock") == 1
    final = next(record for record in records if record["event"] == "final")
    assert final["reason"] == "eof_drain"
    assert final["sample_count"] == 5
    assert final["colour"] == "black"


def test_cli_validation_happens_before_model_loading(tmp_path):
    args = Namespace(
        video=tmp_path / "missing.mp4",
        output=tmp_path / "output.mp4",
        device="cpu",
        max_frames=None,
        display=False,
        log_tracks=False,
        baseline_tracks=None,
    )
    with pytest.raises(runner.RunnerError, match="does not exist"):
        runner.run(args)


def test_no_learned_colour_dependency_is_reintroduced():
    source = Path(runner.__file__).read_text(encoding="utf-8").lower()
    assert "torchvision" not in source


def test_live_colour_path_has_no_external_agent_or_queue():
    import live_feed.pipeline as pipeline_module

    source = Path(pipeline_module.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "colour" + "_verifier",
        "open" + "router",
        "httpx",
        "requests.",
    )
    assert all(token not in source for token in forbidden)

    pipeline = _pipeline()
    assert not hasattr(pipeline, "_" + "verifier_worker")
    assert not hasattr(pipeline, "_" + "verifier_crops")


def test_public_profile_and_websocket_have_no_external_colour_state():
    profile = VehicleProfile(track_id=1, bbox=[0, 0, 10, 10])
    forbidden_fields = {
        "colour_" + "verification_status",
        "colour_" + "lifecycle_id",
        "colour_" + "source",
    }
    assert forbidden_fields.isdisjoint(vars(profile))

    source = (runner.PROJECT_ROOT / "live_feed" / "app.py").read_text(encoding="utf-8")
    assert "colour_" + "verified" not in source


def test_removed_external_colour_cli_flags_are_rejected(tmp_path):
    with pytest.raises(SystemExit):
        runner.parse_args([
            "--video", str(tmp_path / "in.mp4"),
            "--output", str(tmp_path / "out.mp4"),
            "--colour-verifier-mode", "sync",
        ])


def test_comparison_pairs_reused_track_ids_by_lifecycle_and_frame(tmp_path):
    baseline = tmp_path / "baseline.jsonl"
    temporal = tmp_path / "temporal.jsonl"
    comparison = tmp_path / "comparison.csv"
    baseline.write_text(
        '\n'.join([
            json.dumps({"event": "final", "track_id": 9, "frame_index": 20,
                        "colour": "white", "colour_confidence": 0.6, "sample_count": 2}),
            json.dumps({"event": "final", "track_id": 9, "frame_index": 80,
                        "colour": "blue", "colour_confidence": 0.7, "sample_count": 2}),
        ]) + '\n',
        encoding="utf-8",
    )
    temporal.write_text(
        '\n'.join([
            json.dumps({"event": "final", "track_id": 9, "frame_index": 20,
                        "colour": "white", "colour_confidence": 0.9, "sample_count": 5}),
            json.dumps({"event": "final", "track_id": 9, "frame_index": 80,
                        "colour": "unknown", "colour_confidence": 0.5, "sample_count": 12}),
        ]) + '\n',
        encoding="utf-8",
    )

    summary = runner.write_baseline_comparison(baseline, temporal, comparison)
    assert summary["aligned_rows"] == 2
    assert summary["unchanged"] == 1
    assert summary["changed"] == 1
    assert summary["changed_to_unknown"] == 1


def test_colour_feature_export_flattens_one_valid_observation(tmp_path):
    diagnostics = tmp_path / "tracks.jsonl"
    output = tmp_path / "features.csv"
    diagnostics.write_text(json.dumps({
        "event": "sample", "valid": True, "frame_index": 3, "track_id": 7,
        "label": "silver/gray", "hsv_confidence": 0.8,
        "winner": "silver/gray", "winner_share": 1.0, "winner_margin": 1.0,
        "crop_quality": 0.7,
        "colour_diagnostics": {
            "feature_mode": "hsv_lab",
            "rois": [{
                "accepted": True, "label": "silver/gray", "hsv_label": "blue",
                "hsv_statistics": {"median_h": 110, "median_s": 45, "median_v": 120,
                                   "low_saturation_ratio": 0.7},
                "grayscale_statistics": {"mean_intensity": 105, "median_intensity": 100,
                                         "p10": 60, "p90": 180},
                "lab_statistics": {"median_l": 110, "median_a": 129, "median_b": 119,
                                   "l_p10": 65, "l_p90": 190},
            }],
        },
    }) + "\n", encoding="utf-8")

    assert runner.write_colour_feature_export(diagnostics, output) == 1
    rows = list(csv.DictReader(output.open(newline="", encoding="utf-8")))
    assert rows[0]["feature_mode"] == "hsv_lab"
    assert rows[0]["raw_hsv_roi_labels"] == "blue"
    assert rows[0]["lab_median_l"] == "110.0"
