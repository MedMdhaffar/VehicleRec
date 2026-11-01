# Vehicle AI Platform

Vehicle AI is a Django + React application for running computer-vision
services on vehicle footage. The backend exposes one API namespace per Django
app; the React frontend provides a single workspace for uploading footage,
viewing results, and monitoring a live RTSP camera.

## Repository layout

```text
vehicle/                         Django project and API apps
  manage.py
  vehicle/                       settings and root URL configuration
  api/                           shared API health endpoint
  license_plate_service/         license-plate API + pipeline packages
  vehicle_color_service/         vehicle-colour API + pipeline package
  vehicle_classification_manufacturer_service/
                                 vehicle classification API + pipeline package
services/                        standalone service source/packages and READMEs
frontend/                        React/Vite web application
```

The packages inside each Django app are the runtime copies used by the API.
The corresponding directories under `services/` document and preserve the
standalone command-line pipelines.

## Backend setup

Create or activate a Python 3.11 virtual environment, then install the backend
dependencies:

```bash
cd vehicle
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --no-deps --no-build-isolation -e ../services/license_plate_service/LicensePlatePlugin
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

The API is served at `http://127.0.0.1:8000`. Generated files are stored under
`vehicle/media/` in per-job directories.

Install the system `ffmpeg` executable with the `libx264` encoder for license
plate output videos. The API converts annotated videos and completed camera
recordings to H.264 with `yuv420p` pixels and fast-start MP4 metadata so they can
play in the browser. Standalone pipelines retain their OpenCV output format.

The computer-vision services require the packages listed in
`vehicle/requirements.txt`, model weights, and locally installed registration OCR assets. Other services retain their own configuration.

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

The frontend defaults to `http://127.0.0.1:8000`. Set `VITE_API_URL` when the
backend runs elsewhere:

```bash
VITE_API_URL=http://localhost:8000 npm run dev
```

## API services

All endpoints accept JSON responses and CORS requests from the Vite dev server.

| Service | Health | Analyze |
| --- | --- | --- |
| License plates | `GET /api/license-plate/health/` | `POST /api/license-plate/analyze/` |
| Vehicle colour | `GET /api/color/health/` | `POST /api/color/analyze/` |
| Classification/manufacturer | `GET /api/classification-manufacturer/health/` | `POST /api/classification-manufacturer/analyze/` |

Analysis requests use `multipart/form-data` with a `video` file. License-plate
and classification analysis also accept an optional positive `max_frames` field.
Responses contain recognized/tracked records and a link to the annotated
video. JSON/JSONL reports are available through the job artifact URLs returned
by each service.

The license-plate service additionally provides live camera controls:

```text
GET  /api/license-plate/rtsp/
POST /api/license-plate/rtsp/
POST /api/license-plate/rtsp/stop/
GET  /api/license-plate/rtsp/preview/
```

## Standalone pipelines

Each service can also be run independently. See the service-specific READMEs:

- `vehicle/license_plate_service/run_video_pipeline/README.md`
- `vehicle/license_plate_service/run_rtsp_pipeline/README.md`
- `vehicle/vehicle_color_service/README.md`
- `vehicle/vehicle_classification_manufacturer_service/README.md`

## Verification

Run the Django checks and test suite from `vehicle/`:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

Run frontend checks from `frontend/`:

```bash
npm run lint
npm run build
```

## Local plate-category runtime

Django and standalone video/RTSP require the same editable `license_plate_plugin`
package. The supplemental DINOv3 dependencies are included by
`vehicle/requirements.txt` through
`services/license_plate_service/requirements-classifier.txt`. Keep the complete
plugin directory, including its vendored implementation, model files and frozen
reference index. Neither runtime downloads or substitutes a classifier model.

Use one Python 3.11 environment for both entry points, or repeat dependency and
editable-package installation in each environment. For standalone execution with
the backend environment, from the repository root:

```bash
cd services/license_plate_service
../../vehicle/.venv/bin/python -m run_video_pipeline.run_video_prototype /absolute/video.mp4 --max-frames 60
../../vehicle/.venv/bin/python -m run_rtsp_pipeline.run_rtsp_prototype --url 'rtsp://camera-host/stream' --max-frames 60
```

The classifier is initialized on each runner's single recognition worker before
frames are processed. Selected-crop recognition and better-crop rechecks classify
before registration OCR. Classification evidence is stored separately in
`captures/prediction_debug.jsonl`; compact results retain their registration
category/text/confidence meanings. A classifier exception makes the attempt
unresolved and skips registration and external routing for that attempt.

A writable Torch/Paddle cache may be required even for local model loading. For
restricted hosts, point `TORCH_HOME`, `PADDLE_PDX_CACHE_HOME` and `MPLCONFIGDIR` to
writable cache directories. Paddle's PP-OCRv6 registration model must already be
cached for fully offline execution; the category plugin includes its own Arabic
OCR model. CPU thread limits can be set in the process environment for bounded
verification; they do not change model configuration.

License-plate recognition is fully local. `tunis` uses the existing conservative
private-plate validation. Whole-crop OCR now implements the user-specified
application formats for `idara` (`group1 - group2`), `nt` (`digits nt`) and
`shorta` (`shorta digits`). `WHOLE_PLATE_MIN_OCR_CONF` defaults to `0.90`,
a conservative starting value, not an independently calibrated threshold.
Unambiguous numeric fields, sufficient OCR confidence and no contradictory
category marker are required; classification supplies labels, never digits.
See [the policy report](docs/whole_plate_number_policies.md) for extraction rules,
real development-crop mismatches and rerun commands. No external
recognition route or manual approval is available. See
[the local completion audit](docs/license_plate_local_completion.md) for exact
capabilities, setup, queue behavior and verification limitations.

### Dedicated local recognition runtime

```bash
bash scripts/setup_local_plate_runtime.sh
export REGISTRATION_OCR_MODEL_DIR=/absolute/path/to/PP-OCRv6_medium_rec
bash scripts/run_local_plate.sh django check
bash scripts/run_local_plate.sh django runserver
bash scripts/run_local_plate.sh video /absolute/video.mp4 --max-frames 60
```

The default setup creates `.runtime/plates/venv` and installs backend dependencies
there. A host with an existing persistent CV environment may explicitly reuse its
site-packages with `PLATE_BASE_SITE_PACKAGES=/absolute/site-packages` and
`PLATE_PYTHON=/absolute/python`. The new environment installs supplemental packages
without upgrading the host environment. Runtime caches stay under `.runtime`.
Normal startup requires existing local weights; registration OCR never downloads
a model. Classifier and registration CPU/GPU selectors are `PLATE_CLASSIFIER_DEVICE`
and `PLATE_OCR_DEVICE`; CPU is the default, and GPU behavior requires host validation.
`PLATE_MAX_PENDING` bounds recognition tasks; see the audit for deferral semantics.

### Active plate detector

The license-plate pipeline now defaults to the local ONNX
`yolo-v9-t-640-license-plate-end2end`, not the archived `best.pt`.
Run `bash scripts/setup_local_plate_runtime.sh` for explicit provisioning, then
`bash scripts/run_local_plate.sh preflight` for offline model/provider validation.
Normal uploads/video/RTSP never download weights. CPU is the explicit default;
GPU requests must be supported or explicitly permit CPU fallback. Input is fixed
at 640; obsolete `DETECT_IMGSZ=960`/`--imgsz 960` fail clearly.
The Tunis YOLO marker model and unrelated vehicle detectors are unchanged.
See [the t-640 integration audit](docs/detector_t640_integration.md) for exact
configuration, local asset provenance, nine development-image results, commands,
limitations and rollback.
