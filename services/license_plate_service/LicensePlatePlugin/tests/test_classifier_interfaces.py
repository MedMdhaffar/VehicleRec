"""Interface contracts with real preprocessing and mocked expensive inference."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from license_plate_plugin import PlateCategoryClassifier, PluginError, load_default_config
from license_plate_plugin.config import ResolutionPolicy
from license_plate_plugin.ocr import ArabicRecognizer


def evidence_only(value):
    """Remove provenance and measured durations, including Paddle input_path."""
    if isinstance(value, dict):
        return {
            key: evidence_only(item) for key, item in value.items()
            if key not in {"input", "input_path", "submitted_path", "timing"}
            and not key.endswith("seconds")
        }
    if isinstance(value, list):
        return [evidence_only(item) for item in value]
    return value


class ClassifierInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.classifier = PlateCategoryClassifier.__new__(PlateCategoryClassifier)
        self.classifier.config = SimpleNamespace(policy=ResolutionPolicy(), tie_tolerance=1e-12)
        self.classifier.encoder = object()
        self.classifier.index = Mock(path=Path("fixture.npz"), compatibility_fingerprint="fixture")
        self.classifier.index.match.return_value = {
            "visual_category": "tunis", "nearest_reference_id": "12",
            "nearest_similarity": 0.7, "predicted_category_margin": 0.1, "tie_flag": False,
        }
        self.classifier.ocr = ArabicRecognizer.__new__(ArabicRecognizer)
        self.classifier.ocr.model = Mock()
        self.set_ocr("نت١0379")
        self.extract = patch(
            "license_plate_plugin.classifier.extract_r2",
            return_value=(np.ones(1536, dtype=np.float32), {"views": {}}),
        ).start()
        self.addCleanup(patch.stopall)
        self.image = np.random.default_rng(7).integers(0, 256, (35, 129, 3), dtype=np.uint8)

    def set_ocr(self, text):
        self.classifier.ocr.model.predict.side_effect = None
        self.classifier.ocr.model.predict.return_value = [
            {"res": {"rec_text": text, "rec_score": 0.85}}
        ]

    def test_invalid_arrays_fail_before_models_or_output(self):
        invalid = [None, [], "plate.png", np.zeros((0, 3, 3), np.uint8),
                   np.zeros((2, 0, 3), np.uint8), np.zeros((3, 4), np.uint8),
                   np.zeros((3, 4, 4), np.uint8), np.zeros((3, 4, 3), np.float32),
                   np.zeros((3, 4, 3), np.uint16), np.zeros((3, 4, 3), bool)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unused"
            for image in invalid:
                with self.subTest(image_type=type(image)), self.assertRaisesRegex(PluginError, "Expected"):
                    self.classifier.classify_bgr(image, output)
            self.assertFalse(output.exists())
        self.extract.assert_not_called()
        self.classifier.ocr.model.predict.assert_not_called()

    def test_caller_storage_is_isolated_even_from_mutating_dependency(self):
        image = self.image[:, ::2]
        before = image.copy()
        image.setflags(write=False)

        def mutating_extract(owned, *args):
            self.assertTrue(owned.flags.c_contiguous)
            owned[:] = 0
            return np.ones(1536, np.float32), {}

        self.extract.side_effect = mutating_extract
        self.classifier.classify_bgr(image)
        np.testing.assert_array_equal(image, before)
        self.assertFalse(image.flags.writeable)

    def test_default_array_path_has_no_file_operations_or_fabricated_provenance(self):
        with patch("license_plate_plugin.ocr.save_png") as save, patch(
            "license_plate_plugin.classifier.sha256_file"
        ) as hash_file, patch("pathlib.Path.mkdir") as mkdir:
            result = self.classifier.classify_bgr(self.image)
        save.assert_not_called()
        hash_file.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(result["input"], {
            "kind": "bgr_array", "dtype": "uint8", "color_order": "BGR", "width": 129, "height": 35,
        })
        self.assertIsNone(result["ocr"]["submitted_path"])
        submitted = self.classifier.ocr.model.predict.call_args.kwargs["input"]
        self.assertIsInstance(submitted, np.ndarray)
        self.assertEqual(submitted.dtype, np.uint8)

    def test_array_diagnostics_are_optional_and_lossless(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.classifier.classify_bgr(self.image, directory)
            path = Path(result["ocr"]["submitted_path"])
            self.assertEqual(path.name, "array_budget_unpadded.png")
            self.assertTrue(path.is_file())
            self.assertEqual(self.classifier.ocr.model.predict.call_args.kwargs["input"], str(path))
            saved = cv2.imread(str(path))
            self.assertEqual(saved.shape[:2], (
                result["ocr"]["submitted_height"], result["ocr"]["submitted_width"],
            ))
            self.assertEqual(len(list(Path(directory).rglob("*.png"))), 1)

    def test_file_and_array_evidence_match_and_file_metadata_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plate.png"
            self.assertTrue(cv2.imwrite(str(source), self.image))
            file_result = self.classifier.classify(source, Path(directory) / "file-output")
            array_result = self.classifier.classify_bgr(cv2.imread(str(source)))
            self.assertEqual(evidence_only(file_result), evidence_only(array_result))
            self.assertEqual(set(file_result["input"]), {"path", "sha256", "width", "height"})
            self.assertEqual(file_result["input"]["path"], str(source.resolve()))
            self.assertEqual(len(file_result["input"]["sha256"]), 64)
            self.assertTrue(Path(file_result["ocr"]["submitted_path"]).is_file())
            self.assertEqual(Path(file_result["ocr"]["submitted_path"]).name, "plate_budget_unpadded.png")

    def test_shared_decisions_for_positive_absent_conflicting_and_failed_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plate.png"
            cv2.imwrite(str(source), self.image)
            for text, final, conflict, failed in [
                ("نت68", "nt", False, False), ("123", "tunis", False, False),
                ("نت تونس", "tunis", True, False), (None, "tunis", False, True),
            ]:
                with self.subTest(text=text):
                    if failed:
                        self.classifier.ocr.model.predict.side_effect = RuntimeError("fixture failure")
                    else:
                        self.set_ocr(text)
                    array = self.classifier.classify_bgr(self.image)
                    file = self.classifier.classify(source, directory)
                    self.assertEqual(array["decision"], file["decision"])
                    self.assertEqual(array["decision"]["final_category"], final)
                    self.assertEqual(array["decision"]["conflict_flag"], conflict)
                    self.assertEqual(array["decision"]["ocr_failure_flag"], failed)

    def test_disabled_ocr_keeps_visual_result_in_both_interfaces(self):
        self.classifier.ocr = None
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plate.png"
            cv2.imwrite(str(source), self.image)
            file = self.classifier.classify(source, directory)
            array = self.classifier.classify_bgr(self.image)
            self.assertEqual(evidence_only(file), evidence_only(array))
            self.assertEqual(array["ocr"]["inference_status"], "disabled")
            self.assertEqual(array["decision"]["final_category"], "tunis")

    def test_exclusions_and_evaluation_are_forwarded_by_both_interfaces(self):
        kwargs = {"exclude_reference_ids": ("5",), "exclude_groups": {"group_id": "car"},
                  "expected_category": "nt"}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plate.png"
            cv2.imwrite(str(source), self.image)
            for result in (self.classifier.classify_bgr(self.image, **kwargs),
                           self.classifier.classify(source, directory, **kwargs)):
                self.assertTrue(result["evaluation"]["final_correct"])
        for call in self.classifier.index.match.call_args_list:
            self.assertEqual(call.kwargs["exclude_ids"], ("5",))
            self.assertEqual(call.kwargs["exclude_groups"], {"group_id": "car"})
            self.assertEqual(call.kwargs["expected_category"], "nt")

    def test_calls_reuse_models_and_index(self):
        encoder, index, ocr = self.classifier.encoder, self.classifier.index, self.classifier.ocr
        self.classifier.classify_bgr(self.image)
        self.classifier.classify_bgr(self.image)
        self.assertIs(self.classifier.encoder, encoder)
        self.assertIs(self.classifier.index, index)
        self.assertIs(self.classifier.ocr, ocr)
        self.assertEqual(ocr.model.predict.call_count, 2)

    def test_initialization_loads_dependencies_once_for_multiple_calls(self):
        config = load_default_config()
        with patch("license_plate_plugin.classifier.DinoV3Encoder") as encoder, patch(
            "license_plate_plugin.classifier.compatibility_record", return_value={}
        ), patch("license_plate_plugin.classifier.ReferenceIndex.load", return_value=self.classifier.index) as index, patch(
            "license_plate_plugin.classifier.ArabicRecognizer", return_value=self.classifier.ocr
        ) as ocr:
            classifier = PlateCategoryClassifier(config)
            classifier.classify_bgr(self.image)
            classifier.classify_bgr(self.image)
        encoder.assert_called_once_with(config.dinov3_repo, config.dinov3_checkpoint, config.device)
        index.assert_called_once_with(config.reference_index, {})
        ocr.assert_called_once_with(config.paddle_model_dir, config.ocr_device)

    def test_array_ocr_input_preserves_bgr_channel_order(self):
        image = np.empty_like(self.image)
        image[:] = [11, 73, 201]
        self.classifier.classify_bgr(image)
        submitted = self.classifier.ocr.model.predict.call_args.kwargs["input"]
        np.testing.assert_array_equal(submitted[0, 0], [11, 73, 201])

    def test_default_config_is_anchored_to_package(self):
        config = load_default_config()
        plugin_root = Path(__file__).resolve().parents[1]
        self.assertEqual(config.source_path, plugin_root / "configuration/local_reproduction.json")
        self.assertEqual(config.reference_index, plugin_root / "assets/reference_index/reference_r2.npz")


if __name__ == "__main__":
    unittest.main()
