# Sprint F6 — Migrate `live_feed` to the shared `vms_client` (Hik via VMS, drop RTSP)

Rewire `vehicle_services/live_feed` so **all** camera frames come through the shared
**`vms_client`** package — the same migration already done for `nightmode` and
`live_inference`. After this sprint, VMS connection logic lives in **one place**; when the
VMS API changes we fix `vms_client`, not `live_feed`.

Two functional changes ride along with the plumbing swap:

1. **Hikvision moves onto the VMS.** Today Hik is reached by a hardcoded RTSP URL
   (`10.0.0.78` via `cv2.VideoCapture`). After F6 a Hik camera is a
   `CameraSpec(kind="hik", …)` streamed over EHome WebSocket through the VMS — chosen by
   **camera IP**, exactly like the Dahua channels.
2. **Direct-RTSP support is removed entirely.** `RtspFrameSource` and the `RTSP_CAMERAS`
   env var go away. Every camera is either `kind="dahua"` (NVR channel) or `kind="hik"`
   (EHome device). No hardcoded `ch4` / `ch14` / `.78`.

> **Read first:** `vms_client/MIGRATION.md` (the recipe), `live_inference/live_sources.py`
> + `live_inference/sprints/SPRINT_LI0_vms_client_migration.md` (the closest precedent —
> same batched `MultiStreamDriver` architecture), and `nightmode/live_sources.py` (the
> reference for resolving Hik `UserID` by IP).
>
> Decisions locked for this sprint (asked 2026-06-29):
> **(a)** camera registry = **env lists by kind** — `CHANNELS` for Dahua (unchanged),
> new `HIK_CAMERAS` (by IP) for Hik; **(b)** **drop** direct-RTSP support.

---

## 0. Why — and what `vms_client` gives us for free

`live_feed` currently carries its own VMS stack:

- `live_feed/vms.py` — a standalone `DahuaVMSClient` (HTTP MJPEG; **decodes JPEG→BGR
  inside `iter_frames`**).
- `live_feed/stream.py` — `FrameGrabber` (reader thread + fixed-then-exponential backoff
  around the Dahua client) and `RtspFrameSource` (`cv2.VideoCapture` for Hik/any RTSP).

`vms_client` is a **superset** of all of it:

- `DahuaFrameSource` ≈ `FrameGrabber` — reader thread, **exponential-backoff reconnect
  (1 s→16 s)**, JPEG→BGR decode, **drop-stale single-slot** `get_latest()`. It also does
  `login()`/`logout()` **inside its own reader loop per (re)connect** — so the driver no
  longer owns a client object or a login lifecycle.
- `HikEHomeFrameSource` — Hikvision over EHome WebSocket through the VMS. This is what
  replaces `RtspFrameSource` for Hik. Proactively closes the WS on `stop()`.
- `open_source(CameraSpec, VMSConfig)` — picks the impl and **applies the Dahua SDK
  channel offset internally** (pass the USER channel; it computes `sdk = ch + offset`).
- Identical consumer interface: `start()` / `get_latest() → (frame|None, ts)` / `stop()`
  / `frames_total` / `frames_dropped` — a drop-in for the driver's tick loop.

`vms_client` is **already installed** in `/home/malek/workflow/.venv_linux` (editable,
compat mode — `import vms_client` works from the workflow root). No new package work.

---

## 1. Complete inventory — every touch point (nothing missed)

