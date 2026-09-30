"""Command-line entry point."""
from __future__ import annotations

import argparse
import logging

from .service import VehicleAnalysisService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone vehicle detection, tracking and traffic analytics")
    parser.add_argument("--video", required=True, help="input video path")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or an Ultralytics device index")
    parser.add_argument("--output", help="optional annotated MP4 output")
    parser.add_argument("--json-output", help="optional final JSON report")
    parser.add_argument("--events-output", help="optional ROI events JSONL")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--roi-config", help="ROI JSON file; enables ROI analytics")
    parser.add_argument("--model", help="override vehicle YOLO weights")
    parser.add_argument("--tracker", help="override ByteTrack YAML")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("Loading vehicle detector...")
    service = VehicleAnalysisService(
        device=args.device,
        enable_roi=bool(args.roi_config),
        roi_path=args.roi_config,
        model_path=args.model,
        tracker_path=args.tracker,
    )
    print(f"Processing: {args.video}")
    result = service.process_video(args.video, args.output, args.json_output, args.events_output, args.max_frames)
    print(f"Processed frames: {result['frames_processed']}")
    print(f"Tracks detected: {result['tracks_detected']}")
    print(f"Finalized tracks: {len(result['vehicles'])}")
    print(f"Processing time: {result['processing_seconds']:.1f} seconds")
    return 0
