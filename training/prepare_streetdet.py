"""
Validate the streetdet dataset and write a corrected training YAML.

Run from vehicle_services/:
    python training/prepare_streetdet.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths (anchored to this file's location → vehicle_services/training/)
# ---------------------------------------------------------------------------
TRAINING_DIR   = Path(__file__).resolve().parent
VS_DIR         = TRAINING_DIR.parent          # vehicle_services/
DATASET_DIR    = VS_DIR / "vehicle_class1"
CONFIGS_DIR    = TRAINING_DIR / "configs"
OUT_YAML       = CONFIGS_DIR / "streetdet_train.yaml"

SPLITS = {
    "train": DATASET_DIR / "train",
    "valid": DATASET_DIR / "valid",
}

NC = 9
CLASS_NAMES = ["bus", "camper", "car", "cyclist", "motorcycle",
               "person", "tractor", "truck", "van"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_image(label_path: Path, images_dir: Path) -> Path | None:
    stem = label_path.stem
    for ext in IMAGE_EXTS:
        candidate = images_dir / (stem + ext)
        if candidate.exists():
            return candidate
    return None


def check_split(split_name: str, split_dir: Path) -> tuple[dict[int, int], list[str], int]:
    """Return (class_counts, errors, n_empty_negatives)."""
    labels_dir = split_dir / "labels"
    images_dir = split_dir / "images"
    errors: list[str] = []
    counts: dict[int, int] = defaultdict(int)
    n_empty = 0

    if not labels_dir.exists():
        errors.append(f"[{split_name}] labels dir missing: {labels_dir}")
        return counts, errors, n_empty
    if not images_dir.exists():
        errors.append(f"[{split_name}] images dir missing: {images_dir}")
        return counts, errors, n_empty

    label_files = sorted(labels_dir.glob("*.txt"))
    if not label_files:
        errors.append(f"[{split_name}] no label files found in {labels_dir}")
        return counts, errors, n_empty

    for lf in label_files:
        # image parity
        if find_image(lf, images_dir) is None:
            errors.append(f"[{split_name}] no matching image for {lf.name}")

        lines = lf.read_text().splitlines()
        if not lines:
            # Empty label files are valid negative/background examples in Roboflow datasets.
            # Ultralytics handles them correctly — count but do not treat as errors.
            n_empty += 1
            continue

        for lineno, line in enumerate(lines, 1):
            parts = line.strip().split()
            if len(parts) != 5:
                errors.append(f"[{split_name}] {lf.name}:{lineno} — expected 5 fields, got {len(parts)}")
                continue

            cls_id = int(parts[0])
            if cls_id < 0 or cls_id >= NC:
                errors.append(f"[{split_name}] {lf.name}:{lineno} — class id {cls_id} out of [0,{NC-1}]")
            else:
                counts[cls_id] += 1

            try:
                coords = [float(x) for x in parts[1:]]
            except ValueError:
                errors.append(f"[{split_name}] {lf.name}:{lineno} — non-numeric bbox")
                continue

            for val in coords:
                if not (0.0 < val <= 1.0):
                    errors.append(
                        f"[{split_name}] {lf.name}:{lineno} — bbox value {val:.6f} outside (0,1]"
                    )
                    break

    return counts, errors, n_empty


def print_distribution(train_counts: dict[int, int], valid_counts: dict[int, int]) -> None:
    header = f"{'Class':<14} {'ID':>3}  {'Train':>8}  {'Valid':>8}"
    print("\n" + header)
    print("-" * len(header))
    for cid, name in enumerate(CLASS_NAMES):
        t = train_counts.get(cid, 0)
        v = valid_counts.get(cid, 0)
        print(f"{name:<14} {cid:>3}  {t:>8,}  {v:>8,}")
    print(f"{'TOTAL':<14} {'':>3}  {sum(train_counts.values()):>8,}  {sum(valid_counts.values()):>8,}")
    print()


def write_yaml() -> None:
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "path": str(DATASET_DIR),
        "train": "train/images",
        "val":   "valid/images",
        "nc":    NC,
        "names": CLASS_NAMES,
    }
    with OUT_YAML.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    print(f"Written: {OUT_YAML}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    all_errors: list[str] = []

    train_counts, train_errors, train_empty = check_split("train", SPLITS["train"])
    valid_counts, valid_errors, valid_empty = check_split("valid", SPLITS["valid"])
    all_errors.extend(train_errors + valid_errors)

    print_distribution(train_counts, valid_counts)
    print(f"  Negative frames (empty labels): train={train_empty:,}  valid={valid_empty:,}")
    print(f"  (These are valid background examples — not errors.)\n")

    if all_errors:
        print(f"SANITY CHECK FAILED — {len(all_errors)} error(s):")
        for e in all_errors:
            print(f"  {e}")
        return 1

    print("Sanity check passed.")
    write_yaml()
    return 0


if __name__ == "__main__":
    sys.exit(main())
