# Sprint LF-3 — Vite frontend (minimal live dashboard)

**Phase:** Live Feed — precursor to Phase C.
**Depends on:** LF-2 (MJPEG + WebSocket serving).
**Goal:** A **minimal** Vite app to watch the live feed and judge latency: a **large, clear annotated video** on top, a **live results table** of vehicle profiles below, basic start/stop + a metrics strip. This is a **validation/monitoring UI**, not the product UI — keep it lean.

---

## 1. Stack & layout

- **Vite + React** (TS optional), minimal deps. Plain CSS — no heavy UI kit.
- Layout (single page):
  ```
  ┌─────────────────────────────────────────────┐
  │  [camera selector ch4 ▾]  [Start] [Stop]     │
  │  FPS 14.8 · e2e 92ms · OCR 1.3/s · tracks 3  │  ← metrics strip
  ├─────────────────────────────────────────────┤
  │                                             │
  │            LARGE annotated video            │  ← MJPEG, big & clear (≥ 70% viewport width)
  │            (<img src=/stream.mjpg>)          │
  │                                             │
  ├─────────────────────────────────────────────┤
  │  track | type | colour | plate | type | seen │  ← live profiles table
  │  ...                                         │
  └─────────────────────────────────────────────┘
  ```

---

## 2. Data wiring

- **Video:** `<img src="http://<host>:<LF_PORT>/stream.mjpg">` — browser renders MJPEG natively. Make it the dominant element (large, sharp; `image-rendering:auto`, max it to the viewport width).
- **Table + metrics:** open `WebSocket(ws://<host>:<LF_PORT>/ws)`:
  - on `profile` (`active`/`final`) → upsert the row by `track_id` (mark `final` rows e.g. greyed); keep most-recent N.
  - on `metrics` → update the metrics strip.
- **Controls:** Start/Stop → `POST /start` / `POST /stop`. Camera selector switches the host:port (one service per camera, port = 5030 + channel).
- **Multi-camera (1–2):** simple tabs (one per camera) or a 2-up grid; each tab points at its camera's port. Keep it simple.

---

## 3. Table columns

`track_id` · `type` · `colour` · `plate_number` · `plate_type (tounes/other)` · `plate_conf` · `first_seen` · `status (active/final)` · (optional) plate thumbnail.

---

## 4. Config

- A `.env` / config with the camera→host:port map (e.g. `ch4 → http://localhost:5034`).
- CORS is already `*` on the services; dev runs via `npm run dev` (Vite dev server) hitting the LF-2 ports.

---

## 5. Acceptance criteria

- `npm run dev` serves the page; selecting a camera + Start shows the **large live annotated video** within ~1–2 s.
- The **table updates live** as vehicles are detected/plates read (rows upsert by track, finalize on exit).
- The **metrics strip** reflects `/ws` metrics (FPS ≈ 15, e2e latency, OCR rate, active tracks) — this is where we *watch* the latency and decide pass/fail.
- Works for **1–2 cameras** (tabs/grid).
- Minimal but clear: video is large and legible; table is readable. No design polish required.

---

## 6. Out of scope

- Auth, user management, persistence/history, export, mobile layout. Any product-grade polish (this is the validation UI). The product UI is a later, separate effort.

---

## After LF-3 — the validation gate

With LF-1+LF-2+LF-3 running on a real camera, we **watch the metrics strip + video** and judge: does it hold ~15 FPS, is end-to-end latency acceptable, are plates/types/colours sensible on real (imperfect-angle) footage? If yes → **proceed to Phase C traffic_analytics** (add geometry + counting/parking/restricted rules on top of this exact enriched-tracker core). If latency fails → tune (drop to 720 sub-stream, raise OCR gating, MPS) before rules.
