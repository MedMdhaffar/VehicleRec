"""
Word-spotting plate classifier — validation.

Runs the trained تونس-word detector on plate crops:
    تونس detected  -> 'tounes' (civilian)
    not detected   -> 'other'

Validates against the real data we have:
  - civilian crops  -> should detect تونس  (high tounes rate = good)
  - non-civilian refs -> should NOT detect it (-> other), except tounes.jpg

Run from plate_recognition/ (venv active):
    python word_classify.py
    python word_classify.py --conf 0.35 --model models/tounes_word_detector.pt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

SCRIPT_DIR = Path(__file__).resolve().parent
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# non-civilian references -> 'other'; tounes.jpg is civilian -> 'tounes'
TRUTH = {"haras": "other", "images": "other", "militaire": "other",
         "ministere_vh": "other", "nounta": "other", "silkdb1": "other",
         "tounes": "tounes"}   # pred label is 'tounes' (= civilian)


def main():
    ap = argparse.ArgumentParser(description="Validate the تونس word-spotting classifier")
    ap.add_argument("--model", type=Path,
                    default=SCRIPT_DIR / "models" / "tounes_word_detector.pt",
                    help="trained تونس-word detector weights")
    ap.add_argument("--civilian", type=Path, default=SCRIPT_DIR / "civilian_crops")
    ap.add_argument("--refs", type=Path, default=SCRIPT_DIR / "tunisian_plate")
    ap.add_argument("--conf", type=float, default=0.5, help="detection confidence threshold")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--save-misses", action="store_true",
                    help="copy civilian crops where تونس was NOT found into misses/ for inspection")
    args = ap.parse_args()

    if not args.model.exists():
        # fall back to the raw run output if the copied weights aren't there
        alt = SCRIPT_DIR / "runs_word" / "tounes_word" / "weights" / "best.pt"
        if alt.exists():
            args.model = alt
        else:
            sys.exit(f"ERROR: detector not found at {args.model} (train it first).")

    from ultralytics import YOLO
    model = YOLO(str(args.model))
    print(f"Detector: {args.model}  |  conf>={args.conf}\n")

    def detect(img_path):
        im = cv2.imread(str(img_path))
        if im is None:
            return False, 0.0
        # STRETCH to 512x512 — matches the Roboflow training preprocessing
        # ("Resize 512x512 Stretch"). Critical: letterboxed input drops recall ~43%,
        # stretched input gives ~96%.
        im = cv2.resize(im, (512, 512))
        r = model.predict(im, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
        n = len(r.boxes)
        return n > 0, (float(r.boxes.conf.max()) if n else 0.0)

    # --- civilian crops ---
    civ = sorted(p for p in args.civilian.iterdir() if p.suffix.lower() in IMG_EXTS)
    if not civ:
        sys.exit(f"ERROR: no civilian crops in {args.civilian}")
    misses = []
    hits = 0
    for p in civ:
        found, _ = detect(p)
        if found:
            hits += 1
        else:
            misses.append(p)
    print(f"CIVILIAN crops: {len(civ)}")
    print(f"  تونس detected  -> tounes : {hits}  ({100*hits/len(civ):.1f}%)   [want high]")
    print(f"  not detected   -> other  : {len(misses)}  (false 'other' = misses)")

    if args.save_misses and misses:
        md = SCRIPT_DIR / "misses"
        md.mkdir(exist_ok=True)
        import shutil
        for p in misses:
            shutil.copy2(p, md / p.name)
        print(f"  (copied {len(misses)} missed civilian crops -> {md})")

    # --- references ---
    print("\nREFERENCE plates:")
    refs = sorted(p for p in args.refs.iterdir() if p.suffix.lower() in IMG_EXTS)
    correct = 0
    for p in refs:
        found, c = detect(p)
        pred = "tounes" if found else "other"
        truth = TRUTH.get(p.stem, "?")
        ok = "OK " if pred == truth else "XX "
        correct += pred == truth
        print(f"  {ok} {p.stem:16s} truth={truth:9s} pred={pred:7s}  (word conf {c:.2f})")
    print(f"\nReference accuracy: {correct}/{len(refs)}")
    print(f"\nRule: تونس detected (conf>={args.conf}) -> tounes ; else -> other")
    print("Tune --conf: higher = fewer false 'tounes' on others, but more civilian misses.")


if __name__ == "__main__":
    main()
