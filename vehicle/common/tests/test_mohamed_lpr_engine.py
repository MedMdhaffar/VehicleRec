"""Synthetic Engine bindings and existing T1 frame/crop ownership; no GPU/media."""
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from vehicle.common.engine_plate import CropBinding, native_access
from vehicle.common.plate_crop_smoke import EngineCrop
from vehicle.common.plate_metadata import PlateCandidate
from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
from vehicle.common.observation_evidence import RecognitionIdentity, VehicleIdentity
from vehicle.common.real_video.input import DecodedFrame
from vehicle.common.real_video.plate_engine import CombinedEngine, VideoPlateLimits
from vehicle.common.real_video.plate_local import PlateDetections, crop_in_frame
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_mp4_colour_plate import Vehicles, Plates
from vehicle.common.mohamed_lpr_contract import Provenance, ReferenceIndex, Request, decode, encode
from vehicle.common.mohamed_lpr_harness import Harness
from vehicle.common.mohamed_lpr_engine import BindingError, RetirementPending, V3EngineAdapter, bind_crop
from vehicle.common.mohamed_lpr_native import Assets, CudaModels, cuda_preflight, source_detector_config, source_plugin
from vehicle.common.mohamed_plate_policy import ReferenceScore


class Runtime:
    def __init__(self, category="nt", text="0007"):
        self.category, self.text = category, text
        self.calls, self.images = Counter(), {}
        self.defer = self.fail = self.bad_identity = self.malformed = False
        self.ctx = None
        self.index_identity = ReferenceIndex("synthetic-index", "f" * 64, (("1", "idara"), ("2", "shorta"), ("3", "tunis")))

    def expected(self, role):
        return Provenance("synthetic-" + role, "a" * 64, "b" * 64, "c" * 64, "runtime", "injected")

    def verify(self, role):
        pass

    def invoke(self, role, image, context):
        self.ctx = context
        try:
            actual = self.expected(role)
            context.started(replace(actual, device="wrong") if self.bad_identity else actual)
            self.calls[role] += 1
            self.images[role] = image.copy()
            image[:] = 0  # A mutating library cannot alter the original allocation.
            if self.fail:
                raise RuntimeError("synthetic_execution_failure")
            if self.malformed:
                return dict(raw_text="invented", confidence=float("nan"))
            if role == "visual":
                return dict(reference_index=self.index_identity, scores=tuple(
                    ReferenceScore(i, c, .8 if c == self.category else -.8) for i, c in self.index_identity.entries))
            if role == "marker":
                return dict(detected=True, confidence=.98, bbox=(40, 0, 60, 30))
            text = {"category_ocr": "ن ت" if self.category == "nt" else "", "registration": self.text,
                    "left": "001", "right": "0007"}[role]
            return dict(raw_text=text, confidence=.97)
        finally:
            if not self.defer:
                context.retired()


class EngineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.owner, _, _ = fixture()
        self.life = self.owner.lifetime
        self.receipt = self.life.first_receipt
        self.image = np.arange(30 * 100 * 3, dtype=np.uint8).reshape(30, 100, 3)
        self.resources = []
        self.adapters = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for adapter in self.adapters:
            if adapter.active is not None:
                adapter.active["context"].retired()
                adapter.poll_retirement()
            adapter.release(discard=True)
        for resource in self.resources:
            resource.release()

    def bound(self, **changes):
        c = PlateCandidate(VehicleIdentity.from_lifetime(self.life), "candidate", 1, 37, self.receipt, "associated", False,
            "private-hash", sha256(self.image.tobytes()).hexdigest(), (20, 40, 120, 70), (100, 30), .9, .9, "t1-quality")
        c = replace(c, **changes)
        resource = EngineCrop(CropBinding(c, "private-handle", self.image.nbytes), self.image.tobytes())
        self.resources.append(resource)
        return bind_crop(resource, self.life, plate_epoch=4, frame_receipt=self.receipt, frame_sha256="d" * 64)

    def adapter(self, category="nt", text="0007", mode="legacy_whole"):
        bound = self.bound()
        harness = Harness(bound.scalar, tunis_mode=mode)
        rt = Runtime(category, text)
        adapter = V3EngineAdapter(rt, bound, harness)
        self.adapters.append(adapter)
        return adapter, harness, rt

    def test_actual_t1_binding_fields_keep_plate_and_vehicle_identity_distinct(self):
        b = self.bound()
        self.assertEqual((b.scalar.plate_track_id, b.scalar.plate_track_epoch), ("37", "4"))
        self.assertNotEqual(b.scalar.vehicle_lifetime_id, b.scalar.plate_track_id)
        self.assertIs(b.receipt, self.receipt)
        self.assertEqual(b.original_bbox, (20, 40, 120, 70))
        self.assertEqual(b.scalar.frame_sha256, "d" * 64)

    def test_cross_vehicle_wrong_receipt_and_unassociated_crops_rejected(self):
        for changes in (dict(identity=replace(VehicleIdentity.from_lifetime(self.life), service_track_id=999)),
                        dict(association="ambiguous"), dict(association="unassociated"),
                        dict(receipt=replace(self.receipt)), dict(crop_sha256="0" * 64)):
            with self.subTest(keys=tuple(changes)), self.assertRaises(ValueError):
                self.bound(**changes)

    def test_pixel_mutation_cannot_change_original_crop(self):
        adapter, h, rt = self.adapter()
        r, = h.advance()
        original = adapter.bound.resource.read()
        result = adapter.execute(r)
        self.assertEqual(adapter.bound.resource.read(), original)
        np.testing.assert_array_equal(rt.images["category_ocr"], self.image)
        self.assertEqual(result.provenance, rt.expected("category_ocr"))

    def test_execution_retry_is_cached_and_wire_roundtrip_does_not_execute(self):
        a, h, rt = self.adapter()
        seal = a.drive(deadline=time.monotonic() + 5)
        r = h.engine.current_requests[-1]
        before = dict(rt.calls)
        original = a.execute(r)
        for _ in range(3):
            self.assertEqual(a.execute(decode(encode(r))), original)
            h.inject(decode(encode(original)))
        self.assertEqual(rt.calls, before)
        self.assertIs(h.seal(), seal)
        a.release()
        a.release()
        self.assertEqual(a.bound.resource.release_calls, 1)

    def test_all_four_category_paths_and_both_modes(self):
        for mode in ("legacy_whole", "marker_first"):
            for category, text in (("nt", "0007"), ("shorta", "0007"), ("idara", "001 - 002"), ("tunis", "001T0007")):
                with self.subTest(mode=mode, category=category):
                    a, h, rt = self.adapter(category, text, mode)
                    d = a.drive(deadline=time.monotonic() + 5).decision
                    self.assertEqual((d.category, d.status), (category, "accepted"))
                    if category == "nt":
                        self.assertNotIn("visual", rt.calls)
                    if category == "tunis" and mode == "marker_first":
                        self.assertNotIn("registration", rt.calls)
                    a.release()

    def test_regional_input_maps_to_original_crop_full_height(self):
        a, h, rt = self.adapter("tunis", mode="marker_first")
        a.drive(deadline=time.monotonic() + 5)
        np.testing.assert_array_equal(rt.images["left"], self.image[:, :40])
        np.testing.assert_array_equal(rt.images["right"], self.image[:, 60:])
        self.assertEqual(h.seal().decision.observed_text, (("left", "001"), ("right", "0007")))

    def test_failed_execution_is_retired_and_distinct_from_invalid_read(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        rt.fail = True
        failed = a.execute(r)
        self.assertEqual((failed.outcome, failed.error_code, failed.provenance),
                         ("failed", "execution_failed", rt.expected("category_ocr")))
        h.inject(failed)
        visual, = h.advance()
        self.assertEqual(visual.role, "visual")  # Source Arabic failure fallback.
        self.assertIsNone(a.active)

    def test_invalid_number_never_becomes_accepted_from_category(self):
        a, h, _ = self.adapter("nt", "00 07")
        d = a.drive(deadline=time.monotonic() + 5).decision
        self.assertEqual((d.category, d.status, d.reason_kind), ("nt", "unresolved", "invalid_format"))

    def test_malformed_model_output_has_explicit_decode_failure(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        rt.malformed = True
        result = a.execute(r)
        self.assertEqual((result.outcome, result.error_code), ("failed", "output_decode_failed"))

    def test_wrong_actual_identity_blocks_without_borrowed_attribution(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        rt.bad_identity = True
        with self.assertRaises(BindingError):
            a.execute(r)
        self.assertTrue(a.blocked)
        self.assertEqual(rt.calls, {})
        self.assertEqual(a.results, {})
        with self.assertRaises(ValueError):
            h.seal()

    def test_unretired_return_keeps_crop_and_global_lane_until_ack(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        rt.defer = True
        with self.assertRaises(RetirementPending):
            a.execute(r)
        with self.assertRaises(ValueError):
            a.release(discard=True)
        with self.assertRaises(Exception):
            with native_access():
                self.fail("unretired lane was reacquired")
        with self.assertRaises(ValueError):
            h.seal()
        self.assertFalse(a.bound.resource.released)
        self.assertIsNone(a.poll_retirement())
        rt.ctx.retired()
        result = a.poll_retirement()
        self.assertEqual(result.outcome, "succeeded")
        with native_access():
            pass
        h.inject(result)

    def test_execution_failure_without_retirement_does_not_fabricate_failed_result(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        rt.fail = rt.defer = True
        with self.assertRaises(RetirementPending):
            a.execute(r)
        self.assertEqual(a.results, {})
        self.assertFalse(a.bound.resource.released)
        rt.ctx.retired()
        self.assertEqual(a.poll_retirement().outcome, "failed")

    def test_release_requires_seal_and_unadmitted_requests_do_not_execute(self):
        a, h, rt = self.adapter()
        r, = h.advance()
        a.execute(r)
        with self.assertRaises(ValueError):
            a.release()
        other = Harness(replace(h.engine.crop, crop_id="other"), tunis_mode="legacy_whole")
        r, = other.advance()
        with self.assertRaises(ValueError):
            a.execute(r)
        self.assertEqual(sum(rt.calls.values()), 1)

    def test_time_budget_prevents_new_invocations_without_fake_seal(self):
        a, h, rt = self.adapter()
        with self.assertRaises(ValueError):
            a.drive(deadline=1, monotonic=lambda: 2)
        self.assertEqual(rt.calls, {})
        self.assertEqual(h.plate.requests, ())


class T1OwnershipTests(unittest.TestCase):
    def engine(self, *, ambiguous=False, unassociated=False, batch=None):
        owner, _, _ = fixture()
        context = owner.lifetime.context
        clock = FrameReceiptClock(context, utc_clock=lambda: context.started_at,
                                  monotonic_clock=lambda: 100, policy=ClockPolicy())
        rows = [[0, 0, 120, 90, 7, .9, 2, 0], [1, 0, 121, 90, 8, .9, 2, 0]] if ambiguous else None
        d = (130, 90, 150, 98, .8)
        plates = Plates([], PlateDetections((d,), ((1, d),)) if unassociated else batch)
        engine = CombinedEngine(context, clock, Vehicles([], rows), plates, {},
            algorithm=RecognitionIdentity("injected-hsv", 1, "classify"), quality_algorithm="injected-quality",
            plate_quality_algorithm="injected-quality", estimate=lambda c: SimpleNamespace(label="red", confidence=.9),
            quality_fn=lambda c, s, b: SimpleNamespace(combined=.9), plate_quality_fn=lambda c, conf, s, b: float(c.size),
            limits=VideoPlateLimits(max_frames=2))
        def cleanup():
            for c in engine.plate.buffers:
                c.release()
            engine.close()
        self.addCleanup(cleanup)
        return engine, clock.capture(0)

    def test_original_coordinate_mapping_clips_without_resizing(self):
        pixels = np.arange(100 * 160 * 3, dtype=np.uint8).reshape(100, 160, 3)
        c = crop_in_frame(pixels, (-4.7, 2.9, 18.6, 10.2, .9), origin=(10, 20))
        self.assertEqual(c.bbox, (5, 22, 28, 30))
        np.testing.assert_array_equal(c.crop, pixels[22:30, 5:28])

    def test_ambiguous_and_unassociated_have_no_owned_crop_or_execution(self):
        for flags in (dict(ambiguous=True), dict(unassociated=True)):
            e, receipt = self.engine(**flags)
            e.process(DecodedFrame(receipt, np.full((100, 160, 3), 50, dtype=np.uint8)))
            self.assertFalse(e.plate.buffers)
            self.assertIsNone(e.plate.associations[0]["association"]["selected_track_id"])

    def test_best_crop_replacement_happens_before_any_v3_execution(self):
        e, receipt = self.engine()
        image = np.full((100, 160, 3), 50, dtype=np.uint8)
        e.process(DecodedFrame(receipt, image))
        old = e.plate.buffers[0]
        d = (30, 50, 100, 80, .9)
        e.plate.detector.batch = PlateDetections((d,), ((1, d),))
        second = e.colour.pipeline.observation_clock.capture(1)
        e.process(DecodedFrame(second, image))
        self.assertTrue(old.released)
        self.assertEqual(old.release_calls, 1)
        self.assertEqual(e.plate.crop_replacements, 1)
        self.assertIs(e.plate.buffers[0].binding.candidate.receipt, second)


class AssetBindingTests(unittest.TestCase):
    def test_cuda_preflight_rejects_cpu_builds_and_provider_fallback(self):
        versions = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "paddlepaddle-gpu": "3.3.1",
                    "paddleocr": "3.7.0", "open-image-models": "0.6.0", "ultralytics": "8.4.46"}
        for torch_ok, paddle_ok, providers, reason in (
            (False, True, ["CUDAExecutionProvider"], "torch_cuda_unavailable"),
            (True, False, ["CUDAExecutionProvider"], "paddle_cuda_unavailable"),
            (True, True, ["CPUExecutionProvider"], "onnx_cuda_unavailable")):
            modules = dict(torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: torch_ok),
                                               version=SimpleNamespace(cuda="12.8")),
                           paddle=SimpleNamespace(__version__="3.3.1", is_compiled_with_cuda=lambda: paddle_ok,
                                                  device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1))),
                           onnxruntime=SimpleNamespace(get_available_providers=lambda: providers))
            with self.subTest(reason=reason), patch.dict("sys.modules", modules), \
                 patch("importlib.metadata.version", side_effect=lambda n: versions.get(n, "synthetic")):
                with self.assertRaisesRegex(BindingError, reason):
                    cuda_preflight()

    def test_missing_runtime_package_has_explicit_blocker(self):
        from importlib.metadata import PackageNotFoundError
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError), \
             self.assertRaisesRegex(BindingError, "missing_runtime_package_torch"):
            cuda_preflight()

    def test_wrong_artifact_hash_rejected_before_model_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "weights"
            p.write_bytes(b"synthetic")
            a = object.__new__(Assets)
            a.cache = Path(directory)
            a.source = Path(directory)
            a.binding_files = {}
            a.lock = dict(files=dict(marker=dict(base="cache", path="weights", bytes=9,
                                                sha256=sha256(b"synthetic").hexdigest())))
            a.verify(("marker",))
            p.write_bytes(b"different")
            with self.assertRaisesRegex(BindingError, "wrong_artifact_marker"):
                a.verify(("marker",))

    def test_wrong_dino_index_identity_rejected(self):
        expected = ReferenceIndex("approved", "a" * 64, (("1", "idara"),))
        assets = SimpleNamespace(runtime={}, index_identity=expected, labels=lambda r: (), verify=lambda labels: None)
        models = CudaModels(assets)
        models.index_identity = replace(expected, index_sha256="b" * 64)
        with self.assertRaisesRegex(BindingError, "reference_index_identity_drift"):
            models.verify("visual")

    def test_changed_loaded_reference_categories_rejected(self):
        expected = ReferenceIndex("approved", "a" * 64, (("1", "idara"),))
        assets = SimpleNamespace(runtime={}, index_identity=expected, labels=lambda r: (), verify=lambda labels: None)
        models = CudaModels(assets)
        models.models["visual"] = (None, SimpleNamespace(references=[dict(id="1", category="shorta")]), None)
        with self.assertRaisesRegex(BindingError, "loaded_reference_index_drift"):
            models.verify("visual")

    def test_source_detector_config_restores_existing_t1_alias(self):
        import sys
        sentinel = SimpleNamespace(value="t1")
        with patch.dict(sys.modules, {"run_video_pipeline": sentinel}):
            with source_detector_config() as config:
                self.assertEqual(config.PLATE_DETECTOR_PROVIDERS, "CUDAExecutionProvider")
                self.assertEqual(config.DETECT_IMGSZ, 640)
            self.assertIs(sys.modules["run_video_pipeline"], sentinel)

    def test_source_namespace_conflict_rejected(self):
        import sys
        alias = "_t1_mohamed_f64ac4_lpr_plugin"
        with patch.dict(sys.modules, {alias: SimpleNamespace(__path__=["wrong-source"])}):
            with self.assertRaises(BindingError):
                source_plugin(SimpleNamespace(active=Path("/different")))

    def test_runner_requires_explicit_opt_in_mode_and_budgets(self):
        p = Path(__file__).resolve().parents[3] / "scripts/vehicle_mohamed_lpr_mp4.py"
        spec = importlib.util.spec_from_file_location("_review_runner", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with patch("sys.stderr"):
            for argv in ([], ["--enable-mohamed-lpr-v3"], ["--tunis-mode", "0"]):
                with self.assertRaises(SystemExit):
                    m.arguments(argv)


if __name__ == "__main__":
    unittest.main()