| # | File:loc | Today | After F6 |
|---|---|---|---|
| 1 | `live_feed/vms.py` | local `DahuaVMSClient` | **DELETE** (use `vms_client`) |
| 2 | `live_feed/stream.py` | `FrameGrabber` + `RtspFrameSource` | **DELETE whole file** — both superseded; only `multistream.py` imports it |
| 3 | `multistream.py:31` | `from live_feed.stream import FrameGrabber, RtspFrameSource` | **remove** |
| 4 | `multistream.py:32` | `from live_feed.vms import DahuaVMSClient` | `from vms_client import open_source, FrameSource` |
| 5 | `multistream.py:157-176` | `_make_source(camera)` branches dahua/rtsp, returns `(client, source)` | rewrite → takes a resolved `CameraSpec`, returns a started `FrameSource` (no client). See §4 |
| 6 | `multistream.py:160-168` | `DahuaVMSClient(...)` + `client.login()` + `sdk_ch = channel + offset` | **delete all** — `open_source` builds the client, offsets the channel, and `start()` logs in inside the reader thread |
| 7 | `multistream.py:198` | `self._clients: dict[str, DahuaVMSClient] = {}` | **delete** — sources self-manage login/logout |
| 8 | `multistream.py:199` | `self._sources: dict[str, FrameGrabber] = {}` | `self._sources: dict[str, FrameSource] = {}` |
| 9 | `multistream.py:246-255` | `start()` loops cams, `_make_source(cam)`, stashes `_clients`/`_sources` | loop resolved specs → `open_source(spec, vms_cfg)`, `src.start()`, stash `_sources` |
| 10 | `multistream.py:257-268` | `stop()` stops sources **then** `client.logout()` | drop the `_clients.logout()` loop — `src.stop()` tears down stream + logout |
| 11 | `multistream.py:274-289` | tick loop `src.get_latest()` | **unchanged** (same interface) |
| 12 | `app.py:246` | `"port": cfg.LF_PORT_BASE + cfg.CAMERAS[0]["channel"]` | **fix** — Hik cams have no `"channel"`; use `cfg.LF_PORT` (see §3) |
| 13 | `app.py:509` | `port = cfg.LF_PORT_BASE + cfg.CAMERAS[0]["channel"]` | **fix** — same; use `cfg.LF_PORT` |
| 14 | `app.py:64-71, 79, 197, 203-242, 275-281` | iterate `cfg.CAMERAS`, use `cam["id"]` | **unchanged** — every other use keys on `"id"`, which all specs have |
| 15 | `config.py:33-39` | legacy `VMS_URL/DEVICE_IP/DEVICE_PORT/USERNAME/PASSWORD/CHANNEL_OFFSET` | keep as **fallback** feeding `VMSConfig` (see §3); going forward use `VMSConfig` names |
| 16 | `config.py:69-87` | `CAMERAS` = `CHANNELS` (dahua) + `RTSP_CAMERAS` (rtsp) | `CHANNELS` (dahua) + **`HIK_CAMERAS`** (hik by IP); **remove the `RTSP_CAMERAS` block** (see §2) |
| 17 | `config.py` (new) | — | add `LF_PORT` (safe single backend port, §3) |
| 18 | `live_feed/live_sources.py` | — | **NEW** — `build_vms_config()` + `resolve_live_specs(CAMERAS)` (§4) |
| 19 | `review_agent/run.py:33-36` | `CAMERAS[0]["id"]` | **unchanged** — `"id"` still present |
| 20 | `start_live_feed.sh` | `CHANNELS` + `RTSP_CAMERAS` + `VMS_URL/DEVICE_*` | env remap to `VMSConfig` names, add `HIK_CAMERAS` + `VMS_HIK_PORT`, drop `RTSP_CAMERAS`, rebuild `VITE_CAMERAS` (§5) |
| 21 | `live_feed/launch.sh` | legacy per-channel launcher, Dahua-only | **out of scope** — already superseded by `start_live_feed.sh`; leave a note that it's stale (per-channel ports, no Hik) |

**Behavioural diff to note:** old `vms.py:iter_frames` **decoded** to BGR;
`vms_client.DahuaVMSClient.iter_frames` yields **raw JPEG bytes** and the *FrameSource*
decodes. `live_feed` only ever consumed frames through `FrameGrabber.get_latest()` (which
already returns decoded BGR) — and that path is preserved by `FrameSource.get_latest()`.
No consumer touches raw `iter_frames`, so nothing downstream changes.

---

## 2. Camera registry — `config.py` `CAMERAS`

Keep the env-list-by-kind shape. Dahua is unchanged; the RTSP block is replaced by a Hik
block keyed by **camera IP**.

```python
# ── Multi-camera registry ─────────────────────────────────────────────────────
# Dahua NVR channels (unchanged): CHANNELS = space/comma-separated channel numbers.
#   cam_id = the channel number (e.g. "20").
CAMERAS = [
    {"id": str(c), "type": "dahua", "channel": int(c)}
    for c in os.environ.get("CHANNELS", str(LF_CHANNEL)).replace(",", " ").split()
]

# Hikvision EHome cameras via the VMS — HIK_CAMERAS = ';'-separated "id=ip" entries:
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
```

- **Delete** the entire `RTSP_CAMERAS` parsing block (`config.py:76-87`).
- `cam_id` conventions are unchanged for Dahua (= channel number). For Hik, the operator
  picks the id in `HIK_CAMERAS`; that id drives `rois/ch{id}.json`, `logs/ch{id}_*`, and
  review_agent records — so choose stable ids (e.g. `103`, `104`).

---

## 3. Config reconciliation — env var names + safe port

`vms_client.VMSConfig` uses different env names than `live_feed`'s legacy set. **Both Hik
and Dahua now share one VMS host** (`VMSConfig.vms_host` backs both `hik_base_url` and
`dahua_base_url`).

