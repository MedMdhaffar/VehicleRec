"""
Synthetic Tunisian license-plate generator (for yolo26n-cls category classifier).

Renders plates per CATEGORY from templates derived from real reference plates,
with random digits and the correct Arabic word / colours / decor, then optional
CCTV-style augmentation. Output is one folder per category — exactly the layout
ultralytics classification training expects.

Why rendering (not a diffusion model): the plate CONTENT must be exact and the
Arabic must be correct — diffusion garbles both. Here digits are known and Arabic
is rendered from real strings + a real font.

Setup:
    pip install pillow arabic-reshaper python-bidi numpy opencv-python
    # Needs an Arabic-capable font. On WSL, Windows Arial/Tahoma work out of the box;
    # otherwise pass --arabic-font /path/to/font.ttf (Noto Sans Arabic, Amiri, ...).

Usage:
    python synthetic_generator.py                       # 100/category, clean
    python synthetic_generator.py --count 500 --augment # 500/category, CCTV-augmented
    python synthetic_generator.py --count 20            # quick look

Output: ./synthetic_data/<category>/*.jpg
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _ARABIC_OK = True
except ImportError:
    _ARABIC_OK = False

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Fonts (auto-detect; WSL Windows fonts are a reliable Arabic source)
# ---------------------------------------------------------------------------
ARABIC_FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/tahoma.ttf",
    "/mnt/c/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
]
DIGIT_FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/mnt/c/Windows/Fonts/arial.ttf",
]


def find_font(candidates, override=None):
    if override:
        return override
    for c in candidates:
        if Path(c).exists():
            return c
    return None


# ---------------------------------------------------------------------------
# Category templates (derived from the 7 reference plates)
#   colours are RGB; segments are laid left-to-right in VISUAL order
#   seg kinds: digits(n=(min,max)) | arabic(text) | latin(text|gen) | dash
#   decor: flag(side) | emblem(side); subtitle: small latin under the row
# ---------------------------------------------------------------------------
def digits(a, b):
    return {"kind": "digits", "n": (a, b)}

CATEGORIES = {
    "civilian": {        # 139 تونس 7605  — black/white
        "size": (520, 115), "bg": (15, 15, 15), "fg": (240, 240, 240),
        "segments": [digits(2, 3), {"kind": "arabic", "text": "تونس"}, digits(3, 4)],
    },
    "national_guard": {  # 15573 الحرس الوطني  — white/black + flag right
        "size": (600, 115), "bg": (245, 245, 245), "fg": (20, 20, 20),
        "segments": [digits(4, 5), {"kind": "arabic", "text": "الحرس الوطني"}],
        "decor": {"kind": "flag", "side": "right"},
    },
    "military": {        # [emblem] 21551  + TUNISIA  — black/white
        "size": (600, 140), "bg": (15, 15, 15), "fg": (240, 240, 240),
        "segments": [digits(4, 5)],
        "decor": {"kind": "emblem", "side": "left"},
        "subtitle": "TUNISIA",
    },
    "ministry": {        # 20 - 130486  — white bg, RED text
        "size": (520, 115), "bg": (245, 245, 245), "fg": (200, 20, 20),
        "segments": [digits(2, 2), {"kind": "dash"}, digits(5, 6)],
    },
    "nt": {              # 103168 ن ت  — black/white
        "size": (520, 115), "bg": (15, 15, 15), "fg": (240, 240, 240),
        "segments": [digits(5, 6), {"kind": "arabic", "text": "ن ت"}],
    },
    "diplomatic": {      # 03 س د 29CD  — black/white, mixed Arabic+Latin
        "size": (560, 115), "bg": (15, 15, 15), "fg": (240, 240, 240),
        "segments": [digits(2, 2), {"kind": "arabic", "text": "س د"},
                     {"kind": "latin", "gen": "cd"}],
    },
    "am": {              # ع م 78752  — black/white
        "size": (520, 115), "bg": (15, 15, 15), "fg": (240, 240, 240),
        "segments": [{"kind": "arabic", "text": "ع م"}, digits(5, 5)],
    },
}


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------
def shape_arabic(text):
    # PIL renders reshaped Arabic presentation forms in CORRECT visual order already;
    # applying get_display()/bidi on top REVERSES the word (verified wrong). Reshape only.
    if _ARABIC_OK:
        return arabic_reshaper.reshape(text)
    return text  # unshaped fallback — install arabic-reshaper for connected letters


def render_text(text, font, color):
    """Render text to a tight RGBA image."""
    tmp = Image.new("RGBA", (10, 10))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.new("RGBA", (max(1, w + 6), max(1, h + 6)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((3 - bbox[0], 3 - bbox[1]), text, font=font, fill=color + (255,))
    return img


def make_segment_img(seg, fg, fonts, rng):
    kind = seg["kind"]
    if kind == "digits":
        n = rng.randint(*seg["n"])
        return render_text("".join(str(rng.randint(0, 9)) for _ in range(n)), fonts["digit"], fg)
    if kind == "dash":
        return render_text("-", fonts["digit"], fg)
    if kind == "latin":
        txt = f"{rng.randint(10,99)}CD" if seg.get("gen") == "cd" else seg["text"]
        return render_text(txt, fonts["digit"], fg)
    if kind == "arabic":
        return render_text(shape_arabic(seg["text"]), fonts["arabic"], fg)
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# Decor
# ---------------------------------------------------------------------------
def make_flag(h):
    """Approx Tunisian flag patch: red field, white disc, red crescent+star hint."""
    s = int(h * 0.85)
    img = Image.new("RGBA", (s, s), (206, 17, 38, 255))
    d = ImageDraw.Draw(img)
    cx, cy, r = s // 2, s // 2, int(s * 0.30)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    d.ellipse([cx - r + r // 2, cy - r, cx + r + r // 2, cy + r], fill=(206, 17, 38, 255))
    return img


def make_emblem(h):
    """Approx military emblem: red disc + white crescent hint."""
    s = int(h * 0.85)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = s // 2, s // 2, int(s * 0.45)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(206, 17, 38, 255))
    d.ellipse([cx - r // 2, cy - r, cx + r + r // 4, cy + r], fill=(255, 255, 255, 255))
    d.ellipse([cx - r // 4, cy - r, cx + r, cy + r], fill=(206, 17, 38, 255))
    return img


# ---------------------------------------------------------------------------
# Plate assembly
# ---------------------------------------------------------------------------
def render_plate(cat, spec, fonts, rng):
    W, H = spec["size"]
    plate = Image.new("RGB", (W, H), spec["bg"])
    ImageDraw.Draw(plate).rectangle([2, 2, W - 3, H - 3],
                                    outline=spec["fg"], width=max(1, H // 40))

    decor = spec.get("decor")
    side_w = 0
    decor_img = None
    if decor:
        decor_img = make_flag(H) if decor["kind"] == "flag" else make_emblem(H)
        side_w = decor_img.width + 14

    # text band height (leave room for subtitle if any)
    band_h = int(H * (0.62 if spec.get("subtitle") else 0.74))
    seg_imgs = []
    for seg in spec["segments"]:
        im = make_segment_img(seg, spec["fg"], fonts, rng)
        scale = band_h / im.height
        im = im.resize((max(1, int(im.width * scale)), band_h), Image.LANCZOS)
        seg_imgs.append(im)

    gap = int(W * 0.03)
    content_w = sum(i.width for i in seg_imgs) + gap * (len(seg_imgs) - 1)
    avail_w = W - side_w - int(W * 0.06)
    if content_w > avail_w:  # shrink to fit
        f = avail_w / content_w
        seg_imgs = [i.resize((max(1, int(i.width * f)), max(1, int(i.height * f))), Image.LANCZOS)
                    for i in seg_imgs]
        content_w = sum(i.width for i in seg_imgs) + gap * (len(seg_imgs) - 1)

    # left offset (account for left-side decor)
    left_pad = side_w if (decor and decor["side"] == "left") else 0
    x = left_pad + (W - side_w - content_w) // 2
    y_row = int(H * 0.06)
    for im in seg_imgs:
        plate.paste(im, (x, y_row + (band_h - im.height) // 2), im)
        x += im.width + gap

    if decor_img:
        dy = (H - decor_img.height) // 2
        dx = 8 if decor["side"] == "left" else W - decor_img.width - 8
        plate.paste(decor_img, (dx, dy), decor_img)

    if spec.get("subtitle"):
        sub = render_text(spec["subtitle"], fonts["digit"], spec["fg"])
        sh = int(H * 0.16)
        sub = sub.resize((int(sub.width * sh / sub.height), sh), Image.LANCZOS)
        plate.paste(sub, ((W - sub.width) // 2, int(H * 0.80)), sub)

    return plate


# ---------------------------------------------------------------------------
# Augmentation (CCTV realism) — applied with cv2 if available
# ---------------------------------------------------------------------------
def augment(pil_img, rng):
    if not _CV2_OK:
        return pil_img
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    # perspective
    m = 0.06
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[rng.uniform(0, m) * w, rng.uniform(0, m) * h],
                            [-rng.uniform(0, m) * w, rng.uniform(0, m) * h],
                            [-rng.uniform(0, m) * w, -rng.uniform(0, m) * h],
                            [rng.uniform(0, m) * w, -rng.uniform(0, m) * h]])
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h),
                              borderValue=(127, 127, 127))
    # brightness / contrast
    img = cv2.convertScaleAbs(img, alpha=rng.uniform(0.6, 1.3), beta=rng.uniform(-30, 30))
    # downscale-upscale (low-res CCTV)
    f = rng.uniform(0.35, 0.9)
    img = cv2.resize(cv2.resize(img, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA),
                     (w, h), interpolation=cv2.INTER_LINEAR)
    # blur
    k = rng.choice([1, 3, 3, 5])
    if k > 1:
        img = cv2.GaussianBlur(img, (k, k), 0)
    # noise
    img = np.clip(img.astype(np.int16) + rng.normal(0, rng.uniform(2, 12), img.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Synthetic Tunisian plate generator")
    ap.add_argument("--count", type=int, default=100, help="images per category (default 100)")
    ap.add_argument("--augment", action="store_true", help="apply CCTV-style augmentation")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "synthetic_data", help="output dir")
    ap.add_argument("--arabic-font", default=None, help="path to an Arabic-capable .ttf")
    ap.add_argument("--digit-font", default=None, help="path to a bold Latin .ttf for digits")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not _ARABIC_OK:
        print("WARN: arabic-reshaper / python-bidi not installed — Arabic will render "
              "disconnected/incorrect. Run: pip install arabic-reshaper python-bidi")
    arabic_path = find_font(ARABIC_FONT_CANDIDATES, args.arabic_font)
    digit_path = find_font(DIGIT_FONT_CANDIDATES, args.digit_font)
    if not arabic_path:
        sys.exit("ERROR: no Arabic-capable font found. Pass --arabic-font /path/to/font.ttf")
    if not digit_path:
        sys.exit("ERROR: no Latin font found. Pass --digit-font /path/to/font.ttf")
    print(f"Arabic font: {arabic_path}\nDigit font : {digit_path}")

    fonts = {"arabic": ImageFont.truetype(arabic_path, 80),
             "digit":  ImageFont.truetype(digit_path, 80)}
    rng = random.Random(args.seed)
    nprng = np.random.default_rng(args.seed)

    total = 0
    for cat, spec in CATEGORIES.items():
        out_dir = args.out / cat
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(args.count):
            plate = render_plate(cat, spec, fonts, rng)
            if args.augment:
                # use a numpy-backed rng wrapper for augment's .uniform/.normal/.choice
                plate = augment(plate, nprng)
            plate.save(out_dir / f"{cat}_{i:05d}.jpg", quality=92)
        total += args.count
        print(f"  {cat:15s} → {args.count} images")

    print(f"\nDone: {total} images across {len(CATEGORIES)} categories → {args.out}")
    print("Inspect a few per folder to judge template fidelity, then we iterate.")


if __name__ == "__main__":
    main()
