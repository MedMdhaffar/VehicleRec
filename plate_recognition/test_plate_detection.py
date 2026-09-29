"""
Standalone ANPR test: plate DETECTION (finetuned YOLO) + plate READING (PaddleOCR).

Purpose: run the full detect->read pipeline on real CCTV frames and judge whether
PP-OCRv6 reads our (Tunisian) plates well enough to SKIP a fine-tune. Throwaway
test tool, NOT the production service.

Pipeline per frame:
    YOLO plate detector  ->  plate crop  ->  PaddleOCR (PP-OCRv6 det+rec)  ->  text

Setup:
    # detector weights
    cp .../best_matricule.pt  vehicle_services/plate_recognition/models/plate_yolo.pt
    # OCR (PP-OCRv6 det+rec, downloaded on first run):
    pip install paddleocr
    # NOTE: PaddleOCR API differs across versions. If init/parse fails, adjust
    # PlateReader.__init__ / .read() to your installed version (see HF model card:
    # https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_safetensors).

Usage (run from anywhere):
    python test_plate_detection.py --source <image | folder | video>
    python test_plate_detection.py --source ./cctv_frames/ --conf 0.25 --imgsz 1280
    python test_plate_detection.py --source clip.mp4 --every 30 --no-ocr   # detection only

Outputs (under --out, default ./test_outputs/):
    annotated/    frames with plate boxes + confidence + READ TEXT drawn
    plate_crops/  each detected plate cropped out
    results.csv   per-frame: n_plates, confidences, box sizes, plate_texts, ocr_confs
and a printed SUMMARY (detection rate, read rate, confidence stats, mean plate size).

NOTE ON METHOD: runs the detector on FULL frames (hardest case — plates are small).
In production the detector runs on the zoomed vehicle crop (cascade), so real
performance will be >= this.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

import cv2

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VID_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = SCRIPT_DIR / "models" / "plate_yolo.pt"


# ---------------------------------------------------------------------------
# Plate detector (YOLO)
# ---------------------------------------------------------------------------

def load_model(model_path: Path):
    if not model_path.exists():
        sys.exit(f"ERROR: model not found at {model_path}\n"
                 f"Copy best_matricule.pt there, or pass --model <path>.")
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ERROR: ultralytics not installed. Run: pip install ultralytics")
    return YOLO(str(model_path))


def preprocess_plate(crop, invert: bool):
    """Optional preprocessing applied to the plate crop BEFORE OCR.
    Currently: colour inversion (black<->white) for white-on-black plates."""
    if invert and crop is not None and crop.size:
        return cv2.bitwise_not(crop)
    return crop


def detect(model, frame, conf: float, imgsz: int, device):
    """Run plate detection on one BGR frame -> list of (x1, y1, x2, y2, conf)."""
    res = model.predict(frame, conf=conf, imgsz=imgsz, device=device, verbose=False)[0]
    out = []
    for b in res.boxes:
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
        out.append((x1, y1, x2, y2, float(b.conf[0])))
    return out


# ---------------------------------------------------------------------------
# Plate reader (PaddleOCR PP-OCRv6)
# ---------------------------------------------------------------------------

class PlateReader:
    """Thin wrapper around the PaddleOCR pipeline (PP-OCRv6 det + rec).
    read(crop) -> (joined_text, mean_confidence). Returns ('', 0.0) on failure."""

    def __init__(self, rec_model: str = "PP-OCRv6_medium_rec"):
        # Recognition-only via the TRANSFORMERS engine. This bypasses PaddlePaddle's
        # inference runtime (the oneDNN/PIR backend that throws
        # 'ConvertPirAttribute2RuntimeAttribute') and runs through torch.
        # The YOLO crop is already a tight plate = one text line, so rec alone suffices.
        self.rec_model = rec_model
        try:
            from paddleocr import TextRecognition
            self.engine = TextRecognition(model_name=rec_model, engine="transformers")
            print(f"PaddleOCR TextRecognition ({rec_model}, transformers engine) loaded.")
            return
        except Exception as e:
            print(f"  (transformers TextRecognition unavailable: {e}; falling back to pipeline w/o oneDNN)")
        # Fallback: full pipeline with oneDNN/MKL-DNN disabled (the failing path).
        from paddleocr import PaddleOCR
        self.engine = PaddleOCR(
            text_recognition_model_name=rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )
        print(f"PaddleOCR pipeline ({rec_model}, oneDNN disabled) loaded.")

    def _parse(self, out):
        texts, scores = [], []
        for r in out or []:
            d = r if isinstance(r, dict) else getattr(r, "json", None) or {}
            if isinstance(d, dict) and "res" in d and isinstance(d["res"], dict):
                d = d["res"]
            if isinstance(d, dict) and "rec_texts" in d:           # pipeline (3.x)
                texts += list(d.get("rec_texts", []))
                scores += list(d.get("rec_scores", []))
            elif isinstance(d, dict) and ("rec_text" in d or "text" in d):  # TextRecognition (single line)
                texts.append(d.get("rec_text", d.get("text", "")))
                scores.append(float(d.get("rec_score", d.get("score", 0.0)) or 0.0))
            elif isinstance(r, (list, tuple)):                      # legacy 2.x: [[box,(text,score)],...]
                for line in r:
                    try:
                        texts.append(line[1][0]); scores.append(float(line[1][1]))
                    except Exception:
                        pass
        text = " ".join(t.strip() for t in texts if t and t.strip())
        conf = round(statistics.mean(scores), 4) if scores else 0.0
        return text, conf

    def read(self, crop):
        if crop is None or crop.size == 0:
            return "", 0.0
        try:
            if hasattr(self.engine, "predict"):
                return self._parse(self.engine.predict(crop))
            return self._parse(self.engine.ocr(crop))               # legacy
        except Exception as e:
            print(f"  OCR error: {e}")
            return "", 0.0


def load_ocr(enabled: bool, rec_model: str = "PP-OCRv6_medium_rec"):
    if not enabled:
        return None
    try:
        return PlateReader(rec_model)
    except ImportError:
        sys.exit("ERROR: paddleocr not installed. Run: pip install paddleocr  (or use --no-ocr)")
    except Exception as e:
        print(f"WARN: could not init PaddleOCR ({e}); running detection only.")
        return None


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

def annotate(frame, dets):
    img = frame.copy()
    h, w = img.shape[:2]
    thick = max(2, w // 700)
    fs = max(0.5, w / 1600.0)
    for d in dets:
        x1, y1, x2, y2 = d["bbox"]
        label = f"{d['conf']:.2f}"
        if d["text"]:
            label += f" | {d['text']} ({d['ocr_conf']:.2f})"
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), thick)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, thick)
        ty = max(th + 4, y1)
        cv2.rectangle(img, (x1, ty - th - 6), (x1 + tw + 4, ty), (0, 255, 0), -1)
        cv2.putText(img, label, (x1 + 2, ty - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), thick, cv2.LINE_AA)
    return img


# ---------------------------------------------------------------------------
# Per-frame processing
# ---------------------------------------------------------------------------

def process(stem, frame, model, ocr, args, dirs, rows, stats):
    boxes = detect(model, frame, args.conf, args.imgsz, args.device)
    dets = []
    for i, (x1, y1, x2, y2, c) in enumerate(boxes, 1):
        crop = frame[y1:y2, x1:x2]
        proc = preprocess_plate(crop, args.invert)   # what actually goes to OCR
        text, oc = ocr.read(proc) if ocr else ("", 0.0)
        if crop.size:
            cv2.imwrite(str(dirs["crops"] / f"{stem}_p{i}.jpg"), crop)
            if args.invert:
                cv2.imwrite(str(dirs["crops_inv"] / f"{stem}_p{i}.jpg"), proc)
        dets.append({"bbox": (x1, y1, x2, y2), "conf": c, "text": text, "ocr_conf": oc})
        stats["confs"].append(c)
        stats["sizes"].append((x2 - x1, y2 - y1))
        if text:
            stats["reads"] += 1
            stats["ocr_confs"].append(oc)

    cv2.imwrite(str(dirs["annotated"] / f"{stem}.jpg"), annotate(frame, dets))
    rows.append({
        "name": stem,
        "n_plates": len(boxes),
        "confidences": ";".join(f"{d['conf']:.3f}" for d in dets),
        "box_sizes_px": ";".join(f"{x2-x1}x{y2-y1}" for x1, y1, x2, y2, _ in boxes),
        "plate_texts": " || ".join(d["text"] for d in dets if d["text"]),
        "ocr_confs": ";".join(f"{d['ocr_conf']:.3f}" for d in dets if d["text"]),
    })
    stats["frames"] += 1
    if boxes:
        stats["frames_with_det"] += 1


def main():
    ap = argparse.ArgumentParser(description="ANPR test: plate detection + PaddleOCR reading")
    ap.add_argument("--source", required=True, help="image file, folder of images, or video")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="path to plate YOLO weights")
    ap.add_argument("--conf", type=float, default=0.25,
                    help="detector confidence (default 0.25 — low, to also see weak detections)")
    ap.add_argument("--imgsz", type=int, default=1280,
                    help="detector inference size (default 1280 — higher helps small CCTV plates)")
    ap.add_argument("--every", type=int, default=30, help="for video: sample every N frames (default 30)")
    ap.add_argument("--device", default=None, help="0, cpu, … (default: auto)")
    ap.add_argument("--no-ocr", action="store_true", help="run detection only (skip PaddleOCR)")
    ap.add_argument("--rec-model", default="PP-OCRv6_medium_rec",
                    help="PaddleOCR recognition model (default: Arabic PP-OCRv5 mobile)")
    ap.add_argument("--invert", action="store_true",
                    help="invert plate colours (black<->white) before OCR; also saves the inverted crop")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "test_outputs", help="output dir")
    args = ap.parse_args()

    model = load_model(args.model)
    ocr = load_ocr(enabled=not args.no_ocr, rec_model=args.rec_model)

    dirs = {"annotated": args.out / "annotated", "crops": args.out / "plate_crops",
            "crops_inv": args.out / "plate_crops_inverted"}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    rows = []
    stats = {"frames": 0, "frames_with_det": 0, "confs": [], "sizes": [], "reads": 0, "ocr_confs": []}
    src = Path(args.source)

    if src.is_file() and src.suffix.lower() in VID_EXTS:
        cap = cv2.VideoCapture(str(src))
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % args.every == 0:
                process(f"{src.stem}_f{idx:06d}", frame, model, ocr, args, dirs, rows, stats)
            idx += 1
        cap.release()
    else:
        if src.is_dir():
            images = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXTS)
        elif src.is_file() and src.suffix.lower() in IMG_EXTS:
            images = [src]
        else:
            sys.exit(f"ERROR: --source {src} is not an image, folder, or video.")
        if not images:
            sys.exit(f"ERROR: no images found in {src}")
        for p in images:
            frame = cv2.imread(str(p))
            if frame is None:
                print(f"  skip (unreadable): {p.name}")
                continue
            process(p.stem, frame, model, ocr, args, dirs, rows, stats)

    with open(args.out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "n_plates", "confidences",
                                          "box_sizes_px", "plate_texts", "ocr_confs"])
        w.writeheader()
        w.writerows(rows)

    n = stats["frames"]
    det = stats["frames_with_det"]
    confs = stats["confs"]
    total_plates = len(confs)
    print("\n" + "=" * 56)
    print("ANPR TEST — DETECTION + OCR SUMMARY")
    print("=" * 56)
    print(f"frames/images processed : {n}")
    if n:
        print(f"frames with >=1 plate   : {det}  ({100*det/n:.1f}% detection rate)")
    print(f"total plates detected   : {total_plates}")
    if not args.no_ocr:
        print(f"plates read (non-empty) : {stats['reads']}"
              + (f"  ({100*stats['reads']/total_plates:.1f}% read rate)" if total_plates else ""))
        if stats["ocr_confs"]:
            oc = stats["ocr_confs"]
            print(f"OCR confidence min/mean/max: {min(oc):.2f} / {statistics.mean(oc):.2f} / {max(oc):.2f}")
    if confs:
        print(f"detector conf  min/mean/max: {min(confs):.2f} / {statistics.mean(confs):.2f} / {max(confs):.2f}")
        ws = [w for w, h in stats["sizes"]]
        hs = [h for w, h in stats["sizes"]]
        print(f"plate box size (px)    avg: {statistics.mean(ws):.0f} w x {statistics.mean(hs):.0f} h"
              f"  (min w {min(ws)}, min h {min(hs)})")
    print(f"\noutputs → {args.out}")
    print("  annotated/   boxes + read text drawn on frames")
    print("  plate_crops/ cropped plates (original)")
    if args.invert:
        print("  plate_crops_inverted/ inverted crops fed to OCR")
    print("  results.csv  per-frame detail incl. plate_texts")
    print("\nReminder: full-frame detection (hardest case). Production runs OCR on the")
    print("zoomed vehicle->plate crop, so expect equal-or-better reads.")


if __name__ == "__main__":
    main()
