import unittest

import numpy as np
import torch

from plate_representations import (
    global_regional_matrix,
    patch_selection,
    region_coordinates,
)
from plate_variants import VariantExperimentError


class PatchSelectionTests(unittest.TestCase):
    def test_padding_only_and_partial_patches(self):
        metadata = {
            "patch_grid_hw": [2, 3],
            "resized_dimensions_hw": [15, 15],
            "padding_ltrb": [14, 7, 13, 6],
        }
        result = patch_selection(metadata, (14, 14))
        self.assertEqual(result["valid_patch_count"], 4)
        self.assertEqual(result["padding_only_patch_count"], 2)
        self.assertEqual(result["partial_patch_count"], 4)
        self.assertEqual(len(result["selected_mask_flat"]), 6)
        self.assertTrue(all(0 < value < 1 for value in result["selected_content_coverage_fractions"]))

    def test_minimal_padding_selects_every_intersecting_patch(self):
        metadata = {
            "patch_grid_hw": [2, 2],
            "resized_dimensions_hw": [20, 20],
            "padding_ltrb": [4, 4, 4, 4],
        }
        result = patch_selection(metadata, (14, 14))
        self.assertEqual(result["valid_patch_count"], 4)
        self.assertEqual(result["padding_only_patch_count"], 0)
        self.assertEqual(result["partial_patch_count"], 4)


class RegionTests(unittest.TestCase):
    def test_region_floor_ceil_rounding_for_odd_width(self):
        self.assertEqual(
            region_coordinates(101, 20),
            {
                "left": [0, 0, 51, 20],
                "centre": [25, 0, 76, 20],
                "right": [50, 0, 101, 20],
            },
        )

    def test_empty_source_is_rejected(self):
        with self.assertRaises(VariantExperimentError):
            region_coordinates(0, 20)


class AggregationTests(unittest.TestCase):
    def test_weighted_concatenation_matches_direct_score(self):
        basis = [torch.tensor([1.0, 0.0]), torch.tensor([0.0, 1.0])]
        components = {
            "whole": basis,
            "left": basis,
            "centre": basis,
            "right": basis,
        }
        matrix, metadata = global_regional_matrix(components)
        np.testing.assert_allclose(matrix, np.eye(2), atol=1e-7)
        self.assertLessEqual(metadata["maximum_absolute_equivalence_difference"], 1e-7)
        self.assertEqual(metadata["dimension_if_concatenated"], 8)

    def test_component_order_is_fixed(self):
        vector = [torch.tensor([1.0, 0.0])]
        with self.assertRaises(VariantExperimentError):
            global_regional_matrix(
                {"left": vector, "whole": vector, "centre": vector, "right": vector}
            )


if __name__ == "__main__":
    unittest.main()