| live_feed legacy | `vms_client` `VMSConfig` |
|---|---|
| `VMS_URL` (`http://host:5057`) | `VMS_HOST` + `VMS_DAHUA_PORT` (5057) → `dahua_base_url` |
| — (Hik was RTSP) | `VMS_HIK_PORT` (5200) → `hik_base_url` |
| `VMS_DEVICE_IP` | `DAHUA_NVR_IP` |
| `VMS_DEVICE_PORT` (37777) | `DAHUA_NVR_PORT` (37777) |
| `VMS_USERNAME` | `DAHUA_NVR_USER` |
| `VMS_PASSWORD` | `DAHUA_NVR_PASS` |
| `VMS_CHANNEL_OFFSET` (−1) | `DAHUA_CHANNEL_OFFSET` (−1) |

`build_vms_config()` (§4) keeps the legacy `VMS_*` vars working as a **fallback** so
deployment env needn't change in lockstep — but it must also derive `vms_host` (Hik needs
it). **Going forward set the `VMSConfig` names in `start_live_feed.sh` (§5).**

> ⚠ **Single-host assumption.** `VMSConfig` points both gateways at one `vms_host`. The
> current `start_live_feed.sh` aims the Dahua gateway at the **WSL Windows host**
> (`dahuasdkweb` on `:5057`). For F6 the Hik EHome gateway (`:5200`) must be reachable on
> that **same host**. If the Hik VMS lives elsewhere (e.g. the `10.0.0.113` SecureVision
> server, per `reference: securevision-vms`), that is a deployment decision to resolve
> before testing — flag it in verification (§6).

**Safe backend port** — `app.py` computes the port from `CAMERAS[0]["channel"]`, which
KeyErrors if the first camera is Hik. Add to `config.py`:

```python
# Single batched backend — pick port from the first DAHUA channel if any, else a default.
_first_ch = next((c["channel"] for c in CAMERAS if c["type"] == "dahua"), LF_CHANNEL)
LF_PORT = LF_PORT_BASE + _first_ch
```

Replace both `cfg.LF_PORT_BASE + cfg.CAMERAS[0]["channel"]` sites (`app.py:246, 509`) with
`cfg.LF_PORT`.

---

## 4. New file — `live_feed/live_sources.py`

Mirror `live_inference/live_sources.py` + the Hik-by-IP resolution from
`nightmode/live_sources.py`.

```python
from __future__ import annotations
import logging

from vms_client import CameraSpec, VMSConfig, HikvisionEHomeClient
import live_feed.config as cfg

log = logging.getLogger(__name__)


def build_vms_config() -> VMSConfig:
    """VMSConfig from env; fall back to live_feed's legacy VMS_* fields when env unset."""
    vc = VMSConfig()                                   # reads VMS_HOST / VMS_*_PORT / DAHUA_NVR_*
    # Legacy VMS_URL ("http://host:5057") → host, only if VMS_HOST not explicitly set.
    if vc.vms_host == "10.0.0.113" and cfg.VMS_URL:    # default unchanged ⇒ no VMS_HOST given
        host = cfg.VMS_URL.split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
        if host:
            vc.vms_host = host
    if not vc.dahua_nvr_ip:   vc.dahua_nvr_ip   = cfg.VMS_DEVICE_IP
    if not vc.dahua_nvr_user: vc.dahua_nvr_user = cfg.VMS_USERNAME
    if not vc.dahua_nvr_pass: vc.dahua_nvr_pass = cfg.VMS_PASSWORD
    if cfg.VMS_DEVICE_PORT:   vc.dahua_nvr_port = cfg.VMS_DEVICE_PORT
    return vc


def resolve_live_specs(cameras: list[dict], vms_cfg: VMSConfig) -> dict[str, CameraSpec]:
    """Map each camera dict (config.CAMERAS) → vms_client.CameraSpec.

    Hik cameras get UserID resolved at startup by matching device_ip against
    HikvisionEHomeClient.list_devices() — UserID is NOT stable across re-registration,
    so it is never hardcoded/persisted. Raises if a Hik IP is not registered in the VMS.
    """
    hik_cams = [c for c in cameras if c["type"] == "hik"]
    hik_user_ids: dict[str, int] = {}     # device_ip → UserID

    if hik_cams:
        hc = HikvisionEHomeClient(vms_cfg.hik_base_url)
        hc.start_all()
        devices = hc.list_devices()
        registered = [d.get("IP") for d in devices]
        for c in hik_cams:
            ip = c["device_ip"]
            match = next((d for d in devices if d.get("IP") == ip), None)
            if match is None:
                raise RuntimeError(
                    f"Hik camera {c['id']!r} (IP={ip}) not registered in the VMS "
                    f"(registered: {registered}). Set the camera's EHome/ISUP CMS "
                    f"address to the VMS server."
                )
            hik_user_ids[ip] = match["UserID"]
            log.info("Resolved cam %s (Hik, IP=%s) → UserID=%d", c["id"], ip, match["UserID"])

    specs: dict[str, CameraSpec] = {}
    for c in cameras:
        if c["type"] == "dahua":
            # USER channel — open_source applies the SDK offset; do NOT pre-apply it.
            specs[c["id"]] = CameraSpec(id=c["id"], kind="dahua", channel=c["channel"])
        elif c["type"] == "hik":
            specs[c["id"]] = CameraSpec(
                id=c["id"], kind="hik",
                user_id=hik_user_ids[c["device_ip"]], channel=1,
            )
        else:
            raise ValueError(f"camera {c['id']!r}: unknown type {c['type']!r}")
    return specs
```

