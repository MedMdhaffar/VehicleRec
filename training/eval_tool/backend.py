"""
Real-footage evaluation tool — backend.

Samples frames from a CCTV video, runs the vehicle detector, serves
frame+detection data to the frontend, receives human labels, and computes
per-class precision/recall/F1 + a confusion matrix. Saves labeled test sets
to test_sets/ for reuse across model versions.

Run:
    python training/eval_tool/backend.py [--port 5021]

Then open training/eval_tool/frontend/index.html in a browser.
"""
from __future__ import annotations

import base64
import json
import uuid
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
EVAL_TOOL_DIR = Path(__file__).resolve().parent
TEST_SETS_DIR = EVAL_TOOL_DIR / "test_sets"
TEST_SETS_DIR.mkdir(exist_ok=True)

VS_DIR           = EVAL_TOOL_DIR.parent.parent
DEFAULT_MODEL    = str(VS_DIR / "common" / "models" / "vehicle_yolo26.pt")

CLASS_NAMES = ["bus", "camper", "car", "cyclist", "motorcycle",
               "person", "tractor", "truck", "van"]

app = FastAPI(title="Vehicle Eval Tool", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store {session_id: SessionData}
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    video_path:  str
    model_path:  str = DEFAULT_MODEL
    sample_rate: float = 1.0   # frames per second to sample
    conf:        float = 0.35


class LabelEntry(BaseModel):
    det_id:       int
    ground_truth: str  # class name or "false_positive"


class FrameLabelRequest(BaseModel):
    labels:        list[LabelEntry]
    missed_counts: dict[str, int] = {}  # class_name → count of missed vehicles


# ---------------------------------------------------------------------------
# Detection helper
# ---------------------------------------------------------------------------

def load_model(model_path: str):
    from ultralytics import YOLO
    return YOLO(model_path)


def detect_frame(model, frame: np.ndarray, conf: float) -> list[dict]:
    results = model(frame, conf=conf, verbose=False)[0]
    detections = []
    for i, box in enumerate(results.boxes):
        cls_id = int(box.cls)
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
        crop = frame[y1:y2, x1:x2]
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        crop_b64 = base64.b64encode(buf).decode()
        detections.append({
            "det_id":     i,
            "bbox":       [x1, y1, x2, y2],
            "predicted":  CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else str(cls_id),
            "confidence": round(float(box.conf), 4),
            "crop_b64":   crop_b64,
            "ground_truth": None,  # filled by human
        })
    return detections


def encode_frame(frame: np.ndarray) -> str:
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf).decode()


def annotate_frame(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    """Draw each detection's box + '#id class' label so the displayed frame
    matches the detection rows (rows show '#det_id'). Drawn on a copy — the
    crops sent to the frontend stay clean (they were cut before this)."""
    img = frame.copy()
    h, w = img.shape[:2]
    thick = max(2, w // 600)            # scale up so it's legible after the frontend downscales
    fs    = max(0.5, w / 1600.0)
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        label = f"#{det['det_id']} {det['predicted']}"
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), thick)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, thick)
        ty = max(th + 4, y1)
        cv2.rectangle(img, (x1, ty - th - 4), (x1 + tw + 4, ty), (0, 255, 0), -1)
        cv2.putText(img, label, (x1 + 2, ty - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), thick, cv2.LINE_AA)
    return img


# ---------------------------------------------------------------------------
# Session sampling
# ---------------------------------------------------------------------------

