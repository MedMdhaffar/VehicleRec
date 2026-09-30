# Vehicle classification and manufacturer service

This Django app exposes the standalone vehicle classification pipeline through
the project API. It detects and tracks vehicles, classifies their type, applies
the configured ROI and lifecycle rules, and produces an annotated video plus a
JSON report.

## API

Check service health:

```http
GET /api/classification-manufacturer/health/
```

Analyze a video:

```http
POST /api/classification-manufacturer/analyze/
Content-Type: multipart/form-data

video=<video file>
max_frames=<optional positive integer>
```

The request is synchronous. The response contains `frames_processed`,
`tracks_detected`, `vehicles`, performance details, and a `job_id`.

## Generated artifacts

The response includes links to the generated files:

```text
/api/classification-manufacturer/jobs/<job_id>/annotated.mp4/
/api/classification-manufacturer/jobs/<job_id>/results.json/
```

Files are stored under Django's `MEDIA_ROOT/vehicle_classification/<job_id>`
directory.

## Standalone pipeline

The original command-line service remains available from the app directory:

```bash
cd /home/intern/pipeline/vehicle/vehicle_classification_manufacturer_service
python -m vehicle_classification_manufacturer_service \
  --video /absolute/path/to/video.mp4 \
  --device auto \
  --output debug/vehicle_output.mp4 \
  --json-output debug/vehicle_results.json
```

The Django endpoint invokes the same CLI service and does not duplicate its
detector or tracking logic.
