import unittest

import cv2
import numpy as np
import torch

from evaluate_encoder_colour_shape import (
    dinov3_blocker_reason,
    reconstruct_naflex,
    siglip2_pooled_image_feature,
)
from compare_plate_embeddings import PlateExperimentError
from plate_feature_branches import (
    colour_descriptor,
    evaluate_leave_one_out,
    hellinger_distance,
    l1_histogram,
    normalize_shape_views,
    symmetric_chamfer_distance,
    symmetric_template_correlation,
)


class HistogramTests(unittest.TestCase):
    def test_l1_normalization_and_identity(self):
        image = np.zeros((8, 9, 3), dtype=np.uint8)
        image[:, :4] = (0, 0, 255)
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        histogram, count = l1_histogram(hsv)
        self.assertEqual(count, 72)
        self.assertAlmostEqual(float(histogram.sum()), 1.0)
        self.assertAlmostEqual(hellinger_distance(histogram, histogram), 0.0)

    def test_colour_groups_are_explicit(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        image[:, 15:] = 255
        descriptor = colour_descriptor(image)
        self.assertAlmostEqual(descriptor["measurements"]["bright_proportion"], 0.5)
        self.assertAlmostEqual(descriptor["measurements"]["dark_proportion"], 0.5)


class ShapeTests(unittest.TestCase):
    def test_template_score_is_symmetric(self):
        first = np.zeros((20, 30, 3), dtype=np.uint8)
        second = np.zeros((20, 30, 3), dtype=np.uint8)
        cv2.putText(first, "A", (3, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(second, "B", (3, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        first_view, second_view = normalize_shape_views(first), normalize_shape_views(second)
        forward, _ = symmetric_template_correlation(first_view, second_view)
        reverse, _ = symmetric_template_correlation(second_view, first_view)
        self.assertIsNotNone(forward)
        self.assertAlmostEqual(forward, reverse)
        chamfer_forward, _ = symmetric_chamfer_distance(first_view, second_view)
        chamfer_reverse, _ = symmetric_chamfer_distance(second_view, first_view)
        self.assertAlmostEqual(chamfer_forward, chamfer_reverse)

    def test_constant_and_empty_views_are_invalid(self):
        blank = normalize_shape_views(np.zeros((20, 30, 3), dtype=np.uint8))
        score, reason = symmetric_template_correlation(blank, blank)
        self.assertIsNone(score)
        self.assertIn("constant", reason)
        distance, reason = symmetric_chamfer_distance(blank, blank)
        self.assertIsNone(distance)
        self.assertIn("empty", reason)


class EvaluationDirectionTests(unittest.TestCase):
    def records(self):
        return [
            {"id": "1", "category": "a", "physical_plate_id": None, "group_id": None},
            {"id": "2", "category": "a", "physical_plate_id": None, "group_id": None},
            {"id": "3", "category": "b", "physical_plate_id": None, "group_id": None},
        ]

    def test_similarity_and_distance_direction_and_margin(self):
        similarity = np.asarray([[1, .8, .2], [.8, 1, .3], [.2, .3, 1]], dtype=float)
        distance = 1 - similarity
        sim_rows = evaluate_leave_one_out(self.records(), similarity, True)
        dist_rows = evaluate_leave_one_out(self.records(), distance, False)
        self.assertEqual(sim_rows[0]["nearest_reference_id"], "2")
        self.assertEqual(dist_rows[0]["nearest_reference_id"], "2")
        self.assertAlmostEqual(sim_rows[0]["correct_category_margin"], 0.6)
        self.assertAlmostEqual(dist_rows[0]["correct_category_margin"], 0.6)
        self.assertTrue(all(row["query_id"] != row["nearest_reference_id"] for row in sim_rows))

    def test_group_exclusion(self):
        records = self.records()
        records[0]["group_id"] = records[1]["group_id"] = "same"
        matrix = np.asarray([[1, .99, .1], [.99, 1, .2], [.1, .2, 1]], dtype=float)
        rows = evaluate_leave_one_out(records, matrix, True)
        self.assertEqual(rows[0]["nearest_reference_id"], "3")
        self.assertEqual(rows[0]["eligible_reference_count"], 1)


class FakeImageProcessor:
    patch_size = 2
    image_mean = [0.5, 0.5, 0.5]
    image_std = [0.5, 0.5, 0.5]


class FakeProcessor:
    image_processor = FakeImageProcessor()


class NaFlexTests(unittest.TestCase):
    def test_packed_patch_reconstruction_and_padding_mask(self):
        # Two 2x2 RGB patches in one row, plus one padded patch slot.
        image = torch.arange(24, dtype=torch.float32).reshape(3, 2, 4) / 23
        normalized = (image - 0.5) / 0.5
        # Match the installed processor's CHW -> grid_y, grid_x, y, x, channel
        # conversion and row-major flattening.
        patches = normalized.reshape(3, 1, 2, 2, 2).permute(1, 3, 2, 4, 0).reshape(2, 12)
        packed = torch.cat([patches, torch.zeros((1, 12))], dim=0).unsqueeze(0)
        inputs = {
            "pixel_values": packed,
            "pixel_attention_mask": torch.tensor([[1, 1, 0]]),
            "spatial_shapes": torch.tensor([[1, 2]]),
        }
        reconstructed, metadata = reconstruct_naflex(inputs, FakeProcessor())
        expected = cv2.cvtColor((image.permute(1, 2, 0).numpy() * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
        np.testing.assert_array_equal(reconstructed, expected)
        self.assertEqual(metadata["valid_patch_count"], 2)
        self.assertEqual(metadata["padded_patch_slots"], 1)

    def test_siglip2_structured_and_tensor_feature_outputs(self):
        pooled = torch.randn(1, 768)
        structured = type("Output", (), {"pooler_output": pooled})()
        self.assertIs(siglip2_pooled_image_feature(structured), pooled)
        self.assertIs(siglip2_pooled_image_feature(pooled), pooled)

        with self.assertRaises(PlateExperimentError):
            siglip2_pooled_image_feature(type("Output", (), {})())


class DINOv3AvailabilityTests(unittest.TestCase):
    def test_uncached_gated_checkpoint_has_actionable_error(self):
        reason = dinov3_blocker_reason(
            "facebook/dinov3-vits16-pretrain-lvd1689m",
            False,
            "OSError: not cached",
            None,
        )
        self.assertIn("not cached locally", reason)
        self.assertIn("--allow-download", reason)
        self.assertIn("accept", reason.lower())

    def test_failed_authorized_download_is_not_called_processor_bug(self):
        reason = dinov3_blocker_reason(
            "facebook/dinov3-vits16-pretrain-lvd1689m",
            True,
            "OSError: DNS failure",
            None,
        )
        self.assertIn("Authenticated download", reason)
        self.assertIn("reachable", reason)


if __name__ == "__main__":
    unittest.main()
