# Sprint F4 — Add RTSP cameras (Hikvision + any future RTSP camera) — ✅ IMPLEMENTED

Add the Hikvision (`10.0.0.78`, `admin` / `Waycon2025`) via **direct RTSP**, and make adding
**any** RTSP camera a one-line env change (not Hikvision-specific). **Depends on F3** (the batched
multi-cam driver). Additive: a new `FrameSource` type + a generic registry env + `start_live_feed.sh`.

> **Status:** implemented. Generalized vs the original plan — instead of a single `HIK1_RTSP_URL`,
> a generic **`RTSP_CAMERAS`** env (`;`-separated `id=url`) registers any number of RTSP cameras.
> Wired into `start_live_feed.sh` (now a single F3 backend process, not per-channel). The standalone
> `spike_batched_cam.py` is unchanged (it uses its own grabber loop, not `config.CAMERAS`).

> Prereq: F3 done — `MultiStreamDriver` builds a `FrameSource` per camera via a factory keyed on
> `camera["type"]`, and all per-camera state/files key by the string `cam_id`. F4 adds `"rtsp"`.

---

## 0. How it slots in
```
F3 already collects frames uniformly:  for cam in CAMERAS: source[cam].get_latest()
F4 adds one more source type:          type="rtsp" → RtspFrameSource(url)  (cv2.VideoCapture)
→ the Hikvision frames flow into the SAME batched vehicle-detect + per-cam track + batched
  plate/OCR/تونس cascade + per-cam ROI/serving. No pipeline/serving changes.
```

Hikvision RTSP URLs (standard):
- main stream: `rtsp://admin:Waycon2025@10.0.0.78:554/Streaming/Channels/101`
- sub stream : `rtsp://admin:Waycon2025@10.0.0.78:554/Streaming/Channels/102`

---

## 1. CREATE `RtspFrameSource` (in `live_feed/stream.py`)
Same interface as `FrameGrabber` (so the driver treats it identically): `start()`,
`get_latest() -> (frame|None, ts)`, `stop()`, `frames_total`, `frames_dropped`. A reader thread
opens the RTSP via `cv2.VideoCapture` and keeps only the latest frame (grab-latest), with
reconnect/backoff — mirror `FrameGrabber._reader_loop` (`stream.py:66-…`).

```python
import os, cv2, threading, time, numpy as np

class RtspFrameSource:
    """Direct-RTSP frame source (e.g. Hikvision). Same interface as FrameGrabber."""
    def __init__(self, url: str):
        self._url = url
        self._lock = threading.Lock()
        self._latest = None            # (frame, ts)
        self._stop = threading.Event()
        self._thread = None
        self._frames_total = 0
        self._frames_dropped = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rtsp-grabber")
        self._thread.start()

    def get_latest(self):
        with self._lock:
            if self._latest is None:
                return None, 0.0
            f, ts = self._latest
            self._latest = None        # consume (grab-latest)
            return f, ts

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def frames_total(self):   return self._frames_total
    @property
    def frames_dropped(self): return self._frames_dropped

    def _loop(self):
        # force TCP transport — reliable over the LAN; udp drops on congestion
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        backoff = 1.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)        # minimize latency
            if not cap.isOpened():
                cap.release(); time.sleep(backoff); backoff = min(backoff*2, 16); continue
            backoff = 1.0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    break                              # stream dropped → reconnect
                ts = time.monotonic()
                with self._lock:
                    if self._latest is not None:
                        self._frames_dropped += 1
                    self._latest = (frame, ts)
                self._frames_total += 1
            cap.release()
```

---

## 2. UPDATE the source factory (in `live_feed/multistream.py`, from F3)
The driver builds a source per camera. Add the `rtsp` branch:
```python
def make_source(cam):
    if cam["type"] == "dahua":
        client = DahuaVMSClient(base_url=cfg.VMS_URL, device_ip=cfg.VMS_DEVICE_IP, …)
        client.login()
        return FrameGrabber(client, cam["channel"] + cfg.VMS_CHANNEL_OFFSET, cfg.STREAM_TYPE)
    if cam["type"] == "rtsp":
        return RtspFrameSource(cam["url"])
    raise ValueError(f"unknown camera type: {cam['type']}")
```
(Dahua keeps its `DahuaVMSClient`; the driver must logout dahua clients on stop and `stop()` all
sources — track the client refs as F3 does.)

---

