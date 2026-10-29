import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from controlled_plate_evaluation import build_pair_rows
from plate_variants import (
    VariantExperimentError,
    classification_metrics,
    leave_one_out_predictions,
    prepare_controlled_variants,
    prepare_variants,
    rounded_bbox,
    validate_annotations,
    validate_corners,
)


class GeometryValidationTests(unittest.TestCase):
    def test_bbox_rounding_is_enclosing_and_exclusive(self):
        self.assertEqual(rounded_bbox([1.8, 2.2, 8.1, 9.0], 10, 10), [1, 2, 9, 9])

    def test_invalid_corner_order_is_rejected(self):
        with self.assertRaises(VariantExperimentError):
            validate_corners([[0, 0], [10, 10], [10, 0], [0, 10]], 10, 10)

    def test_corner_conversion_and_rectified_size(self):
        image = np.zeros((40, 100, 3), dtype=np.uint8)
        annotation = {
            "tight_plate_bbox": [10, 5, 91, 36],
            "plate_corners": [[12, 7], [88, 8], [86, 33], [14, 34]],
            "rectification_status": "needed",
        }
        variants = prepare_variants(image, annotation)
        local = variants["p2"].metadata["perspective_transform"]["source_corners_p1_local_xy"]
        self.assertEqual(local[0], [2.0, 2.0])
        self.assertGreater(variants["p2"].image_bgr.shape[1], 70)
        self.assertGreater(variants["p2"].image_bgr.shape[0], 20)

    def test_grayscale_is_replicated_without_thresholding(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(30, dtype=np.uint8)
        annotation = {
            "tight_plate_bbox": [0, 0, 30, 20],
            "plate_corners": [[0, 0], [30, 0], [30, 20], [0, 20]],
            "rectification_status": "unnecessary",
        }
        p3 = prepare_variants(image, annotation)["p3"].image_bgr
        self.assertTrue(np.array_equal(p3[:, :, 0], p3[:, :, 1]))
        self.assertTrue(np.array_equal(p3[:, :, 1], p3[:, :, 2]))
        self.assertGreater(len(np.unique(p3)), 2)

    def test_controlled_variants_preserve_complete_dimensions(self):
        image = np.zeros((23, 47, 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(47, dtype=np.uint8)
        variants = prepare_controlled_variants(image)
        self.assertEqual(set(variants), {"p0", "grayscale", "clahe"})
        for variant in variants.values():
            self.assertEqual(variant.image_bgr.shape, image.shape)
            self.assertEqual(variant.metadata["pixels_cropped"], 0)
        gray = variants["grayscale"].image_bgr
        self.assertTrue(np.array_equal(gray[:, :, 0], gray[:, :, 1]))
        self.assertTrue(np.array_equal(gray[:, :, 1], gray[:, :, 2]))
        self.assertEqual(variants["clahe"].metadata["clahe"]["clipLimit"], 2.0)
        self.assertEqual(variants["clahe"].metadata["clahe"]["tileGridSize"], [4, 4])

    def test_marker_box_cannot_be_reused_as_plate_bbox(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "annotations.json"
            path.write_text(json.dumps({"images": {"1": {
                "tight_plate_bbox": [2, 2, 8, 8],
                "plate_corners": [[2, 2], [8, 2], [8, 8], [2, 8]],
                "rectification_status": "needed",
            }}}))
            records = [{"id": "1", "available": True, "decoded_dimensions_wh": [10, 10], "marker_bbox": [2, 2, 8, 8]}]
            _, report = validate_annotations(path, records)
            self.assertFalse(report["complete"])
            self.assertIn("marker_bbox", report["errors"][0])


class EvaluationTests(unittest.TestCase):
    def records(self):
        return [
            {"id": str(index + 1), "category": "a" if index < 5 else "b", "path": Path(f"{index + 1}.png"), "physical_plate_id": None, "group_id": None}
            for index in range(10)
        ]

    def test_pair_count_and_p0_budget_equivalence(self):
        records = self.records()
        identity = np.eye(10, dtype=np.float32)
        matrices = {name: identity.copy() for name in ("original", "native", "budget", "p0_budget", "grayscale_budget", "clahe_budget")}
        pairs = build_pair_rows(matrices, records, full=True)
        self.assertEqual(len(pairs), 45)
        self.assertEqual(len({(row["image_1_id"], row["image_2_id"]) for row in pairs}), 45)
        self.assertTrue(all(row["budget_similarity"] == row["p0_budget_similarity"] for row in pairs))

    def test_p0_budget_mismatch_is_rejected(self):
        records = self.records()
        identity = np.eye(10, dtype=np.float32)
        matrices = {name: identity.copy() for name in ("original", "native", "budget", "p0_budget", "grayscale_budget", "clahe_budget")}
        matrices["p0_budget"][0, 1] = matrices["p0_budget"][1, 0] = 0.5
        with self.assertRaises(VariantExperimentError):
            build_pair_rows(matrices, records, full=True)

    def test_leave_one_out_excludes_self_and_same_group(self):
        records = [
            {"id": "1", "category": "a", "group_id": "g", "physical_plate_id": None},
            {"id": "2", "category": "a", "group_id": "g", "physical_plate_id": None},
            {"id": "3", "category": "b", "group_id": None, "physical_plate_id": None},
        ]
        matrix = np.asarray([[1, .99, .2], [.99, 1, .3], [.2, .3, 1]], dtype=float)
        rows = leave_one_out_predictions("fixture", matrix, records)
        first = rows[0]
        self.assertEqual(first["nearest_reference_id"], "3")
        self.assertEqual(first["eligible_reference_count"], 1)
        self.assertIsNone(first["best_same_category_reference_id"])

    def test_metrics_fixture(self):
        rows = [
            {"query_category": "a", "predicted_category": "a", "correct": True},
            {"query_category": "a", "predicted_category": "b", "correct": False},
            {"query_category": "b", "predicted_category": "b", "correct": True},
            {"query_category": "b", "predicted_category": "b", "correct": True},
        ]
        metrics = classification_metrics(rows, ["a", "b"])
        self.assertEqual(metrics["correct"], 3)
        self.assertAlmostEqual(metrics["balanced_accuracy"], 0.75)
        self.assertAlmostEqual(metrics["macro_f1"], (2 / 3 + 0.8) / 2)


if __name__ == "__main__":
    unittest.main()
