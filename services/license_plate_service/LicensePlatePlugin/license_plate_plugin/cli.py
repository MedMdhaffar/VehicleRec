"""Command-line interface for index creation, inference, and reproduction."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .classifier import PlateCategoryClassifier, build_reference_index
from .config import load_config
from .errors import PluginError
from .evaluation import run_leave_one_out
from .utils import json_ready


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="license-plate-plugin")
    root.add_argument("--config", type=Path, required=True, help="JSON configuration; relative asset paths resolve from this file.")
    commands = root.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-index", help="Generate the frozen R2 reference index.")
    build.add_argument("--output", type=Path)
    one = commands.add_parser("classify", help="Classify one complete plate crop.")
    one.add_argument("image", type=Path)
    one.add_argument("--output-dir", type=Path, required=True)
    folder = commands.add_parser("classify-folder", help="Classify every supported image in a folder.")
    folder.add_argument("input_dir", type=Path)
    folder.add_argument("--output-dir", type=Path, required=True)
    evaluate = commands.add_parser("evaluate", help="Run leave-one-reference-out reproduction.")
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--authoritative-csv", type=Path)
    evaluate.add_argument("--similarity-tolerance", type=float, default=1e-6)
    return root


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if args.command == "build-index":
        index = build_reference_index(config, args.output)
        print(json.dumps({"index": str(index.path), "fingerprint": index.compatibility_fingerprint}, indent=2))
        return
    if args.command == "evaluate":
        summary = run_leave_one_out(
            config,
            args.output_dir.resolve(),
            None if args.authoritative_csv is None else args.authoritative_csv.resolve(),
            args.similarity_tolerance,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    classifier = PlateCategoryClassifier(config)
    if args.command == "classify":
        result = classifier.classify(args.image, args.output_dir)
        write_result(args.output_dir / "result.json", result)
        print(json.dumps(json_ready(result), ensure_ascii=False, indent=2))
        return
    if not args.input_dir.is_dir():
        raise PluginError(f"Input folder does not exist: {args.input_dir}")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise PluginError(f"Output folder is not empty: {args.output_dir}")
    paths = sorted(
        path for path in args.input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    )
    if not paths:
        raise PluginError(f"No supported images found in: {args.input_dir}")
    rows, results = [], []
    for path in paths:
        result = classifier.classify(path, args.output_dir / "per_image" / path.stem)
        results.append(result)
        rows.append(
            {
                "input_path": str(path.resolve()),
                "visual_category": result["visual"]["visual_category"],
                "nearest_reference_id": result["visual"]["nearest_reference_id"],
                "nearest_similarity": result["visual"]["nearest_similarity"],
                "category_margin": result["visual"]["predicted_category_margin"],
                "raw_ocr": result["ocr"]["raw_text"],
                "native_ocr_confidence": result["ocr"]["native_confidence"],
                "nt_evidence": result["ocr"]["nt_evidence"],
                "final_category": result["decision"]["final_category"],
                "decision_source": result["decision"]["source"],
                "conflict_flag": result["decision"]["conflict_flag"],
                "ocr_failure_flag": result["decision"]["ocr_failure_flag"],
                "tie_flag": result["decision"]["tie_flag"],
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_result(args.output_dir / "results.json", {"results": results})
    with (args.output_dir / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(f"Classified {len(rows)} images; outputs: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parser().parse_args())
        return 0
    except PluginError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

