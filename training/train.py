"""
Fine-tune RT-DETR-L on the streetdet dataset.

Run from vehicle_services/:
    python training/train.py [--epochs 100] [--imgsz 640] [--batch 8] [--device 0] [--weights rtdetr-l.pt]

Note: RT-DETR-L is a transformer detector — heavier on VRAM and slower than yolo26m.
Start --batch low (8) on a 16 GB card and raise while watching `nvidia-smi`.

Outputs (kept SEPARATE from the YOLO run so nothing is overwritten):
    run dir   → training/runs/rtdetr_streetdet_v1/
    weights   → common/models/vehicle_rtdetr.pt
    class map → common/models/class_map_rtdetr.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TRAINING_DIR = Path(__file__).resolve().parent
VS_DIR       = TRAINING_DIR.parent
TRAIN_YAML   = TRAINING_DIR / "configs" / "streetdet_train.yaml"
MODELS_DIR   = VS_DIR / "common" / "models"
OUT_WEIGHTS  = MODELS_DIR / "vehicle_rtdetr.pt"        # separate from vehicle_yolo26.pt
OUT_CLASSMAP = MODELS_DIR / "class_map_rtdetr.json"    # separate from class_map.json

CLASS_NAMES = ["bus", "camper", "car", "cyclist", "motorcycle",
               "person", "tractor", "truck", "van"]

# Sprint-2 service taxonomy (mirrors common/config.py TAXONOMY)
SERVICE_TAXONOMY = {
    "0": "bus",
    "1": "car",        # camper → car
    "2": "car",
    "3": "cyclist",
    "4": "motorcycle",
    "5": "person",
    "6": "truck",      # tractor → truck
    "7": "truck",
    "8": "van",
}


# ---------------------------------------------------------------------------
# RT-DETR model loader
# ---------------------------------------------------------------------------

def load_rtdetr_model(weights: str):
    try:
        from ultralytics import RTDETR
    except ImportError:
        sys.exit("ERROR: ultralytics not installed. Run: pip install ultralytics")
    try:
        return RTDETR(weights)   # downloads rtdetr-l.pt (COCO-pretrained) on first run
    except Exception as e:
        sys.exit(f"ERROR: could not load RT-DETR model {weights}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Fine-tune RT-DETR-L on streetdet")
    ap.add_argument("--epochs",  type=int,   default=100,       help="Training epochs (default 100)")
    ap.add_argument("--imgsz",   type=int,   default=640,       help="Input image size (default 640)")
    ap.add_argument("--batch",   type=int,   default=8,         help="Batch size (RT-DETR-L is heavy; start 8, raise while watching VRAM)")
    ap.add_argument("--device",  type=str,   default="0",       help="Device: 0, cpu, 0,1 … (default '0')")
    ap.add_argument("--weights", type=str,   default="rtdetr-l.pt", help="Pretrained checkpoint (RT-DETR-L)")
    ap.add_argument("--name",    type=str,   default="rtdetr_streetdet_v1", help="Run name (separate from the YOLO run)")
    args = ap.parse_args()

    if not TRAIN_YAML.exists():
        sys.exit(
            f"ERROR: {TRAIN_YAML} not found.\n"
            f"Run first: python training/prepare_streetdet.py"
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading model: {args.weights}")
    model = load_rtdetr_model(args.weights)

    print(f"Starting training — epochs={args.epochs}, imgsz={args.imgsz}, "
          f"batch={args.batch}, device={args.device}")

    # Note: no forced mosaic/hsv knobs — RT-DETR uses ultralytics' RT-DETR-appropriate
    # augmentation defaults (heavy YOLO-style mosaic can hurt DETR-family training).
    results = model.train(
        data=str(TRAIN_YAML),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(TRAINING_DIR / "runs"),
        name=args.name,
        exist_ok=True,
        verbose=True,
    )

    # Locate best weights
    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        # Fallback — ultralytics sometimes uses last.pt
        best = Path(results.save_dir) / "weights" / "last.pt"
    if not best.exists():
        sys.exit(f"ERROR: could not find trained weights under {results.save_dir}")

    shutil.copy2(best, OUT_WEIGHTS)
    print(f"\nWeights saved → {OUT_WEIGHTS}")

    # Write class_map.json
    class_map = {
        "model_classes": {str(i): name for i, name in enumerate(CLASS_NAMES)},
        "service_taxonomy": SERVICE_TAXONOMY,
        "trained_weights": args.weights,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
    }
    OUT_CLASSMAP.write_text(json.dumps(class_map, indent=2))
    print(f"Class map saved → {OUT_CLASSMAP}")


if __name__ == "__main__":
    main()
