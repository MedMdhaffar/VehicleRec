import hashlib
import importlib
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .service import _ensure_pipeline_import_path


class GPUDefaultsTests(SimpleTestCase):
    def setUp(self):
        _ensure_pipeline_import_path()

    def test_detection_defaults_to_cuda_and_preserves_cpu_override(self):
        root = Path(__file__).parent
        for package in ('run_video_pipeline', 'run_rtsp_pipeline'):
            path = root / package / 'config.py'
            with patch.dict(os.environ, {}, clear=True):
                values = runpy.run_path(str(path))
                self.assertEqual(values['PLATE_DETECTOR_PROVIDERS'], 'CUDAExecutionProvider')
                self.assertFalse(values['PLATE_DETECTOR_ALLOW_CPU_FALLBACK'])
            with patch.dict(os.environ, {'PLATE_DETECTOR_PROVIDERS': 'CPUExecutionProvider'}):
                self.assertEqual(runpy.run_path(str(path))['PLATE_DETECTOR_PROVIDERS'], 'CPUExecutionProvider')

    def test_both_registration_readers_default_to_gpu_and_honor_override(self):
        for package in ('run_video_pipeline', 'run_rtsp_pipeline'):
            module = importlib.import_module(f'{package}.ocr')
            for override, expected in ((None, 'gpu:0'), ('cpu', 'cpu')):
                engine = Mock()
                with patch.dict(sys.modules, {'paddleocr': SimpleNamespace(TextRecognition=engine)}), patch(
                    'pathlib.Path.is_file', return_value=True
                ), patch('pathlib.Path.read_text', return_value='Global:\n  model_name: PP-OCRv6_medium_rec\n'), patch.dict(os.environ, {'REGISTRATION_OCR_MODEL_DIR': '/tmp/mock-registration-ocr'}):
                    os.environ.pop('PLATE_OCR_DEVICE', None)
                    if override:
                        os.environ['PLATE_OCR_DEVICE'] = override
                    module.PlateReader()
                    self.assertEqual(engine.call_args.kwargs['device'], expected)

    def test_classifier_config_defaults_and_bundled_config_use_gpu(self):
        from run_video_pipeline.license_plate_plugin.config import load_config, load_default_config
        bundled = load_default_config()
        self.assertEqual((bundled.device, bundled.ocr_device), ('cuda:0', 'gpu:0'))
        payload = {'dinov3_repo': 'vendor', 'dinov3_checkpoint': 'weights', 'paddle_model_dir': 'ocr',
                   'reference_manifest': 'manifest.json', 'reference_index': 'index.npz'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(payload))
            config = load_config(path)
            self.assertEqual((config.device, config.ocr_device), ('cuda:0', 'gpu:0'))
            path.write_text(json.dumps({**payload, 'device': 'cpu', 'ocr_device': 'cpu'}))
            config = load_config(path)
            self.assertEqual((config.device, config.ocr_device), ('cpu', 'cpu'))

    def test_both_marker_detectors_explicitly_request_gpu(self):
        import numpy as np
        for package in ('run_video_pipeline', 'run_rtsp_pipeline'):
            module = importlib.import_module(f'{package}.marker_localizer')
            marker = module.TounesMarkerLocalizer()
            marker._model = Mock()
            marker._model.predict.return_value = []
            marker.locate(np.zeros((20, 120, 3), dtype=np.uint8))
            self.assertEqual(marker._model.predict.call_args.kwargs['device'], 0)

    def test_onnx_preloads_before_cuda_session_and_rejects_unavailable_gpu(self):
        from run_video_pipeline import config
        from run_video_pipeline.onnx_detector import LocalPlateBackend
        for requested in ('CUDAExecutionProvider', 'CPUExecutionProvider'):
            ort = Mock(__version__='1.25.1')
            ort.get_available_providers.return_value = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            session = Mock()
            session.get_providers.return_value = [requested]
            session.get_inputs.return_value = [SimpleNamespace(shape=[1, 3, 640, 640])]
            def create_detector(*args, **kwargs):
                self.assertEqual(ort.preload_dlls.called, requested == 'CUDAExecutionProvider')
                return SimpleNamespace(model=session)
            factory = Mock(side_effect=create_detector)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'plate.onnx'
                path.write_bytes(b'test weights')
                with patch.dict(sys.modules, {'onnxruntime': ort, 'open_image_models': SimpleNamespace(create_detector=factory)}), patch(
                    'importlib.metadata.version', return_value='0.6.0'
                ), patch('run_video_pipeline.onnx_detector.MODEL_SHA256', hashlib.sha256(path.read_bytes()).hexdigest()), patch.object(
                    config, 'PLATE_DETECTOR_PROVIDERS', requested
                ), patch.object(config, 'PLATE_DETECTOR_ALLOW_CPU_FALLBACK', False):
                    backend = LocalPlateBackend(path)
                    backend.load()
                    self.assertFalse(backend.metadata['fallback_happened'])
                    session.disable_fallback.assert_called_once()
                    if requested == 'CUDAExecutionProvider':
                        ort.get_available_providers.return_value = ['CPUExecutionProvider']
                        factory.reset_mock()
                        with self.assertRaisesRegex(RuntimeError, 'providers unavailable'):
                            LocalPlateBackend(path).load()
                        factory.assert_not_called()
