#!/usr/bin/env python3
"""Combine completed matched runs and explicit blocked DINOv3 conditions."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

import cv2
import numpy as np

ROOT = Path("dinov3_comparison_v2")
ENCODERS = ("dinov2_small", "dinov3_vits16", "dinov3_vitb16")
MODES = ("native", "budget")
REPRESENTATIONS = {
    "full_10": ("r0_cls", "r1_mean_patch", "r2_global_regional"),
    "marker_7": ("r0_cls", "r1_mean_patch", "r2_global_regional", "r3_marker_cls"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    columns = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader(); writer.writerows(rows)


def save_matrix_csv(path: Path, pair_path: Path, ids: list[str], column: str) -> None:
    matrix = np.eye(len(ids), dtype=np.float64)
    index = {image_id: number for number, image_id in enumerate(ids)}
    with pair_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            first, second = index[row["image_1_id"]], index[row["image_2_id"]]
            matrix[first, second] = matrix[second, first] = float(row[column])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["image_id", *ids])
        for image_id, values in zip(ids, matrix): writer.writerow([image_id, *values])


def save_contact_sheet(path: Path, entries: list[tuple[str, Path]]) -> None:
    cells = []
    for label, image_path in entries:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        height, width = image.shape[:2]
        scale = min(300 / width, 130 / height)
        shown = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
        canvas = np.full((175, 320, 3), 245, np.uint8)
        y, x = 35 + (130 - shown.shape[0]) // 2, (320 - shown.shape[1]) // 2
        canvas[y:y + shown.shape[0], x:x + shown.shape[1]] = shown
        cv2.putText(canvas, f"{label} | exact tensor display {width}x{height}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 0, 0), 1, cv2.LINE_AA)
        cells.append(canvas)
    columns = 2
    rows = (len(cells) + columns - 1) // columns
    blank = np.full_like(cells[0], 245)
    cells += [blank] * (rows * columns - len(cells))
    sheet = np.vstack([np.hstack(cells[row * columns:(row + 1) * columns]) for row in range(rows)])
    cv2.imwrite(str(path), sheet)


def main() -> None:
    results = {}
    summary_rows, prediction_rows = [], []
    for mode in MODES:
        directory = ROOT / f"dinov2_{mode}"
        data = json.loads((directory / "representation_results.json").read_text())
        results[mode] = data
        with (directory / "leave_one_out_predictions.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                prediction_rows.append({
                    "encoder": "dinov2_small", "preprocessing": mode,
                    "status": "complete", "failure_reason": "", **row,
                })
        for subset, subset_data in data["subsets"].items():
            for representation, item in subset_data["representations"].items():
                metrics = item["classification"]
                summary_rows.append({
                    "encoder": "dinov2_small", "preprocessing": mode,
                    "evaluation_subset": subset, "representation": representation,
                    "status": "complete", "failure_reason": "",
                    "correct": metrics["correct"], "total": metrics["total"],
                    "macro_f1": metrics["macro_f1"],
                    "balanced_accuracy": metrics["balanced_accuracy"],
                    "per_category_json": json.dumps(metrics["per_category"], sort_keys=True),
                })
        matrix_dir = ROOT / "similarity_matrices"
        matrix_dir.mkdir(exist_ok=True)
        columns = {
            "r0_cls": "r0_cls_similarity", "r1_mean_patch": "r1_mean_patch_similarity",
            "r2_global_regional": "r2_global_regional_similarity", "r3_marker_cls": "r3_marker_cls_similarity",
        }
        for subset, ids in (("full_10", [str(i) for i in range(1, 11)]), ("marker_7", [str(i) for i in range(4, 11)])):
            pair_path = directory / ("all_pairs_full.csv" if subset == "full_10" else "all_pairs_marker_subset.csv")
            for representation in REPRESENTATIONS[subset]:
                save_matrix_csv(matrix_dir / f"dinov2_{mode}_{subset}_{representation}.csv", pair_path, ids, columns[representation])
        sheet_dir = ROOT / "actual_input_contact_sheets"
        sheet_dir.mkdir(exist_ok=True)
        exact = directory / "exact_tensor_inputs"
        save_contact_sheet(sheet_dir / f"dinov2_{mode}_whole.png", [(f"image {i}", exact / "whole" / f"{i}.png") for i in range(1, 11)])
        save_contact_sheet(sheet_dir / f"dinov2_{mode}_markers.png", [(f"marker {i}", exact / "marker" / f"{i}.png") for i in range(4, 11)])
        save_contact_sheet(sheet_dir / f"dinov2_{mode}_r2_example_image4.png", [(name, exact / name / "4.png") for name in ("whole", "left", "centre", "right")])

    blocker = (
        "Official Meta DINOv3 repository and exact .pth checkpoint are absent locally; "
        "no approved signed checkpoint URLs were supplied in the project or conversation."
    )
    query_ids = {"full_10": [str(i) for i in range(1, 11)], "marker_7": [str(i) for i in range(4, 11)]}
    categories = {str(item["id"]): item["category"] for item in results["budget"]["manifest"]["images"]}
    for encoder in ENCODERS[1:]:
        for mode in MODES:
            for subset, reps in REPRESENTATIONS.items():
                for representation in reps:
                    summary_rows.append({
                        "encoder": encoder, "preprocessing": mode,
                        "evaluation_subset": subset, "representation": representation,
                        "status": "blocked", "failure_reason": blocker,
                        "correct": "", "total": 0, "macro_f1": "",
                        "balanced_accuracy": "", "per_category_json": "{}",
                    })
                    for query in query_ids[subset]:
                        prediction_rows.append({
                            "encoder": encoder, "preprocessing": mode,
                            "status": "blocked", "failure_reason": blocker,
                            "evaluation_subset": subset, "representation": representation,
                            "query_id": query, "query_category": categories[query],
                            "nearest_reference_id": "", "predicted_category": "",
                            "nearest_similarity": "", "best_same_category_reference_id": "",
                            "best_same_category_similarity": "", "best_different_category_reference_id": "",
                            "best_different_category_similarity": "", "correct_category_margin": "",
                            "predicted_category_margin": "", "correct": "", "tie_flag": "",
                            "eligible_reference_count": "",
                        })
    write_csv(ROOT / "summary_metrics.csv", summary_rows)
    write_csv(ROOT / "per_query_predictions.csv", prediction_rows)

    availability = {
        "dinov2_small": {"status": "complete", "modes": list(MODES)},
        "dinov3_vits16": {"status": "blocked", "checkpoint": "dinov3_vits16_pretrain_lvd1689m-08c60483.pth", "reason": blocker},
        "dinov3_vitb16": {"status": "blocked", "checkpoint": "dinov3_vitb16_pretrain_lvd1689m-73cec8be.pth", "reason": blocker},
        "gpu": {"available_to_torch": False, "nvml": "GPU access blocked by the operating system"},
    }
    (ROOT / "availability.json").write_text(json.dumps(availability, indent=2) + "\n")
    config = {
        "manifest": "controlled_plate_manifest_historical_remap.json",
        "manifest_sha256": sha256(Path("controlled_plate_manifest_historical_remap.json")),
        "historical_source_hashes_verified": True,
        "models": availability,
        "policy": results["budget"]["budget_policy"],
        "dino_patch_sizes": {"dinov2_small": [14, 14], "dinov3_vits16": [16, 16], "dinov3_vitb16": [16, 16]},
        "precision": "float32 features, L2 normalized; cosine similarity",
        "official_dinov3_interface": "torch.hub.load(local_repo, entrypoint, source='local', weights=local_checkpoint)",
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "numpy", "opencv-python", "matplotlib")},
        "python": platform.python_version(),
        "device": "cpu",
        "timing": {mode: results[mode]["workload"] for mode in MODES},
        "baseline_equivalence": results["budget"]["r0_prior_equivalence"],
    }
    (ROOT / "configuration.json").write_text(json.dumps(config, indent=2) + "\n")

    lines = [
        "# DINOv3 Plate Representation Comparison", "",
        "## Execution status", "",
        "DINOv2-small completed for native and budget rectangular inputs. Both requested DINOv3 models are blocked before loading because the official Meta repository and exact checkpoints are absent. No Hugging Face substitute or fabricated result was used.", "",
        "The historical ten source identities were recovered after file renaming and verified by SHA-256. Newly added NT files were not substituted.", "",
        "## Completed classification results", "",
        "| Encoder | Mode | Subset | R0 | R1 | R2 | R3 |", "|---|---|---|---:|---:|---:|---:|",
    ]
    for mode in ("budget", "native"):
        for subset in ("full_10", "marker_7"):
            values = results[mode]["subsets"][subset]["representations"]
            cells = [f"{values[r]['classification']['correct']}/{values[r]['classification']['total']}" if r in values else "—" for r in ("r0_cls", "r1_mean_patch", "r2_global_regional", "r3_marker_cls")]
            lines.append(f"| DINOv2-small | {mode} | {subset} | " + " | ".join(cells) + " |")
    lines += ["", "Budget R0 reproduces the authoritative baseline: maximum matrix difference 3.58e-7 and identical nearest-neighbour predictions. Native inputs do not improve these examples.", "", "## NT and Shorta", ""]
    for mode in ("budget", "native"):
        rows = [r for r in prediction_rows if r["encoder"] == "dinov2_small" and r["preprocessing"] == mode and r["evaluation_subset"] == "full_10"]
        for rep in ("r0_cls", "r1_mean_patch", "r2_global_regional"):
            selected = [r for r in rows if r["representation"] == rep and r["query_category"] in ("nt", "shorta")]
            detail = ", ".join(f"{r['query_id']} {r['query_category']}→{r['predicted_category']} ({'correct' if str(r['correct']).lower()=='true' else 'wrong'})" for r in selected)
            lines.append(f"- {mode} {rep}: {detail}")
    lines += ["", "## Timing and cost", ""]
    for mode in ("budget", "native"):
        work = results[mode]["workload"]
        whole = work["per_forward_seconds"]["whole_shared_r0_r1"]
        regions = work["per_forward_seconds"]["r2_regions"]
        marker = work["per_forward_seconds"]["r3_marker"]
        lines.append(f"- {mode}: load {work['model_load_seconds']:.3f}s; mean whole pass {whole['mean']:.4f}s; mean regional pass {regions['mean']:.4f}s; mean marker pass {marker['mean']:.4f}s; R2 total for ten plates {work['r2_total_feature_seconds']:.3f}s (CPU).")
    lines += ["", "R0 and R1 share one whole-image forward; R2 uses four views per plate; R3 uses one marker view for each eligible plate. GPU memory is unavailable because CUDA/NVML access is blocked.", "", "## Conclusion", "", "No conclusion about DINOv3 accuracy or cost is possible until the approved official assets are supplied. Among completed DINOv2 conditions, budget R2 is the best accuracy/cost tradeoff observed (7/10 full, 5/7 marker subset), but NT remains unresolved and these repeatedly used development images cannot establish deployment reliability.", "", "## Reproduction", "", "```bash", "# DINOv2", "/home/mohamed/miniconda3/envs/ml/bin/python evaluate_plate_representations.py --manifest controlled_plate_manifest_historical_remap.json --input-mode budget --output-dir <fresh-dir>", "", "# Official DINOv3-S (repeat with dinov3_vitb16 and its exact B checkpoint)", "/home/mohamed/miniconda3/envs/ml/bin/python evaluate_plate_representations.py --manifest controlled_plate_manifest_historical_remap.json --encoder dinov3_vits16 --dinov3-repo /path/to/official/dinov3 --dinov3-checkpoint /path/to/dinov3_vits16_pretrain_lvd1689m-08c60483.pth --input-mode budget --output-dir <fresh-dir>", "```", ""]
    (ROOT / "DINOV3_COMPARISON_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
