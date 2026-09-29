from __future__ import annotations
import os
from pathlib import Path as _Path

from common.config import VEHICLE_MODEL_PATH  # noqa: F401 — re-exported for pipeline use

# ── Pipeline tuning ───────────────────────────────────────────────────────────
FPS          = 15
STREAM_TYPE  = 0      # main stream (1080p)
K_CLASSIFY   = 2      # vehicle-type voting window; colour has an independent valid-sample budget below

# Temporal HSV aggregation. Only supported, non-unknown HSV observations count
# toward the bounded sample budget.
MIN_VALID_COLOR_SAMPLES = int(os.environ.get("MIN_VALID_COLOR_SAMPLES", "5"))
MAX_VALID_COLOR_SAMPLES = int(os.environ.get("MAX_VALID_COLOR_SAMPLES", "20"))
EARLY_LOCK_WINNER_SHARE = float(os.environ.get("EARLY_LOCK_WINNER_SHARE", "0.68"))
EARLY_LOCK_MARGIN       = float(os.environ.get("EARLY_LOCK_MARGIN", "0.20"))
FINAL_WINNER_SHARE      = float(os.environ.get("FINAL_WINNER_SHARE", "0.55"))
FINAL_WINNER_MARGIN     = float(os.environ.get("FINAL_WINNER_MARGIN", "0.10"))
SHORT_TRACK_WINNER_SHARE = float(os.environ.get("SHORT_TRACK_WINNER_SHARE", "0.70"))
SHORT_TRACK_WINNER_MARGIN = float(os.environ.get("SHORT_TRACK_WINNER_MARGIN", "0.25"))
# sqrt(area_fraction / saturation_fraction), capped at 1.0. At 10% of the
# frame area and above, additional crop size adds no more voting weight.
COLOR_QUALITY_SATURATION_FRAC = float(os.environ.get("COLOR_QUALITY_SATURATION_FRAC", "0.10"))
COLOR_ROI_MODE       = os.environ.get("COLOR_ROI_MODE", "multi")
COLOR_NEUTRAL_MODE   = os.environ.get("COLOR_NEUTRAL_MODE", "adaptive")
COLOR_NORMALIZATION  = os.environ.get("COLOR_NORMALIZATION", "none")
COLOR_QUALITY_MODE   = os.environ.get("COLOR_QUALITY_MODE", "enhanced")
COLOR_FEATURE_MODE   = os.environ.get("COLOR_FEATURE_MODE", "hsv")
# Tracking: feed ByteTrack LOW-conf detections (≤ track_low_thresh in the yaml) so its
# 2-stage association can hold a vehicle's id through weak frames → stable line counts.
TRACK_CONF     = float(os.environ.get("TRACK_CONF", "0.10"))
TRACKER_CONFIG = os.environ.get(
    "TRACKER_CONFIG",
    str(__import__("pathlib").Path(__file__).parent / "trackers" / "bytetrack_vehicles.yaml"),
)
MIN_PLATE_W  = 60     # px — below this OCR is unreliable
OCR_COOLDOWN = 6      # frames between OCR attempts per track
MAX_OCR      = 4      # hard cap on OCR calls per track
OCR_LOCK_CONF = 0.90  # stop OCR-ing once digits_conf reaches this
WORD_CONF    = 0.50   # plate-type word-detector threshold
LOST_FRAMES  = 15     # frames absent → finalize and remove track

# ── Plate/OCR state machine (don't re-process known/parked cars) ─────────────
# A track is "resolved" once its plate locks OR it exhausts MAX_OCR attempts.
# Resolved tracks get NO plate_detect / OCR — until they move enough to re-arm.
MOVE_THRESH_PX     = int(os.environ.get("MOVE_THRESH_PX",     "8"))   # centroid move < this ⇒ stationary this frame
STATIONARY_N       = int(os.environ.get("STATIONARY_N",       "10"))  # consecutive stationary frames ⇒ parked
STATIONARY_MAX_OCR = int(os.environ.get("STATIONARY_MAX_OCR", "1"))   # parked car: ONE plate attempt then give up (angle won't change)
# (a parked car re-arms automatically when it starts moving again)

# ── VMS connection (same env vars as live_inference) ─────────────────────────
VMS_URL            = os.environ.get("VMS_URL", "")
VMS_DEVICE_IP      = os.environ.get("VMS_DEVICE_IP", "")
VMS_DEVICE_PORT    = int(os.environ.get("VMS_DEVICE_PORT", "37777"))
VMS_USERNAME       = os.environ.get("VMS_USERNAME", "admin")
VMS_PASSWORD       = os.environ.get("VMS_PASSWORD", "")
VMS_CHANNEL_OFFSET = int(os.environ.get("VMS_CHANNEL_OFFSET", "-1"))

# ── Serving (LF-2) ───────────────────────────────────────────────────────────
LF_CHANNEL    = int(os.environ.get("LF_CHANNEL",    "4"))
LF_PORT_BASE  = int(os.environ.get("LF_PORT_BASE",  "5030"))
JPEG_QUALITY  = int(os.environ.get("JPEG_QUALITY",  "80"))
DISPLAY_FPS   = int(os.environ.get("DISPLAY_FPS",   str(FPS)))
DISPLAY_MAX_W = int(os.environ.get("DISPLAY_MAX_W", "0"))
WS_METRICS_HZ = float(os.environ.get("WS_METRICS_HZ", "1.0"))
THUMB_MAX_W   = int(os.environ.get("THUMB_MAX_W",   "0"))   # 0 = no resize

