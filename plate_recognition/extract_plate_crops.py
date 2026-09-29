"""
Extract TIGHT plate crops from images using the finetuned plate detector
(best_matricule.pt). Ignores any dataset labels — runs the detector on the raw
images so crops match what the deployed pipeline produces. Tight crops become
the 'tounes' (civilian) class for the binary classifier.

Run from plate_recognition/:
    python extract_plate_crops.py --src "Tunisian license plate" --out civilian_crops
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = SCRIPT_DIR / "models" / "best_matricule.pt"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main():
    ap = argparse.ArgumentParser(description="Extract tight plate crops via the plate detector")
    ap.add_argument("--src", type=Path, required=True, help="folder of images (searched recursively)")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="plate detector weights")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "civilian_crops")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--pad", type=float, default=0.02, help="fractional padding around the box")
    ap.add_argument("--min-w", type=int, default=40, help="skip crops narrower than this (px)")
    args = ap.parse_args()

    if not args.model.exists():
        sys.exit(f"ERROR: detector not found at {args.model}")
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ERROR: ultralytics not installed.")
    model = YOLO(str(args.model))

    args.out.mkdir(parents=True, exist_ok=True)
    images = [p for p in args.src.rglob("*") if p.suffix.lower() in IMG_EXTS]
    if not images:
        sys.exit(f"ERROR: no images found under {args.src}")
    print(f"Detecting plates in {len(images)} images...")

    n, no_det = 0, 0
    for img_path in images:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        res = model.predict(img, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
        if len(res.boxes) == 0:
            no_det += 1
            continue
        for i, b in enumerate(res.boxes, 1):
            x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
            bw, bh = x2 - x1, y2 - y1
            px, py = int(bw * args.pad), int(bh * args.pad)
            x1, y1 = max(0, x1 - px), max(0, y1 - py)
            x2, y2 = min(w, x2 + px), min(h, y2 + py)
            if x2 - x1 < args.min_w:
                continue
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            cv2.imwrite(str(args.out / f"{img_path.stem}_p{i}.jpg"), crop)
            n += 1
    print(f"Extracted {n} tight plate crops -> {args.out}  ({no_det} images had no detection)")


if __name__ == "__main__":
    main()
