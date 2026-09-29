"""
Convert KICT PASCAL VOC dataset → YOLO format for cross-domain evaluation.

Source: /mnt/c/Users/malek/Desktop/vehicle_cllass  (WSL path to Windows desktop)
Fallback: training/datasets/kict/raw/

KICT class remap (only bus/truck scoreable against our model):
  KICT "02"         → bus   (model class index 0)
  KICT "03"–"07"   → truck (model class index 7)
  KICT "01", "08"–"12" → dropped

Run from vehicle_services/:
    python training/voc_to_yolo_kict.py [--src <path>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TRAINING_DIR = Path(__file__).resolve().parent
VS_DIR       = TRAINING_DIR.parent

WSL_DEFAULT  = Path("/mnt/c/Users/malek/Desktop/vehicle_cllass")
LOCAL_RAW    = TRAINING_DIR / "datasets" / "kict" / "raw"

OUT_LABELS   = TRAINING_DIR / "datasets" / "kict" / "labels"
OUT_IMAGES   = TRAINING_DIR / "datasets" / "kict" / "images"
OUT_YAML     = TRAINING_DIR / "configs" / "kict_eval.yaml"

# Model class indices (must match streetdet training)
NC = 9
CLASS_NAMES = ["bus", "camper", "car", "cyclist", "motorcycle",
               "person", "tractor", "truck", "van"]

# KICT VOC name → model class index  (None = drop)
KICT_REMAP: dict[str, int | None] = {
    "01": None,   # car/van conflated — unscoreable
    "02": 0,      # bus
    "03": 7,      # truck subtypes
    "04": 7,
    "05": 7,
    "06": 7,
    "07": 7,
    "08": None,
    "09": None,
    "10": None,
    "11": None,
    "12": None,
}

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_source(override: str | None) -> Path:
    if override:
        p = Path(override)
        if not p.exists():
            sys.exit(f"ERROR: specified source path does not exist: {p}")
        return p
    if WSL_DEFAULT.exists():
        print(f"Using WSL path: {WSL_DEFAULT}")
        return WSL_DEFAULT
    if LOCAL_RAW.exists() and any(LOCAL_RAW.iterdir()):
        print(f"Using local raw fallback: {LOCAL_RAW}")
        return LOCAL_RAW
    sys.exit(
        f"ERROR: KICT source not found.\n"
        f"  Expected: {WSL_DEFAULT}\n"
        f"  Fallback: {LOCAL_RAW} (empty or missing)\n"
        f"Copy the KICT dataset to one of these paths, or pass --src."
    )


def find_image(xml_path: Path, src_root: Path, filename: str) -> Path | None:
    # Try alongside the XML, then common image subdirs
    candidates = [
        xml_path.parent.parent / "images" / filename,
        xml_path.parent / filename,
        src_root / "images" / filename,
        src_root / filename,
    ]
    stem = Path(filename).stem
    for base in candidates[:1]:
        for ext in IMAGE_EXTS:
            p = base.parent / (stem + ext)
            if p.exists():
                return p
    for base in candidates:
        if base.exists():
            return base
    return None


def parse_voc_xml(xml_file: Path) -> tuple[str, int, int, list[dict]]:
    """Return (filename, width, height, objects)."""
    tree = ET.parse(xml_file)
    root = tree.getroot()
    filename = root.findtext("filename") or xml_file.stem + ".jpg"
    size = root.find("size")
    w = int(size.findtext("width"))
    h = int(size.findtext("height"))
    objects = []
    for obj in root.findall("object"):
        name = obj.findtext("name", "").strip()
        bb = obj.find("bndbox")
        xmin = float(bb.findtext("xmin"))
        ymin = float(bb.findtext("ymin"))
        xmax = float(bb.findtext("xmax"))
        ymax = float(bb.findtext("ymax"))
        objects.append({"name": name, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})
    return filename, w, h, objects


def to_yolo(xmin: float, ymin: float, xmax: float, ymax: float,
            img_w: int, img_h: int) -> tuple[float, float, float, float]:
    cx = ((xmin + xmax) / 2) / img_w
    cy = ((ymin + ymax) / 2) / img_h
    bw = (xmax - xmin) / img_w
    bh = (ymax - ymin) / img_h
    return cx, cy, bw, bh


def write_kict_yaml() -> None:
    data = {
        "path": str(TRAINING_DIR / "datasets" / "kict"),
        "val":  "images",
        "nc":   NC,
        "names": CLASS_NAMES,
    }
    OUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    with OUT_YAML.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    print(f"Written: {OUT_YAML}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Convert KICT VOC → YOLO")
    ap.add_argument("--src", default=None, help="Override source root path")
    args = ap.parse_args()

    src_root = find_source(args.src)

    # Locate XMLs — KICT typically stores them under train/xml/ or Annotations/
    xml_candidates = [
        src_root / "train" / "xml",
        src_root / "Annotations",
        src_root,
    ]
    xml_dir = next((d for d in xml_candidates if d.is_dir()), None)
    if xml_dir is None:
        sys.exit(f"ERROR: could not find XML directory under {src_root}")

    xml_files = sorted(xml_dir.glob("*.xml"))
    if not xml_files:
        sys.exit(f"ERROR: no *.xml files found in {xml_dir}")

    OUT_LABELS.mkdir(parents=True, exist_ok=True)
    OUT_IMAGES.mkdir(parents=True, exist_ok=True)

    n_xml = 0
    n_labels_written = 0
    n_dropped = 0
    n_images_copied = 0
    class_label_counts: dict[int, int] = {}

    for xf in xml_files:
        try:
            filename, img_w, img_h, objects = parse_voc_xml(xf)
        except Exception as e:
            print(f"  WARN: could not parse {xf.name}: {e}")
            continue
        n_xml += 1

        yolo_lines: list[str] = []
        for obj in objects:
            raw_name = obj["name"].strip()
            cls_idx = KICT_REMAP.get(raw_name)
            if cls_idx is None:
                n_dropped += 1
                continue
            cx, cy, bw, bh = to_yolo(obj["xmin"], obj["ymin"], obj["xmax"], obj["ymax"], img_w, img_h)
            yolo_lines.append(f"{cls_idx} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            class_label_counts[cls_idx] = class_label_counts.get(cls_idx, 0) + 1
            n_labels_written += 1

        if not yolo_lines:
            continue  # all objects dropped — skip this image

        label_out = OUT_LABELS / (xf.stem + ".txt")
        label_out.write_text("\n".join(yolo_lines) + "\n")

        img_src = find_image(xf, src_root, filename)
        if img_src:
            dst = OUT_IMAGES / img_src.name
            if not dst.exists():
                shutil.copy2(img_src, dst)
            n_images_copied += 1
        else:
            print(f"  WARN: image not found for {xf.name} (label written anyway)")

    write_kict_yaml()

    print(f"\nKICT conversion summary:")
    print(f"  XMLs processed : {n_xml}")
    print(f"  Labels written : {n_labels_written}")
    print(f"    bus  (idx 0) : {class_label_counts.get(0, 0)}")
    print(f"    truck(idx 7) : {class_label_counts.get(7, 0)}")
    print(f"  Dropped objs   : {n_dropped}  (class 01 + 08-12)")
    print(f"  Images copied  : {n_images_copied}")
    print(f"  Output labels  : {OUT_LABELS}")
    print(f"  Output images  : {OUT_IMAGES}")


if __name__ == "__main__":
    main()
