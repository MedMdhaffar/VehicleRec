"""Read-only inspection of local license-plate job artifacts."""
import json
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response


def _job(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise Http404
    root = Path(settings.MEDIA_ROOT) / 'license_plate'
    path = root / job_id
    if not path.is_dir() or not path.resolve().is_relative_to(root.resolve()):
        raise Http404
    return path


def _records(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # A running worker may still be appending its last record.
    return rows


def _summary(path):
    summary = path / 'summary.json'
    return json.loads(summary.read_text(encoding='utf-8')) if summary.is_file() else {}


@api_view(['GET'])
def jobs(request):
    root = Path(settings.MEDIA_ROOT) / 'license_plate'
    rows = []
    if root.is_dir():
        for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', path.name):
                continue
            if path.is_symlink():
                continue
            if not (path / 'captures' / 'predictions.jsonl').is_file() and not (path / 'summary.json').is_file():
                continue
            summary = _summary(path)
            rows.append({'job_id': path.name, 'name': summary.get('input_name', f'Run {path.name[:8]}'),
                         'created_at': path.stat().st_mtime, 'frames': summary.get('frames'),
                         'recognized_tracks': summary.get('recognized_tracks')})
    return Response(rows)


@api_view(['GET'])
def job_detail(request, job_id):
    path = _job(job_id)
    summary = _summary(path)
    predictions = _records(path / 'captures' / 'predictions.jsonl')
    debug = {row['sample_id']: row for row in _records(path / 'captures' / 'prediction_debug.jsonl')
             if row.get('record_type') == 'prediction_debug' and row.get('sample_id')}
    tracks = {str(key): dict(value) for key, value in summary.get('tracks', {}).items()}
    for prediction in predictions:
        key = str(prediction.get('track_id', 'unknown'))
        row = tracks.setdefault(key, dict(prediction))
        if not summary.get('tracks'):
            row.update(prediction)
        row.setdefault('attempts', []).append({**prediction, 'debug': debug.get(prediction.get('sample_id'), {})})
    for key, row in tracks.items():
        row['track_id'] = key
        row.setdefault('attempts', [])
        crops = {}
        for label in ('early', 'recognized', 'final_best'):
            filename = f'track_{key}_{label}.jpg'
            if re.fullmatch(r'track_[0-9]+_(early|recognized|final_best)\.jpg', filename) and (path / 'selected_crops' / filename).is_file():
                crops[label] = f'/api/license-plate/jobs/{job_id}/debug/crops/{filename}/'
        row['crops'] = crops
        for attempt in row['attempts']:
            sample = str(attempt.get('sample_id', ''))
            if re.fullmatch(r'[0-9a-f-]{36}', sample) and (path / 'captures' / 'crops' / f'{sample}.jpg').is_file():
                attempt['crop_url'] = f'/api/license-plate/jobs/{job_id}/debug/crops/{sample}.jpg/'
    summary.update({'job_id': job_id, 'tracks': tracks,
                    'has_metrics': (path / 'summary.json').is_file()})
    if (path / 'annotated.mp4').is_file():
        summary['output_video_url'] = f'/api/license-plate/jobs/{job_id}/annotated.mp4/'
    return Response(summary)


@api_view(['GET'])
def crop(request, job_id, filename):
    job = _job(job_id)
    if re.fullmatch(r'track_[0-9]+_(early|recognized|final_best)\.jpg', filename):
        path = job / 'selected_crops' / filename
    elif re.fullmatch(r'[0-9a-f-]{36}\.jpg', filename):
        path = job / 'captures' / 'crops' / filename
    else:
        raise Http404
    if not path.is_file() or not path.resolve().is_relative_to(job.resolve()):
        raise Http404
    return FileResponse(path.open('rb'), content_type='image/jpeg')
