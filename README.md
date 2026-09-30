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
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

The API is served at `http://127.0.0.1:8000`. Generated files are stored under
`vehicle/media/` in per-job directories.

The computer-vision services require the packages listed in
`vehicle/requirements.txt`, model weights, and (for optional agent fallbacks)
the relevant OpenRouter API key environment variables.

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