---

## 5. `multistream.py` edits

Replace the source factory and the client lifecycle.

```python
# top of file
from vms_client import open_source, FrameSource
from live_feed.live_sources import build_vms_config, resolve_live_specs
# remove:  from live_feed.stream import FrameGrabber, RtspFrameSource
# remove:  from live_feed.vms import DahuaVMSClient
```

```python
# delete _make_source(camera) entirely — open_source replaces it.
```

In `MultiStreamDriver.__init__`:

```python
self._sources: dict[str, FrameSource] = {}          # was dict[str, FrameGrabber]
# delete:  self._clients: dict[str, DahuaVMSClient] = {}
self._vms_cfg = build_vms_config()
self._specs   = resolve_live_specs(cameras, self._vms_cfg)   # cam_id → CameraSpec
```

`start()`:

```python
def start(self) -> None:
    """Open and start a vms_client FrameSource per camera."""
    for cam in self._cameras:
        cam_id = cam["id"]
        try:
            src = open_source(self._specs[cam_id], self._vms_cfg)
            src.start()
            self._sources[cam_id] = src
            log.info("[driver] cam %s (%s) source started", cam_id, cam["type"])
        except Exception as exc:
            log.error("[driver] cam %s source failed to start: %s", cam_id, exc)
```

`stop()`:

```python
def stop(self) -> None:
    """Stop all frame sources (each tears down its own stream + VMS session)."""
    for cam_id, src in self._sources.items():
        try:
            src.stop()
        except Exception:
            pass
    # delete the _clients.logout() loop — logout happens inside src.stop()
    try:
        self._collector.stop()
    except Exception:
        pass
```

`run()` tick loop (`get_latest()`) is **unchanged**.

> Resolving Hik UserIDs hits the VMS once at driver construction (`__init__`). If a Hik IP
> isn't registered, `resolve_live_specs` raises there — surfaced by `POST /start`. That's
> the desired fail-fast (mirrors nightmode's "no fallback" stance).

---

## 6. `start_live_feed.sh` edits

Swap the camera + VMS env to the new scheme. Sketch (replace lines ~22-46):

```bash
# ── Cameras ───────────────────────────────────────────────────────────────────
# Dahua VMS channels (space/comma separated):
export CHANNELS="${CHANNELS:-20}"
# Hikvision EHome cameras via VMS — "id=ip", semicolon-separated:
export HIK_CAMERAS="${HIK_CAMERAS:-103=10.0.0.103}"

# ── VMS connection (vms_client VMSConfig names; one host serves Hik :5200 + Dahua :5057)
export VMS_HOST="$WINDOWS_HOST"          # host running BOTH gateways (see §3 single-host note)
export VMS_HIK_PORT="5200"
export VMS_DAHUA_PORT="5057"
export DAHUA_NVR_IP="192.168.40.200"
export DAHUA_NVR_PORT="37777"
export DAHUA_NVR_USER="admin"
export DAHUA_NVR_PASS="broNO_WoRk-2026"
export DAHUA_CHANNEL_OFFSET="-1"

# ── Frontend camera list — ids MUST match the backend registry ────────────────
DAHUA_IDS=$(echo "$CHANNELS" | tr ' ' ',' | sed 's/,,*/,/g; s/^,//; s/,$//')
HIK_IDS=$(echo "$HIK_CAMERAS" | tr ';' '\n' | sed 's/=.*//; /^$/d' | paste -sd, -)
export VITE_CAMERAS="${DAHUA_IDS}${HIK_IDS:+,$HIK_IDS}"
```

- Update the `ENV_FILE` heredoc (the per-tab env) to export the **same** new vars
  (`CHANNELS`, `HIK_CAMERAS`, `VMS_HOST`, `VMS_HIK_PORT`, `VMS_DAHUA_PORT`,
  `DAHUA_NVR_*`, `DAHUA_CHANNEL_OFFSET`, `VITE_CAMERAS`, `VITE_API_URL`, `LF_PORT_BASE`).
  Remove `RTSP_CAMERAS`, `VMS_URL`, `VMS_DEVICE_*`, `VMS_USERNAME/PASSWORD`,
  `VMS_CHANNEL_OFFSET`.
- Header comment: drop "RTSP_CAMERAS"; note Hik now via VMS. The "Prereq: Dahua SDK web
  running" line stays; add "Hik EHome CMS (:5200) reachable on $VMS_HOST".
- `live_feed/launch.sh`: legacy/Dahua-only, superseded — add a one-line stale notice; not
  migrated in F6.

---

## 7. Gotchas (from `vms_client/MIGRATION.md §8`, the ones that bite here)

- **Hik UserID by IP, resolved fresh each run** — never persist/hardcode it (§4). The
  `Online` flag is unreliable; don't gate on it (`list_devices()` may say `false` while
  the camera streams fine — 104 did exactly this).
