"""Offline t-640 detector owner, using open-image-models' public local-file API."""
from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
import time
import warnings

import run_video_pipeline.config as config

log = logging.getLogger(__name__)
MODEL_ID = 'yolo-v9-t-640-license-plate-end2end'
MODEL_FILENAME = 'yolo-v9-t-640-license-plates-end2end.onnx'
MODEL_SHA256 = 'c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076'
MODEL_URL = 'https://github.com/ankandrew/open-image-models/releases/download/assets/' + MODEL_FILENAME


def validate_options(conf=None, imgsz=None):
    threshold = config.PLATE_CONF if conf is None else conf
    if not math.isfinite(threshold) or not .001 <= threshold <= 1:
        raise ValueError('PLATE_CONF/conf must be between 0.001 (embedded ONNX floor) and 1')
    size = config.DETECT_IMGSZ if imgsz is None else imgsz
    if size != 640:
        raise ValueError('t-640 has fixed 640x640 input; DETECT_IMGSZ/--imgsz must be 640 (obsolete arbitrary YOLO sizes are unsupported)')
    return float(threshold)


class StrictSession:
    """Observe native errors that open-image-models 0.6.0 otherwise swallows."""
    def __init__(self, session):
        self.session = session
        self.error = None
        self.capture_outputs = False
        self.last_outputs = None

    def __getattr__(self, name):
        return getattr(self.session, name)

    def run(self, *args, **kwargs):
        self.error = None
        try:
            result = self.session.run(*args, **kwargs)
            if self.capture_outputs:
                self.last_outputs = [output.copy() for output in result]
            return result
        except Exception as exc:
            self.error = exc
            raise


class LocalPlateBackend:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.detector = None
        self.metadata = {}

    def load(self):
        if self.detector is not None:
            return
        validate_options()
        if not self.path.is_file():
            raise FileNotFoundError(f'Local {MODEL_ID} weights missing: {self.path}. Run scripts/provision_plate_detector.py --download during setup; runtime downloads are disabled.')
        actual_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if actual_hash != MODEL_SHA256:
            raise ValueError(f'Wrong plate checkpoint SHA-256 at {self.path}: expected exact t-640 {MODEL_SHA256}, got {actual_hash}')
        import onnxruntime as ort
        from open_image_models import create_detector
        from importlib.metadata import version
        package_version = version("open-image-models")
        if package_version != "0.6.0":
            raise RuntimeError(f"Expected open-image-models 0.6.0, found {package_version}; run the documented detector setup")
        requested = [p.strip() for p in config.PLATE_DETECTOR_PROVIDERS.split(',') if p.strip()]
        if not requested:
            raise ValueError('PLATE_DETECTOR_PROVIDERS must specify at least one provider')
        available = ort.get_available_providers()
        fallback = config.PLATE_DETECTOR_ALLOW_CPU_FALLBACK
        missing = [p for p in requested if p not in available]
        if missing and (not fallback or 'CPUExecutionProvider' not in available):
            raise RuntimeError(f'Requested ONNX providers unavailable: {missing}; available={available}. Explicit CPU fallback is disabled.')
        effective = [p for p in requested if p in available]
        if fallback and 'CPUExecutionProvider' not in effective:
            effective.append('CPUExecutionProvider')
        options = ort.SessionOptions()
        options.intra_op_num_threads = config.PLATE_DETECTOR_INTRA_OP_THREADS
        options.inter_op_num_threads = config.PLATE_DETECTOR_INTER_OP_THREADS
        if options.intra_op_num_threads < 1 or options.inter_op_num_threads < 1:
            raise ValueError('Plate detector thread counts must be positive')
        started = time.perf_counter()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            # A Path plus explicit backend/labels bypasses the registered-model download branch.
            detector = create_detector(self.path, backend='yolo_v9', class_labels=('License Plate',),
                                       conf_thresh=.001, batch_size=1, providers=effective, sess_options=options)
        session = detector.model
        actual = session.get_providers()
        fallback_happened = requested[0] not in actual
        if fallback_happened and not fallback:
            raise RuntimeError(f'ONNX provider fallback rejected: requested={requested}, actual={actual}. Set PLATE_DETECTOR_ALLOW_CPU_FALLBACK=1 only to explicitly allow CPU fallback.')
        if session.get_inputs()[0].shape != [1, 3, 640, 640]:
            raise ValueError(f'Unexpected t-640 ONNX input shape: {session.get_inputs()[0].shape}')
        # Disallow ORT's implicit run-time EP failure retry unless explicitly authorized.
        if not fallback:
            session.disable_fallback()
        detector.model = StrictSession(session)
        self.metadata = dict(model_id=MODEL_ID, checkpoint=str(self.path), sha256=actual_hash,
                             open_image_models_version='0.6.0', onnxruntime_version=ort.__version__,
                             requested_providers=requested, configured_providers=effective,
                             actual_providers=actual, available_providers=available,
                             cpu_fallback_allowed=fallback, fallback_happened=fallback_happened,
                             warnings=[str(w.message) for w in captured], precision='float32',
                             input_shape=[1, 3, 640, 640], embedded_score_floor=.001, embedded_nms_iou=.45,
                             library_output_threshold=.001, application_confidence=config.PLATE_CONF,
                             application_dedup_iou=config.PLATE_DEDUP_IOU,
                             intra_op_threads=options.intra_op_num_threads, inter_op_threads=options.inter_op_num_threads,
                             initialization_seconds=time.perf_counter() - started)
        if missing or fallback_happened:
            message=f'Explicit ONNX CPU fallback: requested={requested}, available={available}, actual={actual}'
            self.metadata['warnings'].append(message)
            log.warning(message)
        for message in self.metadata['warnings']:
            log.warning('Plate detector initialization: %s', message)
        self.detector = detector
        log.info('Loaded %s from %s; requested=%s actual=%s', MODEL_ID, self.path, requested, actual)

    def predict(self, image):
        if self.detector is None:
            raise RuntimeError('Plate detector is not initialized')
        self.detector.model.error = None
        try:
            results = self.detector.predict(image)
        except Exception as exc:
            raise RuntimeError(f'{MODEL_ID} inference failed: {exc}') from exc
        error = self.detector.model.error
        if error is not None:
            raise RuntimeError(f'{MODEL_ID} ONNX inference failed: {error}') from error
        self.last_results = results
        return results
