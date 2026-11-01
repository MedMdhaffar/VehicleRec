"""Mocked adapter contracts plus actual library coordinate conversion; no accuracy claims."""
import hashlib
import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import test_classifier_runtime as runtime
from run_video_pipeline import config
from run_video_pipeline.onnx_detector import MODEL_ID, StrictSession
from run_video_pipeline.plate_detector import PlateDetector
from run_video_pipeline.run_video_prototype import TrackerDetections


def result(box, confidence=.9):
    return SimpleNamespace(bounding_box=SimpleNamespace(**dict(zip(('x1','y1','x2','y2'),box))),confidence=confidence,label='License Plate')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'t640.onnx';self.path.write_bytes(b'mocked exact checkpoint')
        self.session=Mock();self.session.get_providers.return_value=['CPUExecutionProvider']
        self.session.get_inputs.return_value=[SimpleNamespace(shape=[1,3,640,640])]
        self.native=SimpleNamespace(model=self.session,predict=Mock(return_value=[]))
        patches=[patch('run_video_pipeline.onnx_detector.MODEL_SHA256',hashlib.sha256(self.path.read_bytes()).hexdigest()),
                 patch('onnxruntime.get_available_providers',return_value=['CPUExecutionProvider']),
                 patch('open_image_models.create_detector',return_value=self.native),
                 patch.object(config,'PLATE_DETECTOR_PROVIDERS','CPUExecutionProvider'),
                 patch.object(config,'PLATE_DETECTOR_ALLOW_CPU_FALLBACK',False),
                 patch.object(config,'DETECT_IMGSZ',640),patch.object(config,'PLATE_CONF',.25)]
        self.factory=None
        for p in patches:
            value=p.start();self.addCleanup(p.stop)
            if self.factory is None and isinstance(value,Mock) and p.attribute=='create_detector':self.factory=value
        self.image=np.random.default_rng(20).integers(0,256,(400,800,3),dtype=np.uint8)

    def test_load_once_local_factory_fixed_size_and_native_bgr(self):
        detector=PlateDetector(str(self.path));before=self.image.copy()
        detector.predict(self.image);detector.predict(self.image);detector.load()
        self.factory.assert_called_once()
        args=self.factory.call_args
        self.assertEqual(args.args[0],self.path.resolve())
        self.assertEqual(args.kwargs['backend'],'yolo_v9')
        self.assertEqual(args.kwargs['conf_thresh'],.001)
        self.assertEqual(args.kwargs['providers'],['CPUExecutionProvider'])
        self.assertEqual(detector.metadata['model_id'],MODEL_ID)
        self.native.predict.assert_called_with(self.image)
        np.testing.assert_array_equal(self.image,before)
        self.assertTrue(detector.model_loaded)
        self.session.disable_fallback.assert_called_once()

    def test_confidence_multiple_boxes_clipping_invalid_and_safe_crops(self):
        self.native.predict.return_value=[result((-10,20,200,100),.912345678),result((400,200,900,500),.8),
            result((10,10,20,20),.2),result((20,20,10,30)),result((np.nan,1,3,4)),result((1,2,3,4),np.nan),result((1,2,3,4),None),result((1,2,3,4),1.1)]
        detector=PlateDetector(str(self.path));boxes=detector.predict(self.image)
        self.assertEqual(boxes,[(0,20,200,100,.912345678),(400,200,800,400,.8)])
        crops=detector.detect_and_crop(self.image)
        self.assertEqual([c.bbox for c in crops],[(0,20,200,100),(400,200,800,400)])
        np.testing.assert_array_equal(crops[0].crop,self.image[20:100,:200])
        crops[0].crop[:]=0;self.assertTrue(np.any(self.image[20:100,:200]))
        array=TrackerDetections.from_tuples(boxes)
        self.assertEqual(array.xyxy.shape,(2,4));self.assertEqual(array.conf.shape,(2,))
        self.assertEqual(array.xyxy.dtype,np.float32);self.assertEqual(array.conf.dtype,np.float32)
        np.testing.assert_allclose(array.conf,[.912345678,.8])
        self.assertEqual(len(detector.predict(self.image,conf=.9)),1)

    def test_empty_and_duplicate_contract(self):
        detector=PlateDetector(str(self.path));self.assertEqual(detector.predict(self.image),[])
        self.assertEqual(detector.detect_and_crop(self.image),[])
        array=TrackerDetections.from_tuples([])
        self.assertEqual(array.xyxy.shape,(0,4));self.assertEqual(array.conf.shape,(0,))
        self.native.predict.return_value=[result((20,20,200,100),.8),result((21,21,201,101),.9)]
        self.assertEqual(len(detector.predict(self.image)),2)
        self.assertEqual(len(detector.detect_and_crop(self.image)),1)

    def test_actual_library_preprocessing_and_inverse_mapping_is_not_applied_twice(self):
        from open_image_models.detection.core.yolo_v9.preprocess import preprocess
        from open_image_models.detection.core.yolo_v9.postprocess import convert_to_detection_result
        before=self.image.copy();tensor,ratio,padding=preprocess(self.image,(640,640))
        self.assertEqual(tensor.shape,(1,3,640,640));self.assertEqual(tensor.dtype,np.float32)
        np.testing.assert_array_equal(before,self.image)
        self.assertEqual(ratio,(.8,.8));self.assertEqual(padding,(0,160))
        raw=np.array([[0,80,200,240,280,0,.91]],dtype=np.float32)
        self.native.predict.return_value=convert_to_detection_result(raw,{0:'License Plate'},ratio,padding,.001)
        box=PlateDetector(str(self.path)).predict(self.image)[0]
        self.assertEqual(box[:4],(100,50,300,150))
        self.assertAlmostEqual(box[4],.91,places=6)

    def test_missing_wrong_assets_and_obsolete_sizes_fail_without_network(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')) as connect:
            with self.assertRaisesRegex(FileNotFoundError,'runtime downloads are disabled'):
                PlateDetector(str(self.path.with_name('missing.onnx'))).load()
            self.path.write_bytes(b'wrong t256 checkpoint')
            with self.assertRaisesRegex(ValueError,'exact t-640'):
                PlateDetector(str(self.path)).load()
            connect.assert_not_called()
        with self.assertRaisesRegex(ValueError,'fixed 640'):
            PlateDetector(str(self.path)).predict(self.image,imgsz=960)
        for confidence in (0,.0001,1.01,float('nan')):
            with self.assertRaises(ValueError):PlateDetector(str(self.path)).predict(self.image,conf=confidence)
        with self.assertRaises(ValueError):PlateDetector(str(self.path)).predict(np.zeros((10,10)))

    def test_requested_gpu_is_not_implicitly_cpu_and_explicit_fallback_is_reported(self):
        with patch.object(config,'PLATE_DETECTOR_PROVIDERS','CUDAExecutionProvider'):
            with self.assertRaisesRegex(RuntimeError,'providers unavailable'):
                PlateDetector(str(self.path)).load()
            with patch.object(config,'PLATE_DETECTOR_ALLOW_CPU_FALLBACK',True):
                detector=PlateDetector(str(self.path));detector.load()
                self.assertEqual(detector.metadata['actual_providers'],['CPUExecutionProvider'])
                self.assertTrue(detector.metadata['fallback_happened']);self.assertTrue(detector.metadata['warnings'])
        with patch('onnxruntime.get_available_providers',return_value=['CUDAExecutionProvider','CPUExecutionProvider']),patch.object(config,'PLATE_DETECTOR_PROVIDERS','CUDAExecutionProvider'):
            with self.assertRaisesRegex(RuntimeError,'fallback rejected'):
                PlateDetector(str(self.path)).load()

    def test_inference_failures_are_errors_even_when_library_swallows_exception(self):
        detector=PlateDetector(str(self.path));detector.load()
        self.session.run.side_effect=RuntimeError('native execution failed')
        def library_swallow(image):
            try:self.native.model.run([], {})
            except Exception:return []
        self.native.predict.side_effect=library_swallow
        with self.assertRaisesRegex(RuntimeError,'ONNX inference failed.*native execution failed'):
            detector.predict(self.image)
        self.native.predict.side_effect=ValueError('library preprocessing failed')
        with self.assertRaisesRegex(RuntimeError,'inference failed.*preprocessing'):
            detector.predict(self.image)

    def test_video_rtsp_modules_all_construct_new_adapter_and_fixed_defaults(self):
        for module in ('run_video_pipeline.plate_detector','run_rtsp_pipeline.plate_detector'):
            self.native.model=self.session
            cls=importlib.import_module(module).PlateDetector
            detector=cls(str(self.path));detector.load()
            self.assertEqual(detector.metadata['model_id'],MODEL_ID)
        for module in ('run_video_pipeline.config','run_rtsp_pipeline.config'):
            cfg=importlib.import_module(module)
            self.assertTrue(cfg.PLATE_DETECTOR_PATH.endswith('yolo-v9-t-640-license-plates-end2end.onnx'))
            self.assertEqual(cfg.DETECT_IMGSZ,640)
        for module in ('run_video_pipeline.run_video_prototype','run_rtsp_pipeline.run_video_prototype','run_rtsp_pipeline.run_rtsp_prototype'):
            entry=importlib.import_module(module)
            detector=entry.PlateDetector(str(self.path))
            self.assertEqual(detector.model_path,str(self.path))


class OwnerCleanupTests(unittest.TestCase):
    setUp=runtime.RuntimeTests.setUp
    make_pipeline=runtime.RuntimeTests.make_pipeline
    make_runner=runtime.RuntimeTests.make_runner

    def test_detector_initialization_failure_releases_worker_owned_recognition(self):
        from run_video_pipeline.run_video_prototype import VideoPrototypeRunner,CountingReader
        reader=CountingReader(self.reader);pipeline=self.make_pipeline();pipeline.reader=reader
        detector=Mock();detector.load.side_effect=FileNotFoundError('Local t-640 weights missing')
        with self.assertRaisesRegex(FileNotFoundError,'t-640 weights missing'):
            VideoPrototypeRunner(pipeline,detector,Mock(),reader)
        self.assertIsNone(pipeline.classifier)
        self.assertIsNone(reader.delegate)

    def test_rtsp_provider_failure_closes_camera_and_prevents_frame_inference(self):
        import threading
        rtsp=importlib.import_module('run_rtsp_pipeline.run_rtsp_prototype')
        camera=Mock(connection_generation=1,source_fps=25)
        camera.ensure_connected.return_value=True
        args=SimpleNamespace(url='rtsp://example/stream',read_timeout=5,reconnect_delay=1,max_reconnects=0,
                             camera_id='test',capture_root=Path(self.tmp.name)/'rtsp',tracker_config=rtsp.DEFAULT_TRACKER)
        detector=Mock();detector.load.side_effect=RuntimeError('ONNX provider fallback rejected')
        with patch.object(rtsp,'RTSPFrameReader',return_value=camera),patch.object(rtsp,'CountingReader',return_value=runtime.CountingReader(self.reader)),patch.object(rtsp,'create_classifier',return_value=self.classifier),patch.object(rtsp,'TounesMarkerLocalizer',return_value=self.marker),patch.object(rtsp,'PlateDetector',return_value=detector),patch.object(rtsp,'_build_tracker',return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError,'provider fallback rejected'):
                rtsp.run_rtsp(args,threading.Event())
        camera.close.assert_called_once();camera.read.assert_not_called();detector.predict.assert_not_called()