def sample_video(video_path: str, model_path: str, sample_rate: float, conf: float) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(fps / sample_rate))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    model = load_model(model_path)
    frames = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            detections = detect_frame(model, frame, conf)
            frames.append({
                "frame_index":   frame_idx,
                "frame_b64":     encode_frame(annotate_frame(frame, detections)),
                "detections":    detections,
                "missed_counts": {},
            })
        frame_idx += 1

    cap.release()
    return {
        "video_path":   video_path,
        "model_path":   model_path,
        "sample_rate":  sample_rate,
        "conf":         conf,
        "video_fps":    fps,
        "total_frames": total,
        "frames":       frames,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(session: dict) -> dict:
    # TP/FP/FN per class
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for frame in session["frames"]:
        for det in frame["detections"]:
            pred = det["predicted"]
            gt   = det.get("ground_truth")
            if gt is None:
                continue  # unlabelled — skip
            if gt == "false_positive":
                fp[pred] += 1
                confusion[pred]["false_positive"] = confusion[pred].get("false_positive", 0) + 1
            elif gt == pred:
                tp[pred] += 1
                confusion[pred][gt] = confusion[pred].get(gt, 0) + 1
            else:
                fp[pred] += 1
                fn[gt] += 1
                confusion[pred][gt] = confusion[pred].get(gt, 0) + 1

        # Missed vehicles (FN from human tally)
        for cls_name, count in frame.get("missed_counts", {}).items():
            fn[cls_name] += count

    all_classes = sorted(set(list(tp) + list(fp) + list(fn)))
    per_class = []
    for cls in all_classes:
        t = tp.get(cls, 0)
        f_p = fp.get(cls, 0)
        f_n = fn.get(cls, 0)
        prec = t / (t + f_p) if (t + f_p) > 0 else 0.0
        rec  = t / (t + f_n) if (t + f_n) > 0 else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        per_class.append({
            "class":     cls,
            "TP":        t,
            "FP":        f_p,
            "FN":        f_n,
            "precision": round(prec, 4),
            "recall":    round(rec, 4),
            "F1":        round(f1, 4),
            "van_highlight": cls == "van",
        })

    return {
        "per_class":  per_class,
        "confusion":  {k: dict(v) for k, v in confusion.items()},
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    """Serve the frontend so the tool is reachable at http://host:port/ ."""
    return FileResponse(EVAL_TOOL_DIR / "frontend" / "index.html")


@app.post("/session/start")
def session_start(req: StartRequest):
    if not Path(req.video_path).exists():
        raise HTTPException(400, f"Video not found: {req.video_path}")
    if not Path(req.model_path).exists():
        raise HTTPException(400, f"Model not found: {req.model_path}")

    session_id = str(uuid.uuid4())[:8]
    try:
        data = sample_video(req.video_path, req.model_path, req.sample_rate, req.conf)
    except Exception as e:
        raise HTTPException(500, str(e))

    _sessions[session_id] = data
    return {
        "session_id":  session_id,
        "frame_count": len(data["frames"]),
        "video_fps":   data["video_fps"],
        "model_path":  req.model_path,
    }


@app.get("/session/{session_id}/frame/{n}")
def get_frame(session_id: str, n: int):
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    frames = session["frames"]
    if n < 0 or n >= len(frames):
        raise HTTPException(404, f"Frame {n} out of range (0–{len(frames)-1})")
    frame = frames[n]
    return {
        "frame_index": frame["frame_index"],
        "frame_b64":   frame["frame_b64"],
        "detections":  frame["detections"],
        "total_frames": len(frames),
    }


@app.post("/session/{session_id}/frame/{n}/label")
def label_frame(session_id: str, n: int, req: FrameLabelRequest):
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    frames = session["frames"]
    if n < 0 or n >= len(frames):
        raise HTTPException(404, f"Frame {n} out of range")

    frame = frames[n]
    # Apply labels by det_id
    det_map = {d["det_id"]: d for d in frame["detections"]}
    for entry in req.labels:
        if entry.det_id in det_map:
            det_map[entry.det_id]["ground_truth"] = entry.ground_truth

    frame["missed_counts"] = req.missed_counts
    return {"ok": True}


@app.post("/session/{session_id}/finalize")
def finalize(session_id: str):
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    metrics = compute_metrics(session)

    # Persist test set (without frame_b64 / crop_b64 to keep file small)
    def strip_images(frames):
        out = []
        for f in frames:
            dets = [{k: v for k, v in d.items() if k not in ("frame_b64", "crop_b64")}
                    for d in f["detections"]]
            out.append({**{k: v for k, v in f.items() if k != "frame_b64"
                           and k != "detections"}, "detections": dets})
        return out

    test_set = {
        "session_id": session_id,
        "video_path": session["video_path"],
        "model_path": session["model_path"],
        "sample_rate": session["sample_rate"],
        "conf":        session["conf"],
        "frames":      strip_images(session["frames"]),
        "metrics":     metrics,
    }

    out_path = TEST_SETS_DIR / f"{session_id}.json"
    out_path.write_text(json.dumps(test_set, indent=2))

    return {
        "session_id": session_id,
        "test_set_path": str(out_path),
        "metrics": metrics,
    }


@app.get("/session/{session_id}/report")
def report(session_id: str):
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return compute_metrics(session)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5021)
    ap.add_argument("--host", type=str, default="127.0.0.1")
    args = ap.parse_args()

    print(f"Eval tool backend running at http://{args.host}:{args.port}")
    print(f"Test sets will be saved to: {TEST_SETS_DIR}")
    uvicorn.run(app, host=args.host, port=args.port)
