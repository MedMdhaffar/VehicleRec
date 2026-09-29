"""
Embedding-based plate-category classification (the "clustering / KNN" idea).

Hypothesis: a strong PRETRAINED encoder (CLIP) gives general features that may
transfer synthetic->real better than a nano classifier trained from scratch on
synthetic. We build one reference centroid per category from synthetic_data/
(the "clusters"), then classify real plates by cosine similarity (nearest
centroid + KNN vote).

Run from plate_recognition/ (venv active):
    python cluster_classify.py --refs synthetic_data --test tunisian_plate [--per-class 200] [--k 15]

Prints, per real image: nearest-centroid prediction + KNN prediction + similarity,
and overall accuracy against the filename-derived ground truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# real test filenames -> true category (for scoring)
TRUTH = {
    "haras": "national_guard", "images": "am", "militaire": "military",
    "ministere_vh": "ministry", "nounta": "nt", "silkdb1": "diplomatic",
    "tounes": "civilian",
}


def load_clip():
    from transformers import CLIPModel, CLIPProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    name = "openai/clip-vit-base-patch32"
    model = CLIPModel.from_pretrained(name).to(dev).eval()
    proc = CLIPProcessor.from_pretrained(name)
    print(f"CLIP loaded ({name}) on {dev}")
    return model, proc, dev


@torch.no_grad()
def embed(paths, model, proc, dev, bs=64):
    out = []
    for i in range(0, len(paths), bs):
        imgs = [Image.open(p).convert("RGB") for p in paths[i:i + bs]]
        inp = proc(images=imgs, return_tensors="pt").to(dev)
        f = model.get_image_features(**inp)
        f = torch.nn.functional.normalize(f, dim=-1)
        out.append(f.cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 512))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs", type=Path, default=SCRIPT_DIR / "synthetic_data")
    ap.add_argument("--test", type=Path, default=SCRIPT_DIR / "tunisian_plate")
    ap.add_argument("--per-class", type=int, default=200, help="ref images per class to embed")
    ap.add_argument("--k", type=int, default=15, help="KNN neighbours")
    args = ap.parse_args()

    model, proc, dev = load_clip()

    # --- build reference embeddings per class ---
    cats = sorted(d.name for d in args.refs.iterdir() if d.is_dir())
    ref_emb, ref_lab, centroids = [], [], {}
    print("\nEmbedding references:")
    for c in cats:
        imgs = sorted(p for p in (args.refs / c).glob("*.jpg"))[:args.per_class]
        e = embed(imgs, model, proc, dev)
        ref_emb.append(e); ref_lab += [c] * len(e)
        cen = e.mean(0); centroids[c] = cen / (np.linalg.norm(cen) + 1e-9)
        print(f"  {c:15s} {len(e)} imgs")
    ref_emb = np.concatenate(ref_emb)
    ref_lab = np.array(ref_lab)
    cen_mat = np.stack([centroids[c] for c in cats])  # [C, D]

    # --- classify real test images ---
    test_imgs = sorted(p for p in args.test.iterdir() if p.suffix.lower() in IMG_EXTS)
    print(f"\n{'image':16s} {'TRUE':15s} {'centroid':15s} {'KNN':15s}  sim")
    print("-" * 72)
    n_cen = n_knn = n = 0
    for p in test_imgs:
        q = embed([p], model, proc, dev)[0]
        # nearest centroid
        sims = cen_mat @ q
        cen_pred = cats[int(sims.argmax())]
        # KNN vote
        d = ref_emb @ q
        idx = np.argsort(-d)[:args.k]
        knn_pred = Counter(ref_lab[idx]).most_common(1)[0][0]

        true = TRUTH.get(p.stem, "?")
        ok_c = "✅" if cen_pred == true else "❌"
        ok_k = "✅" if knn_pred == true else "❌"
        print(f"{p.stem:16s} {true:15s} {cen_pred:15s}{ok_c} {knn_pred:15s}{ok_k}  {sims.max():.2f}")
        if true != "?":
            n += 1
            n_cen += cen_pred == true
            n_knn += knn_pred == true
    if n:
        print("-" * 72)
        print(f"accuracy  centroid: {n_cen}/{n} ({100*n_cen/n:.0f}%)   "
              f"KNN: {n_knn}/{n} ({100*n_knn/n:.0f}%)")
    print("\n(vs yolo-cls baseline: 2/7)")


if __name__ == "__main__":
    main()
