"""
Evaluate the trained vehicle model and write EVAL_REPORT.md.

Two evaluation passes:
  1. In-distribution: streetdet validation set (all 9 classes, van highlighted)
  2. Cross-domain:    KICT remapped set (bus index 0, truck index 7 only)

Run from vehicle_services/:
    python training/eval.py [--model common/models/vehicle_yolo26.pt]
                            [--streetdet-yaml training/configs/streetdet_train.yaml]
                            [--kict-yaml training/configs/kict_eval.yaml]
                            [--output EVAL_REPORT.md]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TRAINING_DIR    = Path(__file__).resolve().parent
VS_DIR          = TRAINING_DIR.parent
DEFAULT_MODEL   = VS_DIR / "common" / "models" / "vehicle_yolo26.pt"
DEFAULT_SD_YAML = TRAINING_DIR / "configs" / "streetdet_train.yaml"
DEFAULT_KI_YAML = TRAINING_DIR / "configs" / "kict_eval.yaml"
DEFAULT_REPORT  = VS_DIR / "EVAL_REPORT.md"

CLASS_NAMES = ["bus", "camper", "car", "cyclist", "motorcycle",
               "person", "tractor", "truck", "van"]
VAN_IDX   = 8
BUS_IDX   = 0
TRUCK_IDX = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_model(model_path: Path):
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ERROR: ultralytics not installed.")
    if not model_path.exists():
        sys.exit(f"ERROR: model not found: {model_path}\nRun training/train.py first.")
    return YOLO(str(model_path))


def run_val(model, data_yaml: Path, split: str = "val") -> object | None:
    if not data_yaml.exists():
        print(f"  SKIP: yaml not found: {data_yaml}")
        return None
    try:
        results = model.val(data=str(data_yaml), split=split, verbose=False)
        return results
    except Exception as e:
        print(f"  ERROR during val on {data_yaml.name}: {e}")
        return None


def extract_per_class(results) -> list[dict]:
    """Extract per-class metrics from ultralytics val results."""
    metrics = []
    try:
        # ultralytics stores per-class data in results.box
        box = results.box
        # ap_class_index maps positions → class ids
        class_indices = box.ap_class_index.tolist() if hasattr(box, "ap_class_index") else []
        p  = box.p.tolist()   if hasattr(box, "p")  else []
        r  = box.r.tolist()   if hasattr(box, "r")  else []
        ap50   = box.ap50.tolist()   if hasattr(box, "ap50")   else []
        ap5095 = box.ap.tolist()     if hasattr(box, "ap")     else []

        for i, cls_id in enumerate(class_indices):
            metrics.append({
                "class_id": int(cls_id),
                "name":     CLASS_NAMES[int(cls_id)] if int(cls_id) < len(CLASS_NAMES) else str(cls_id),
                "P":        p[i]     if i < len(p)     else float("nan"),
                "R":        r[i]     if i < len(r)     else float("nan"),
                "mAP50":    ap50[i]  if i < len(ap50)  else float("nan"),
                "mAP5095":  ap5095[i] if i < len(ap5095) else float("nan"),
            })
    except Exception as e:
        print(f"  WARN: could not extract per-class metrics: {e}")
    return metrics


def fmt(v: float) -> str:
    return f"{v:.4f}" if v == v else "  N/A"  # nan check


def build_report(
    model_path: Path,
    sd_results,
    kict_results,
    epochs: int | None,
    imgsz: int | None,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# EVAL_REPORT.md — Vehicle Model Evaluation",
        "",
        f"**Date:** {now}  ",
        f"**Model:** `{model_path}`  ",
        f"**Epochs:** {epochs or 'unknown'}  ",
        f"**imgsz:** {imgsz or 'unknown'}  ",
        "",
        "---",
        "",
    ]

    # ---- Streetdet in-distribution ----
    lines += [
        "## 1. In-distribution: streetdet validation set",
        "",
        "> All 9 model classes. Van is the *gap class* — the metric that justifies this custom model.",
        "",
    ]

    if sd_results is None:
        lines.append("*Evaluation skipped (yaml missing or error).*\n")
    else:
        try:
            box = sd_results.box
            overall_map50   = float(box.map50)
            overall_map5095 = float(box.map)
        except Exception:
            overall_map50 = overall_map5095 = float("nan")

        lines += [
            f"**Overall mAP@50:** {fmt(overall_map50)}  ",
            f"**Overall mAP@50-95:** {fmt(overall_map5095)}",
            "",
            "### Per-class metrics",
            "",
            "| Class | ID | Precision | Recall | mAP@50 | mAP@50-95 |",
            "|-------|----|-----------|--------|--------|-----------|",
        ]

        per_class = extract_per_class(sd_results)
        for m in sorted(per_class, key=lambda x: x["class_id"]):
            van_marker = " ← gap class" if m["class_id"] == VAN_IDX else ""
            lines.append(
                f"| **{m['name']}**{van_marker} | {m['class_id']} "
                f"| {fmt(m['P'])} | {fmt(m['R'])} | {fmt(m['mAP50'])} | {fmt(m['mAP5095'])} |"
            )
        lines.append("")

    # ---- KICT cross-domain ----
    lines += [
        "---",
        "",
        "## 2. Cross-domain: KICT (Korean highway CCTV)",
        "",
        "> **Bus and truck only.** KICT class 01 (car/van conflated) is unscoreable and excluded.",
        "> These numbers measure generalization, not target-domain (Tunisia footage) performance.",
        "",
    ]

    if kict_results is None:
        lines.append("*Evaluation skipped (yaml missing, dataset not converted, or error).*\n")
        lines.append("Run `python training/voc_to_yolo_kict.py` first.\n")
    else:
        try:
            box = kict_results.box
            kict_map50   = float(box.map50)
            kict_map5095 = float(box.map)
        except Exception:
            kict_map50 = kict_map5095 = float("nan")

        lines += [
            f"**Overall mAP@50 (bus+truck):** {fmt(kict_map50)}  ",
            f"**Overall mAP@50-95 (bus+truck):** {fmt(kict_map5095)}",
            "",
            "### Per-class metrics (bus / truck)",
            "",
            "| Class | ID | Precision | Recall | mAP@50 | mAP@50-95 |",
            "|-------|----|-----------|--------|--------|-----------|",
        ]

        per_class = extract_per_class(kict_results)
        for m in per_class:
            if m["class_id"] in (BUS_IDX, TRUCK_IDX):
                lines.append(
                    f"| **{m['name']}** | {m['class_id']} "
                    f"| {fmt(m['P'])} | {fmt(m['R'])} | {fmt(m['mAP50'])} | {fmt(m['mAP5095'])} |"
                )
        lines.append("")

    lines += [
        "---",
        "",
        "## Notes",
        "",
        "- **Domain gap:** streetdet is general web/street imagery; target domain is Tunisia CCTV footage.",
        "  These numbers are a baseline. Phase D plans a fine-tune on real target frames.",
        "- **Van** recall is the primary metric driving this sprint — monitor closely.",
        "- **tractor** (33 train instances) expected near-zero AP; acceptable or merge to truck in Sprint 2.",
        "- **KICT** is test-only for v1; promoting it to training data is deferred (ROADMAP §8).",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate vehicle_yolo26 model")
    ap.add_argument("--model",         default=str(DEFAULT_MODEL),   help="Path to .pt weights")
    ap.add_argument("--streetdet-yaml", default=str(DEFAULT_SD_YAML), help="streetdet val yaml")
    ap.add_argument("--kict-yaml",      default=str(DEFAULT_KI_YAML), help="KICT eval yaml")
    ap.add_argument("--output",        default=str(DEFAULT_REPORT),  help="Output report path")
    args = ap.parse_args()

    model_path = Path(args.model)
    sd_yaml    = Path(args.streetdet_yaml)
    kict_yaml  = Path(args.kict_yaml)
    out_path   = Path(args.output)

    model = load_model(model_path)

    # Try to read training metadata from class_map.json
    epochs = imgsz = None
    classmap_path = model_path.parent / "class_map.json"
    if classmap_path.exists():
        import json
        cm = json.loads(classmap_path.read_text())
        epochs = cm.get("epochs")
        imgsz  = cm.get("imgsz")

    print("\n=== In-distribution eval (streetdet val) ===")
    sd_results = run_val(model, sd_yaml)

    print("\n=== Cross-domain eval (KICT) ===")
    kict_results = run_val(model, kict_yaml)

    report = build_report(model_path, sd_results, kict_results, epochs, imgsz)
    out_path.write_text(report)
    print(f"\nReport written → {out_path}")


if __name__ == "__main__":
    main()
