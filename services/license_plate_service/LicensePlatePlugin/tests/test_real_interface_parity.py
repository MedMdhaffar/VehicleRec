"""Opt-in local-model parity; no downloads or gallery/configuration changes."""

import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import torch

from license_plate_plugin import PlateCategoryClassifier, load_default_config
from license_plate_plugin.manifest import load_reference_manifest
from test_classifier_interfaces import evidence_only


@unittest.skipUnless(os.environ.get("PLATE_PLUGIN_REAL_INFERENCE") == "1", "Opt-in local model inference")
class RealInterfaceParityTests(unittest.TestCase):
    def test_all_reference_crops_have_equivalent_file_and_array_evidence(self):
        config = load_default_config()
        classifier = PlateCategoryClassifier(config)
        records, _ = load_reference_manifest(config.reference_manifest)
        # Bound CPU threading only in this test, never in the plugin configuration.
        previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        self.addCleanup(torch.set_num_threads, previous_threads)
        with tempfile.TemporaryDirectory() as directory:
            for record in records:
                with self.subTest(reference=record["id"]):
                    image = cv2.imread(str(record["path"]))
                    before = image.copy()
                    kwargs = {"exclude_reference_ids": (record["id"],),
                              "expected_category": record["category"]}
                    file = classifier.classify(record["path"], Path(directory) / record["id"], **kwargs)
                    array = classifier.classify_bgr(image, **kwargs)
                    self.assertEqual(file["ocr"]["inference_status"], "completed")
                    self.assertEqual(array["ocr"]["inference_status"], "completed")
                    self.assertEqual(evidence_only(file), evidence_only(array))
                    self.assertIsNone(array["ocr"]["submitted_path"])
                    np.testing.assert_array_equal(image, before)


@unittest.skipUnless(os.environ.get("PLATE_PLUGIN_REAL_OCR") == "1", "Opt-in local Arabic OCR inference")
class RealArabicInputParityTests(unittest.TestCase):
    def test_lossless_file_and_array_ocr_inputs_match(self):
        from license_plate_plugin.ocr import ArabicRecognizer
        from license_plate_plugin.preprocessing import calculate_geometry, resize_and_pad

        config = load_default_config()
        recognizer = ArabicRecognizer(config.paddle_model_dir, config.ocr_device)
        records, _ = load_reference_manifest(config.reference_manifest)
        with tempfile.TemporaryDirectory() as directory:
            for record in records:
                with self.subTest(reference=record["id"]):
                    image = record["image"]
                    geometry = calculate_geometry(image.shape, (16, 16), config.policy)
                    resized, _ = resize_and_pad(image, geometry, config.policy)
                    before = resized.copy()
                    file = recognizer.recognize(resized, Path(directory) / f"{record['id']}.png")
                    array = recognizer.recognize(resized)
                    self.assertEqual(file["inference_status"], "completed", file["failure_reason"])
                    self.assertEqual(array["inference_status"], "completed", array["failure_reason"])
                    self.assertEqual(evidence_only(file), evidence_only(array))
                    np.testing.assert_array_equal(resized, before)


if __name__ == "__main__":
    unittest.main()