- **Hik channel is always `1`** — single-camera EHome devices; a null channel makes
  `open_source` raise. `resolve_live_specs` hardcodes `channel=1` for `kind="hik"`.
- **Don't pre-apply the Dahua offset** — pass the USER channel to `CameraSpec`;
  `open_source` adds `dahua_channel_offset`. (Old `_make_source` did `channel + offset`
  manually — that logic is **deleted**.)
- **`get_latest()` is drop-stale, not a queue** — returns newest + clears; `(None, 0.0)`
  when nothing new. The driver already handles this (`if frame is not None`). You skip
  frames if you read slower than the camera — intended (latency control).
- **Always `stop()`** — releases the reader thread + Hik `preview_stop`/`preview_delete`
  (or Dahua stream stop + logout). The driver `stop()` does this; the removed
  `_clients.logout()` loop is now redundant (logout is inside the source).
- **Not picklable** — `FrameSource` owns a thread/socket. `live_feed` runs one batched
  process (no multiprocessing of sources), so this is fine; just never hand a source to a
  subprocess. Pass `CameraSpec`/`VMSConfig` if that ever changes.
- **NVR clock skew** — keep using wall-clock (`time.time()`) for event timestamps;
  `get_latest()`'s `ts` is `monotonic`, for latency math only.
- **Compat install** — already done in `.venv_linux`; if `import vms_client` fails from
  the workflow root, re-install with `--config-settings editable_mode=compat`.

---

## 8. Verification / acceptance

- [ ] `live_feed/vms.py` and `live_feed/stream.py` deleted;
      `grep -rn "live_feed.vms\|live_feed.stream\|FrameGrabber\|RtspFrameSource\|RTSP_CAMERAS" vehicle_services/ --include=*.py` returns nothing (outside this doc).
- [ ] `python -m vms_client.cli list` (with the F6 env exported) shows the Dahua active
      channels **and** the Hik device(s) you put in `HIK_CAMERAS` (sanity on host/creds and
      that the Hik IP is registered — resolves the §3 single-host question).
- [ ] `bash start_live_feed.sh` → backend boots; `POST /start` returns the camera list
      (Dahua channels + Hik ids); `/health` ok.
- [ ] Dashboard (`:5173`) shows live video for **both** a Dahua channel and a Hik camera;
      `GET /api/.../metrics` (or the WS) shows fps climbing per camera.
- [ ] ROI / review_agent still write under the right `cam_id` (`rois/ch{id}.json`,
      `logs/ch{id}_*`) for both kinds.
- [ ] Kill the VMS briefly → both sources auto-reconnect (1→16 s backoff) without a
      service restart; a wrong Hik IP makes `POST /start` fail fast with the
      "not registered in the VMS" message.
- [ ] No `requests`/MJPEG/WebSocket/login/`cv2.VideoCapture` capture code left in
      `live_feed` outside `vms_client`.

---

## 9. Out of scope (call out, don't do)

- Auto-discovery of cameras from the VMS (we chose explicit env lists).
- Migrating `live_feed/launch.sh` (legacy per-channel launcher; superseded by
  `start_live_feed.sh`).
- ROI/cam_id renames for existing Dahua channels (ids unchanged → existing `rois/*.json`
  keep working). New Hik ids start fresh.
