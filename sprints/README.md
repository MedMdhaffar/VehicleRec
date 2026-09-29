# Sprints — vehicle_services

Detailed sprint docs (see `../ROADMAP.md`).
**Phase A — vehicle_classification** (fast win #1): detection + type + colour. ✅ done & validated.
**Phase B — plate_recognition / ANPR** (fast win #2): plate detect + digit OCR + type. Components built; assembly = Sprint 5.

| Sprint | Title | Goal | Depends on |
|--------|-------|------|------------|
| **0** | [Scaffold](SPRINT_0_scaffold.md) | Folder structure, `common/` building blocks, FastAPI skeleton + `/health`, result envelope | — |
| **1** | [Vehicle model (training)](SPRINT_1_vehicle_model.md) | Fine-tune yolo26 on streetdet; evaluate on val + KICT cross-domain; deliver weights | 0 |
| **2** | [Classification service](SPRINT_2_classification_service.md) | Wire detector into `/classify` + `/classify_crop`; return type + confidence + annotated image | 0, 1 |
| **3** | [Colour](SPRINT_3_colour.md) | HSV-baseline colour per vehicle, integrated into the response | 2 |
| **4** | *(skipped — no doc)* | ANPR components built ad-hoc (plate detector + OCR + word-spotting type); captured in Sprint 5 §1 | — |
| **5** | [ANPR assembly](SPRINT_5_anpr_assembly.md) | Wire plate detector + PaddleOCR digits + تونس word-spotting into `plate_recognition` `/read_plate` | 0, components |

**Live Feed — real-time perception + ANPR linking** (precursor to Phase C; validates latency before adding analytics rules). VMS main stream @15 FPS, classify-once-per-track, per-camera process (+ NVIDIA MPS for multi-camera).

| Sprint | Title | Goal | Depends on |
|--------|-------|------|------------|
| **LF-1** | [Stream core + perf](SPRINT_LF1_stream_core.md) | Headless pipeline: VMS → grab-latest @15fps → detect+track → classify-once → plate cascade (gated OCR) → enriched profiles + perf instrumentation | A, 5 |
| **LF-2** | [Serving (MJPEG + WS)](SPRINT_LF2_serving.md) | Annotated MJPEG stream + WebSocket profiles feed + start/stop, per-camera process | LF-1 |
| **LF-3** | [Vite frontend](SPRINT_LF3_frontend.md) | Minimal dashboard: large live video + live profiles table + metrics strip | LF-2 |

**Port plan** (avoids existing services 5001/5003/5004/5005/5008/5011/5100/7870):
- `vehicle_classification` → **5020**
- `plate_recognition` → 5021 (Phase B)
- `traffic_analytics` → 5022 (Phase C)

**Datasets:**
- Primary (training): `vehicle_services/vehicle_class1/` — streetdet, YOLO format, 9 classes incl. van.
- Cross-domain test: KICT at `C:\Users\malek\Desktop\vehicle_cllass` (PASCAL VOC), used bus/truck-only via remap (Sprint 1).
