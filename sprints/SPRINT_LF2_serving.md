# Sprint LF-2 — Serving layer (MJPEG + WebSocket)

**Phase:** Live Feed — precursor to Phase C.
**Depends on:** LF-1 (stream core validated at 15 FPS).
**Goal:** Expose the LF-1 pipeline over HTTP so the Vite frontend can show a **large live annotated video** + a **live profiles table**. One service instance = one camera (per-process model from LF-1); multiple cameras = multiple instances (GPU shared via MPS).

---

## 1. Service model (per-camera process)

- `live_feed/app.py` = a FastAPI service that runs **one camera's** pipeline (channel from env/arg) and serves it.
- Launch one instance per camera on its own port: `LF_PORT = 5030 + user_channel` (e.g. ch4 → 5034, ch5 → 5035).
- A small `live_feed/launch.sh` starts one process per requested channel (and reminds to enable CUDA MPS for >1 camera).
- The pipeline runs in a **background thread** inside the process; the FastAPI endpoints read its shared state (latest annotated frame + active-profile table + perf snapshot) under a lock.

---

## 2. Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/start` | start the camera pipeline (body: `{stream_type?}`; channel is fixed per process). Idempotent. |
| `POST` | `/stop` | stop the pipeline (VMS logout, thread join). |
| `GET` | `/health` | `{status, channel, running, models_loaded}` |
| `GET` | `/metrics` | perf snapshot from `perf.py` (fps, per-stage ms, e2e latency, ocr_rate, active_tracks) |
| `GET` | `/stream.mjpg` | **annotated MJPEG** (`multipart/x-mixed-replace; boundary=frame`) — the live video |
| `WS` | `/ws` | pushes **profile events** (`active` updates on lock, `final` on track exit) + periodic **metrics** |

---

## 3. Annotated MJPEG (`/stream.mjpg`)

- The pipeline draws on each processed frame: vehicle box + label `type colour` and, when a plate is read, `plate_number (plate_type)`. Reuse `common/annotate` style; colour-code box by type.
- Encode each annotated frame to JPEG (quality ~80) and yield as `multipart/x-mixed-replace` (same technique as `live_inference` / `DahuaVMSClient.iter_frames`, in reverse).
- Serve the **latest annotated frame** at the pipeline's 15 FPS; if a client is slow, it just gets the latest (no buffering).

---

## 4. WebSocket (`/ws`)

- On connect: send the current active-profile table (snapshot).
- Then push:
  - `{"type":"profile", "event":"active"|"final", "profile": VehicleProfile}` — on plate lock / track finalize.
  - `{"type":"metrics", "data": <perf snapshot>}` — every ~1–2 s.
- JSON-serialize `VehicleProfile` (include a small base64 plate/vehicle thumbnail for the table, optional — keep it small).

---

## 5. Config additions (`live_feed/config.py`)

```python
LF_PORT_BASE = 5030          # service port = LF_PORT_BASE + user_channel
JPEG_QUALITY = 80
WS_METRICS_HZ = 1            # metrics push rate
THUMB_MAX_W  = 160           # profile thumbnail width for the table (optional)
```

---

## 6. Acceptance criteria

- `python -m uvicorn live_feed.app:app` (with `LF_CHANNEL=4`, VMS_* env) starts; `POST /start` begins processing.
- `GET /stream.mjpg` shows the **live annotated video** in a browser (boxes + type/colour + plate when present).
- `WS /ws` streams profile events as vehicles are read, plus metrics.
- `GET /metrics` matches the LF-1 perf numbers (still ~15 FPS with serving overhead — confirm serving didn't blow the latency budget).
- Two instances on two ports run concurrently for two cameras (MPS enabled) without crashing — `n≥1` validated.

---

## 7. Out of scope

- The UI itself (LF-3). Auth (local/dev only for the spike). Recording/persistence (in-memory profiles only).
