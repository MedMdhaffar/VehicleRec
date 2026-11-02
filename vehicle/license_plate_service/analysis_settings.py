"""Editable, validated settings for one uploaded-video analysis."""
import json
import math
import os
from types import SimpleNamespace


# Defaults are read from the running pipeline, not duplicated in the frontend.
CONFIG_FIELDS = (
    ('PLATE_CONF', 'Detection', 'Plate confidence', 'Discard detections below this score.', .001, 1),
    ('PLATE_DEDUP_IOU', 'Detection', 'Duplicate overlap', 'IoU threshold for removing duplicate boxes.', 0, 1),
    ('MARKER_INFERENCE_CONF', 'Marker', 'Marker detection confidence', 'Minimum score for the Tunis marker detector.', 0, 1),
    ('MARKER_RELIABLE_CONF', 'Marker', 'Reliable marker confidence', 'Minimum score before the marker can resolve ambiguous digits.', 0, 1),
    ('MARKER_REGION_OCR_CONF', 'Marker', 'Region OCR confidence', 'Required OCR confidence for left and right digit regions.', 0, 1),
    ('PRIVATE_ROUTE_MIN_OCR_CONF', 'Number validation', 'Private plate OCR confidence', 'Minimum OCR confidence to accept a private plate number.', 0, 1),
    ('PRIVATE_ROUTE_MIN_MARKER_CONF', 'Number validation', 'Private plate marker confidence', 'Minimum marker confidence to accept a private plate number.', 0, 1),
    ('WHOLE_PLATE_MIN_OCR_CONF', 'Number validation', 'Whole plate OCR confidence', 'Minimum OCR confidence for administrative, temporary and police plates.', 0, 1),
)
RUNNER_FIELDS = (
    ('association_padding', 'Tracking', 'Association padding', 'Expand boxes for track association; OCR crops stay unchanged.', 0, 1, 'float'),
    ('strong_quality', 'Crop selection', 'Strong crop quality', 'Quality score that can trigger early recognition.', 0, 1e9, 'float'),
    ('strong_min_candidates', 'Crop selection', 'Early candidate count', 'Minimum observations before early recognition.', 1, 10000, 'int'),
    ('min_candidates', 'Crop selection', 'Stable candidate count', 'Minimum observations for stable-crop recognition.', 1, 10000, 'int'),
    ('stable_observations', 'Crop selection', 'Stable observations', 'Observations without a better crop before recognition.', 1, 10000, 'int'),
    ('disappearance_frames', 'Crop selection', 'Disappearance frames', 'Frames missing before an eligible track is recognized.', 1, 10000, 'int'),
    ('minimum_exit_candidates', 'Crop selection', 'Exit candidate count', 'Minimum observations to recognize a disappearing or ending track.', 1, 10000, 'int'),
    ('retry_improvement', 'Rechecks', 'Recheck quality multiplier', 'Required quality gain over the first recognition crop.', 1.000001, 100, 'float'),
    ('recheck_detail_improvement', 'Rechecks', 'Recheck detail multiplier', 'Required area or sharpness gain for a recheck.', 1.000001, 100, 'float'),
    ('replacement_confidence_gain', 'Rechecks', 'Replacement confidence gain', 'Confidence improvement required to replace a cached result.', 0, 1, 'float'),
    ('max_pending', 'Rechecks', 'Recognition queue capacity', 'Maximum queued recognition tasks.', 1, 32, 'int'),
)
TRACKER_FIELDS = (
    ('track_high_thresh', 'High tracking confidence', 'Minimum score for the first association pass.', 0, 1, 'float'),
    ('track_low_thresh', 'Low tracking confidence', 'Minimum score for the second association pass.', 0, 1, 'float'),
    ('new_track_thresh', 'New track confidence', 'Minimum score to start a new track.', 0, 1, 'float'),
    ('track_buffer', 'Lost track buffer', 'How long lost tracks are retained, scaled by source FPS.', 1, 10000, 'int'),
    ('match_thresh', 'Tracking match threshold', 'Maximum association cost accepted by ByteTrack.', 0, 1, 'float'),
)


def settings_schema():
    from .service import _ensure_pipeline_import_path
    _ensure_pipeline_import_path()
    from run_video_pipeline import config
    from run_video_pipeline.run_video_prototype import RUNNER_DEFAULTS, DEFAULT_TRACKER
    import yaml

    fields = []
    for key, group, label, help_text, minimum, maximum in CONFIG_FIELDS:
        fields.append(dict(key=key, group=group, label=label, help=help_text,
                           default=getattr(config, key), min=minimum, max=maximum, type='float'))
    defaults = RUNNER_DEFAULTS
    for key, group, label, help_text, minimum, maximum, kind in RUNNER_FIELDS:
        value = int(os.environ.get('PLATE_MAX_PENDING', '4')) if key == 'max_pending' else defaults[key]
        fields.append(dict(key=key, group=group, label=label, help=help_text,
                           default=value, min=minimum, max=maximum, type=kind))
    tracker = yaml.safe_load(DEFAULT_TRACKER.read_text())
    for key, label, help_text, minimum, maximum, kind in TRACKER_FIELDS:
        fields.append(dict(key=key, group='Tracking', label=label, help=help_text,
                           default=tracker[key], min=minimum, max=maximum, type=kind))
    return {'fields': fields, 'fixed': [
        {'label': 'Plate detector input', 'value': '640 × 640', 'reason': 'Fixed ONNX model input.'},
        {'label': 'Marker detector input', 'value': f'{config.WORD_IMGSZ} × {config.WORD_IMGSZ}', 'reason': 'Required training preprocessing.'},
        {'label': 'DINO representation', 'value': 'R2 · 1536 features', 'reason': 'Preprocessing must match the saved reference index.'},
    ]}


def parse_settings(payload=None):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError('Analysis settings must be valid JSON.') from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError('Analysis settings must be an object.')
    fields = {field['key']: field for field in settings_schema()['fields']}
    unknown = set(payload) - fields.keys()
    if unknown:
        raise ValueError(f'Unknown analysis settings: {", ".join(sorted(unknown))}')
    values = {key: field['default'] for key, field in fields.items()}
    for key, value in payload.items():
        field = fields[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{field["label"]} must be a finite number.')
        if field['type'] == 'int' and value != int(value):
            raise ValueError(f'{field["label"]} must be a whole number.')
        if not field['min'] <= value <= field['max']:
            raise ValueError(f'{field["label"]} must be between {field["min"]} and {field["max"]}.')
        values[key] = int(value) if field['type'] == 'int' else float(value)
    if values['track_low_thresh'] > values['track_high_thresh']:
        raise ValueError('Low tracking confidence cannot exceed high tracking confidence.')
    return values


def pipeline_config(values):
    from run_video_pipeline import config
    settings = {key: value for key, value in vars(config).items() if key.isupper()}
    settings.update({key: values[key] for key, *_ in CONFIG_FIELDS})
    return SimpleNamespace(**settings)