## 3. Camera registry — GENERIC RTSP (`config.py`) ✅
After the dahua `CAMERAS` list, parse `RTSP_CAMERAS` (`;`-separated `id=url`) so ANY rtsp camera
registers with no code change:
```python
for _entry in os.environ.get("RTSP_CAMERAS", "").replace("\n", ";").split(";"):
    _entry = _entry.strip()
    if not _entry or "=" not in _entry:
        continue
    _cid, _url = _entry.split("=", 1)     # split once — rtsp urls may contain '='
    if _cid.strip() and _url.strip():
        CAMERAS.append({"id": _cid.strip(), "type": "rtsp", "url": _url.strip()})
```
Set it (creds in env — baked in `start_live_feed.sh` like the VMS creds):
```
export RTSP_CAMERAS="hik1=rtsp://admin:Waycon2025@10.0.0.78:554/Streaming/Channels/101"
# add more cameras by appending:  ";gate=rtsp://user:pass@10.0.0.90:554/Streaming/Channels/101"
```
`cam_id` (`hik1`, `gate`, …) drives `rois/ch{id}.json`, logs, review state — and the frontend
`VITE_CAMERAS` list (derived from `CHANNELS` + the `RTSP_CAMERAS` ids in `start_live_feed.sh`).

---

## 4. ROI calibration for the new camera
The new camera needs its own zones (counting line + read_zone + parking/restricted). The per-cam
calibration endpoints from F3 already work by `cam_id`:
- `GET /snapshot?cam=hik1` → grab a frame to draw on.
- `POST /rois?cam=hik1` → save `rois/chhik1.json`.
No new code — just calibrate the Hikvision view once it streams. (Its better POV should improve
detection + line-count accuracy.)

---

## 5. Functions to KILL / UPDATE (minimal — additive)
| Action | Where | Done |
|---|---|---|
| CREATE `RtspFrameSource` (cv2, TCP, grab-latest, reconnect) | `live_feed/stream.py` | ✅ |
| UPDATE source factory (`rtsp` branch; return `(None, src)`) + `start()` skips None client | `live_feed/multistream.py` `_make_source` | ✅ |
| UPDATE registry: generic `RTSP_CAMERAS` parsing | `live_feed/config.py` | ✅ |
| REWRITE for single F3 backend + `CHANNELS`/`RTSP_CAMERAS` + derived `VITE_CAMERAS` + one `/start` | `start_live_feed.sh` | ✅ |
| Export `RTSP_CAMERAS` in `.env` / compose | `docker-compose.yml` (Sprint G) | pending G |
| (none to kill) | — | — |

---

## 6. Networking / deployment
- The host (and, in Sprint G, the **container**) must reach `10.0.0.78:554`. From the WSL/Linux
  host this is direct LAN. In Docker, ensure the container is on a network that can route to
  `10.0.0.78` (host network, or a bridge with access) — RTSP/TCP 554 must be reachable.
- RTSP creds live in env/secret, never in the image. In cloud, the Hikvision feed must be reachable
  from the cloud host (VPN / relay) — same consideration as the Dahua VMS source.
- Use **TCP** transport (set above) — UDP RTSP drops frames over congested links.

## 7. Verification / acceptance
- [ ] `RtspFrameSource` opens `10.0.0.78` and yields BGR frames (quick standalone: open URL, read 30
      frames, check `frame.mean()` > 0 and shape).
- [ ] With `HIK1_RTSP_URL` set, the driver runs **3 cameras** (ch4 + ch1 + hik1); `/metrics?cam=hik1`
      shows ≥15 fps; `nvidia-smi` VRAM still flat (shared model — adding a camera adds little).
- [ ] `/stream.mjpg?cam=hik1` shows the annotated Hikvision feed (and it should look **clearer**
      than the Dahua ones); plates/types/tracks populate.
- [ ] RTSP resilience: pull the Hikvision network briefly → its source reconnects (backoff) and the
      other cameras are unaffected.
- [ ] Calibrate `rois/chhik1.json` (line + read_zone) and confirm line counting works on it.

## 8. Risks / notes
- **RTSP latency/jitter**: `CAP_PROP_BUFFERSIZE=1` + grab-latest keeps it real-time; TCP transport
  for reliability. If ffmpeg can't open it, verify the URL path (Hikvision firmware variants:
  `/Streaming/Channels/101` vs `/h264/ch1/main/av_stream`) and that port 554 is open.
- **Mixed resolutions**: Hikvision main may differ in resolution from the Dahua 1080p. Batched
  `predict` handles a list of mixed-size frames (per-image letterbox); fine, just slightly less
  uniform batching. Detector runs at `imgsz=640` regardless.
- **Codec**: Hikvision main is usually H.264/H.265 — ffmpeg (opencv) decodes both; H.265 needs the
  ffmpeg build to support hevc (the venv's opencv does). If H.265 fails to open, use the H.264 sub
  stream or set the camera main to H.264.
