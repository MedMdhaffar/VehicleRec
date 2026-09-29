"""
Fine-tune a yolo26n-cls plate-CATEGORY classifier on the synthetic dataset.

Takes synthetic_data/<category>/*.jpg, builds a train/val split in the layout
ultralytics expects, and trains. Best weights -> models/plate_category_cls.pt.

Run from plate_recognition/:
    python train_classifier.py [--epochs 60] [--imgsz 224] [--batch 64] [--val-frac 0.15]

Then test on REAL plate crops:
    yolo classify predict model=models/plate_category_cls.pt source=<crop_or_folder>
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SYNTH_DIR  = SCRIPT_DIR / "synthetic_data"
DATASET    = SCRIPT_DIR / "cls_dataset"          # train/val structure built here
MODELS_DIR = SCRIPT_DIR / "models"
OUT_MODEL  = MODELS_DIR / "plate_category_cls.pt"


def build_split(src: Path, dst: Path, val_frac: float, seed: int):
    rng = random.Random(seed)
    if dst.exists():
        shutil.rmtree(dst)
    cats = sorted(d for d in src.iterdir() if d.is_dir())
    if not cats:
        sys.exit(f"ERROR: no category folders in {src}. Generate data first:\n"
                 f"  python synthetic_generator.py --augment --count 400")
    counts = {}
    for cat in cats:
        imgs = sorted(cat.glob("*.jpg"))
        rng.shuffle(imgs)
        n_val = max(1, int(len(imgs) * val_frac))
        for split, group in [("val", imgs[:n_val]), ("train", imgs[n_val:])]:
            outd = dst / split / cat.name
            outd.mkdir(parents=True, exist_ok=True)
            for p in group:
                shutil.copy2(p, outd / p.name)
        counts[cat.name] = (len(imgs) - n_val, n_val)
    print("Split (train/val) per class:")
    for k, (tr, va) in counts.items():
        print(f"  {k:15s} {tr:5d} / {va}")
    return list(counts)


def load_cls_model(weights: str):
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
    sys.exit(f"ERROR: could not load a classification model ({weights} or fallbacks).")


def main():
    ap = argparse.ArgumentParser(description="Train plate-category classifier (yolo26n-cls)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=224)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--weights", default="yolo26n-cls.pt")
    ap.add_argument("--device", default="0")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    classes = build_split(SYNTH_DIR, DATASET, args.val_frac, args.seed)
    print(f"\n{len(classes)} classes: {classes}\n")

    model = load_cls_model(args.weights)
    results = model.train(
        data=str(DATASET),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(SCRIPT_DIR / "runs_cls"),
        name="plate_category",
        exist_ok=True,
        verbose=True,
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        best = Path(results.save_dir) / "weights" / "last.pt"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, OUT_MODEL)
    print(f"\nWeights saved -> {OUT_MODEL}")
    print("Test on real crops:\n"
          f"  yolo classify predict model={OUT_MODEL} source=<real_plate_crop_or_folder>")


if __name__ == "__main__":
    main()
