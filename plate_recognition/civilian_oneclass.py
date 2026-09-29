"""
One-class 'tounes' (civilian) scorer via CLIP similarity.

Builds a civilian reference set from real crops; scores any plate by its cosine
similarity to the civilian set (1-NN = max sim to any civilian crop). High -> tounes,
low -> other. No synthetic, no training -> no real-vs-synthetic shortcut.

Validates: civilian self-similarity (leave-one-out) vs the non-civilian references,
and suggests a threshold that separates them.

Run from plate_recognition/ (venv active):
    python civilian_oneclass.py --civilian civilian_crops --refs tunisian_plate
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
# non-civilian references (tounes.jpg is civilian -> should score HIGH)
TRUTH = {"haras": "other", "images": "other", "militaire": "other",
         "ministere_vh": "other", "nounta": "other", "silkdb1": "other",
         "tounes": "civilian"}


def load_clip():
    from transformers import CLIPModel, CLIPProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    name = "openai/clip-vit-base-patch32"
    return CLIPModel.from_pretrained(name).to(dev).eval(), CLIPProcessor.from_pretrained(name), dev


@torch.no_grad()
def embed(paths, model, proc, dev, bs=64):
    out = []
    for i in range(0, len(paths), bs):
        imgs = [Image.open(p).convert("RGB") for p in paths[i:i + bs]]
        inp = proc(images=imgs, return_tensors="pt").to(dev)
        f = torch.nn.functional.normalize(model.get_image_features(**inp), dim=-1)
        out.append(f.cpu().numpy())
    return np.concatenate(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--civilian", type=Path, default=SCRIPT_DIR / "civilian_crops")
    ap.add_argument("--refs", type=Path, default=SCRIPT_DIR / "tunisian_plate")
    args = ap.parse_args()

    model, proc, dev = load_clip()
    civ_paths = sorted(p for p in args.civilian.iterdir() if p.suffix.lower() in IMG_EXTS)
    print(f"Civilian reference crops: {len(civ_paths)}")
    C = embed(civ_paths, model, proc, dev)                       # [N, D]

    # civilian leave-one-out 1-NN similarity (exclude self-match)
    S = C @ C.T
    np.fill_diagonal(S, -1.0)
    civ_loo = S.max(axis=1)
    pct = lambda q: float(np.percentile(civ_loo, q))
    print("\nCivilian self-similarity (leave-one-out 1-NN):")
    print(f"  min {civ_loo.min():.3f} | p5 {pct(5):.3f} | p10 {pct(10):.3f} | "
          f"median {pct(50):.3f} | mean {civ_loo.mean():.3f}")

    # non-civilian references: max sim to civilian set
    ref_paths = sorted(p for p in args.refs.iterdir() if p.suffix.lower() in IMG_EXTS)
    print("\nReference plates — max similarity to civilian set:")
    rows = []
    for p in ref_paths:
        q = embed([p], model, proc, dev)[0]
        sim = float((C @ q).max())
        rows.append((p.stem, TRUTH.get(p.stem, "?"), sim))
    for stem, truth, sim in sorted(rows, key=lambda r: -r[2]):
        print(f"  {stem:16s} truth={truth:9s} sim={sim:.3f}")

    # suggest threshold: p5 of civilian (keeps ~95% civilian), show how 'other' refs split
    thr = pct(5)
    other_sims = [s for _, t, s in rows if t == "other"]
    below = sum(s < thr for s in other_sims)
    print(f"\nSuggested threshold (civilian p5) = {thr:.3f}")
    print(f"  non-civilian refs correctly < threshold (-> 'other'): {below}/{len(other_sims)}")
    print("  (civilian crops above threshold by construction ~95%)")
    print("\nRule: sim_to_civilian >= threshold -> tounes ; else -> other")


if __name__ == "__main__":
    main()
