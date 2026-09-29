"""
Train a yolo26n تونس-word DETECTOR on tounes_word_dataset/.

The detector finds the Arabic word region on a plate. Used for word-spotting
classification: تونس detected -> civilian (tounes); not detected -> other.

Run from plate_recognition/ (venv active) and watch it live:
    python train_word_detector.py
    python train_word_detector.py --epochs 80 --imgsz 512 --batch 32

Best weights are copied to models/tounes_word_detector.pt
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_YAML  = SCRIPT_DIR / "tounes_word_dataset" / "data.yaml"
MODELS_DIR = SCRIPT_DIR / "models"
OUT_WEIGHTS = MODELS_DIR / "tounes_word_detector.pt"


def load_model(weights: str):
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ERROR: ultralytics not installed. Run: pip install ultralytics")
    for cand in [weights, weights.replace("yolo26", "yolo11"), weights.replace("yolo26", "yolov8")]:
        try:
            m = YOLO(cand)
            if cand != weights:
                print(f"(note: {weights} unavailable; using {cand})")
            return m
        except Exception:
            continue
    sys.exit(f"ERROR: could not load {weights} or a fallback.")


def main():
    ap = argparse.ArgumentParser(description="Train yolo26n تونس-word detector")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--device", default="0")
    ap.add_argument("--weights", default="yolo26n.pt")
    ap.add_argument("--name", default="tounes_word")
    args = ap.parse_args()

    if not DATA_YAML.exists():
        sys.exit(f"ERROR: {DATA_YAML} not found (clone + git lfs pull the dataset first).")

    print(f"Training {args.weights} on {DATA_YAML}\n"
          f"  epochs={args.epochs} imgsz={args.imgsz} batch={args.batch} device={args.device}\n")
    model = load_model(args.weights)
    results = model.train(
        data=str(DATA_YAML),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(SCRIPT_DIR / "runs_word"),
        name=args.name,
        exist_ok=True,
        verbose=True,
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        best = Path(results.save_dir) / "weights" / "last.pt"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, OUT_WEIGHTS)
    print(f"\nDone. Best weights -> {OUT_WEIGHTS}")
    print(f"mAP50 / mAP50-95 are in the table above and in {results.save_dir}")


if __name__ == "__main__":
    main()