# ── Review-agent ingest dedup (position-anchored, churn-proof) ───────────────
# A parked car gets re-IDed by ByteTrack many times → many duplicate review
# records. We anchor each WRITTEN record by position; re-acquisitions that
# overlap a live anchor (same spot, same type) are dropped. Anchors are refreshed
# every frame by any active box on them, so they survive track-ID churn, and
# expire shortly after the spot is vacated (so a different car later → new record).
DEDUP_ENABLED        = os.environ.get("DEDUP_ENABLED", "true").lower() == "true"
DEDUP_IOU            = float(os.environ.get("DEDUP_IOU",            "0.6"))
ANCHOR_EXPIRE_FRAMES = int(os.environ.get("ANCHOR_EXPIRE_FRAMES",  "30"))  # ~2s @15fps after vacated

# ── ROI / traffic analytics (Sprint C) ───────────────────────────────────────
ROI_DIR            = _Path(__file__).parent / "rois"
IOU_MATCH          = float(os.environ.get("IOU_MATCH",           "0.5"))
MIN_OCCUPY_FRAMES  = int(os.environ.get("MIN_OCCUPY_FRAMES",    "15"))
FREE_FRAMES        = int(os.environ.get("FREE_FRAMES",          "15"))
OVERSTAY_SEC       = int(os.environ.get("OVERSTAY_SEC",         "300"))
RESTRICTED_MIN_SEC = int(os.environ.get("RESTRICTED_MIN_SEC",   "5"))
OCCUPANCY_MAX      = int(os.environ.get("OCCUPANCY_MAX",        "10"))

# ── Multi-camera registry (F3) ───────────────────────────────────────────────
# CHANNELS env: space- or comma-separated channel numbers (e.g. "4 1" or "4,1").
# Each entry produces a cam_id string equal to the channel number.
CAMERAS = [
    {"id": str(c), "type": "dahua", "channel": int(c)}
    for c in os.environ.get("CHANNELS", str(LF_CHANNEL)).replace(",", " ").split()
]
# Hikvision EHome cameras via VMS — HIK_CAMERAS = ';'-separated "id=ip" entries:
#   HIK_CAMERAS="103=10.0.0.103;104=10.0.0.104"
# `id` becomes the cam_id (→ rois/ch{id}.json, logs/ch{id}_*, review_agent/…). Hik
# EHome devices are single-camera → channel is always 1; UserID is resolved by IP at
# startup (never hardcoded — see live_sources.resolve_live_specs).
for _entry in os.environ.get("HIK_CAMERAS", "").replace("\n", ";").split(";"):
    _entry = _entry.strip()
    if not _entry or "=" not in _entry:
        continue
    _cid, _ip = _entry.split("=", 1)
    _cid, _ip = _cid.strip(), _ip.strip()
    if _cid and _ip:
        CAMERAS.append({"id": _cid, "type": "hik", "device_ip": _ip})

# Single batched backend — pick port from the first DAHUA channel if any, else a default.
_first_ch = next((c["channel"] for c in CAMERAS if c["type"] == "dahua"), LF_CHANNEL)
LF_PORT = LF_PORT_BASE + _first_ch

# TensorRT engine for the vehicle model (built from .pt at startup if missing).
VEHICLE_ENGINE_PATH = os.environ.get(
    "VEHICLE_ENGINE_PATH",
    str(_Path(VEHICLE_MODEL_PATH).with_suffix(".engine")),
)
BATCH_MAX = int(os.environ.get("BATCH_MAX", "8"))  # engine dynamic-batch upper bound

# ── Docker auto-start ─────────────────────────────────────────────────────────
# Set AUTO_START=1 in compose to start all cameras on container boot (no POST /start needed).
AUTO_START = os.environ.get("AUTO_START", "0") == "1"

# ── Training-data collector (Sprint H) ───────────────────────────────────────
# Save model inputs + pre-labels for retraining: vehicle frames (1/interval per
# camera), plate-detect crops (every read_zone capture), تونس/other plate crops.
# YOLO layout under DATASET_ROOT (mount it as a volume in Docker — it grows).
DATASET_COLLECT_ENABLED = os.environ.get("DATASET_COLLECT_ENABLED", "true").lower() == "true"
DATASET_ROOT            = _Path(os.environ.get("DATASET_ROOT", str(_Path(__file__).parents[1] / "datasets")))
DATASET_INTERVAL_SEC    = float(os.environ.get("DATASET_INTERVAL_SEC", "5.0"))  # vehicle-frame sampling: 1 frame / 5s / camera
DATASET_JPEG_QUALITY    = int(os.environ.get("DATASET_JPEG_QUALITY", "85"))
DATASET_SERVICE_TAG     = os.environ.get("DATASET_SERVICE_TAG", "live_feed")
