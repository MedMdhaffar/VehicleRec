"""dataset_collector.py — save model inputs + pre-labels for retraining.

Three datasets, one per YOLO model we deploy, written in the layout ultralytics
retrains from directly:

  $DATASET_ROOT/
    vehicle_detect/                 # DETECTION — full frame + all vehicle boxes
      images/  {svc}_ch{cam}_{frame}_{seq}.jpg
      labels/  {svc}_ch{cam}_{frame}_{seq}.txt    # rows: cls cx cy w h  (norm to FULL frame)
      classes.txt / data.yaml
    plate_detect/                   # DETECTION — vehicle crop + plate box
      images/  {svc}_ch{cam}_t{track}_{seq}.jpg   # the crop the plate model sees
      labels/  {svc}_ch{cam}_t{track}_{seq}.txt   # 0 cx cy w h (plate, norm to crop); empty = negative
      classes.txt / data.yaml
    plate_type_cls/                 # CLASSIFICATION — folder-per-class
      tounes/  {svc}_ch{cam}_t{track}_{seq}.jpg
      other/   {svc}_ch{cam}_t{track}_{seq}.jpg

All disk I/O happens on ONE background writer thread draining a bounded queue, so
the GPU tick loop only ever enqueues (and drops, logged, if the queue backs up) —
it never blocks on cv2.imwrite. The save_* methods are called from the single
driver thread, so the seq counter / per-cam timers need no locking.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


class DatasetCollector:
    def __init__(
        self,
        service: str,
        root: str | Path,
        *,
        enabled: bool,
        interval_sec: float,
        jpeg_quality: int,
        class_names: dict[int, str],
        queue_max: int = 256,
    ) -> None:
        self.enabled = enabled
        self._svc = service
        self._root = Path(root)
        self._interval = interval_sec
        self._jpeg_q = int(jpeg_quality)
        self._names = dict(class_names)

        self._last_veh: dict[str, float] = {}   # cam_id → last vehicle-frame save (monotonic)
        self._seq = 0                            # monotonic, single-producer → uniqueness
        self._dropped = 0
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        if not enabled:
            return

        self._veh_img   = self._root / "vehicle_detect" / "images"
        self._veh_lbl   = self._root / "vehicle_detect" / "labels"
        self._plate_img = self._root / "plate_detect" / "images"
        self._plate_lbl = self._root / "plate_detect" / "labels"
        self._type_dir  = self._root / "plate_type_cls"
        for d in (self._veh_img, self._veh_lbl, self._plate_img, self._plate_lbl,
                  self._type_dir / "tounes", self._type_dir / "other"):
            d.mkdir(parents=True, exist_ok=True)
        self._write_meta()

        self._thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="dataset-writer"
        )
        self._thread.start()
        log.info("[dataset] collector ON → %s (vehicle 1/%.0fs)", self._root, self._interval)

    # ── metadata (written once) ───────────────────────────────────────────────

    def _write_meta(self) -> None:
        items = sorted(self._names.items())
        names_block = "".join(f"  {i}: {n}\n" for i, n in items)
        (self._root / "vehicle_detect" / "classes.txt").write_text(
            "\n".join(n for _, n in items) + "\n", encoding="utf-8")
        (self._root / "vehicle_detect" / "data.yaml").write_text(
            f"path: .\ntrain: images\nval: images\nnames:\n{names_block}", encoding="utf-8")
        (self._root / "plate_detect" / "classes.txt").write_text("plate\n", encoding="utf-8")
        (self._root / "plate_detect" / "data.yaml").write_text(
            "path: .\ntrain: images\nval: images\nnames:\n  0: plate\n", encoding="utf-8")

    # ── public hooks (called from the driver; enqueue-only, never block) ───────

    def save_vehicle_frame(self, cam_id: str, frame: np.ndarray, boxes, frame_idx: int) -> None:
        """Full frame + all detection boxes, throttled to 1 / interval_sec per camera.
        Skips frames with no detections."""
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_veh.get(cam_id, -1e9) < self._interval:
            return
        try:
            xyxy = boxes.xyxy.cpu().numpy()
            clss = boxes.cls.cpu().numpy().astype(int)
        except Exception:
            return
        if len(xyxy) == 0:
            return
        self._last_veh[cam_id] = now

        h, w = frame.shape[:2]
        rows = []
        for (x1, y1, x2, y2), c in zip(xyxy, clss):
            cx = ((x1 + x2) / 2.0) / w
            cy = ((y1 + y2) / 2.0) / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            rows.append(f"{int(c)} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        stem = f"{self._svc}_ch{cam_id}_{frame_idx}_{self._next_seq()}"
        self._put(("img_label",
                   str(self._veh_img / f"{stem}.jpg"),
                   str(self._veh_lbl / f"{stem}.txt"),
                   frame.copy(), "\n".join(rows) + "\n"))

    def save_plate_detect(self, cam_id: str, vcrop: np.ndarray,
                          plate_xyxy: tuple[int, int, int, int] | None, track_id: int) -> None:
        """Vehicle crop (the plate model's input) + the plate box, normalized to the crop.
        plate_xyxy=None → empty label = a negative example."""
        if not self.enabled or vcrop is None or not getattr(vcrop, "size", 0):
            return
        h, w = vcrop.shape[:2]
        label = ""
        if plate_xyxy is not None and w > 0 and h > 0:
            px1, py1, px2, py2 = plate_xyxy
            cx = ((px1 + px2) / 2.0) / w
            cy = ((py1 + py2) / 2.0) / h
            bw = (px2 - px1) / w
            bh = (py2 - py1) / h
            label = f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n"

        stem = f"{self._svc}_ch{cam_id}_t{track_id}_{self._next_seq()}"
        self._put(("img_label",
                   str(self._plate_img / f"{stem}.jpg"),
                   str(self._plate_lbl / f"{stem}.txt"),
                   vcrop.copy(), label))

    def save_plate_type(self, cam_id: str, plate_crop: np.ndarray, ptype: str, track_id: int) -> None:
        """Plate crop → tounes/ or other/ folder (classification dataset)."""
        if not self.enabled or plate_crop is None or not getattr(plate_crop, "size", 0):
            return
        cls = "tounes" if ptype == "tounes" else "other"
        stem = f"{self._svc}_ch{cam_id}_t{track_id}_{self._next_seq()}"
        self._put(("img", str(self._type_dir / cls / f"{stem}.jpg"), plate_crop.copy()))

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ── internals ──────────────────────────────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _put(self, item: tuple) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("[dataset] queue full, dropped %d items (writer can't keep up)",
                            self._dropped)

    def _writer_loop(self) -> None:
        params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_q]
        while not self._stop.is_set() or not self._q.empty():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if item[0] == "img_label":
                    _, img_path, lbl_path, img, label = item
                    cv2.imwrite(img_path, img, params)
                    Path(lbl_path).write_text(label, encoding="utf-8")   # label last → always has its image
                elif item[0] == "img":
                    _, img_path, img = item
                    cv2.imwrite(img_path, img, params)
            except Exception:
                log.debug("[dataset] write failed", exc_info=True)
            finally:
                self._q.task_done()
