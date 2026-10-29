import unittest

import numpy as np
import torch

from plate_preprocessing import (
    PreprocessingError,
    ResolutionPolicy,
    calculate_geometry,
    prepare_variable_input,
    reconstruct_tensor_display,
    resize_and_pad,
)


class FakeProcessor:
    do_normalize = True
    do_rescale = True
    rescale_factor = 1 / 255
    image_mean = (0.485, 0.456, 0.406)
    image_std = (0.229, 0.224, 0.225)

    def __call__(self, images, return_tensors, do_resize, do_center_crop):
        self.last_geometry_flags = (do_resize, do_center_crop)
        rgb = np.asarray(images).copy()
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255
        mean = torch.tensor(self.image_mean)[:, None, None]
        std = torch.tensor(self.image_std)[:, None, None]
        return {"pixel_values": ((tensor - mean) / std).unsqueeze(0)}


class GeometryTests(unittest.TestCase):
    patch = (14, 14)
    policy = ResolutionPolicy()

    def test_wide_odd_native_padding(self):
        geometry = calculate_geometry((31, 101, 3), "native", self.patch, self.policy)
        self.assertEqual(geometry["final_dimensions_hw"], [42, 112])
        self.assertEqual(geometry["padding_ltrb"], [5, 5, 6, 6])
        self.assertEqual(geometry["patch_grid_hw"], [3, 8])

    def test_tall_image_is_preserved(self):
        geometry = calculate_geometry((101, 31, 3), "budget", self.patch, self.policy)
        self.assertLessEqual(max(geometry["resized_dimensions_hw"]), 1008)
        self.assertLessEqual(geometry["patch_count"], 512)
        self.assertEqual(geometry["pixels_cropped"], 0)

    def test_tiny_image(self):
        geometry = calculate_geometry((1, 1, 3), "native", self.patch, self.policy)
        self.assertEqual(geometry["resized_dimensions_hw"], [1, 1])
        self.assertEqual(geometry["final_dimensions_hw"], [14, 14])
        self.assertEqual(geometry["padding_ltrb"], [6, 6, 7, 7])

    def test_already_patch_aligned(self):
        geometry = calculate_geometry((28, 42, 3), "native", self.patch, self.policy)
        self.assertEqual(geometry["padding_ltrb"], [0, 0, 0, 0])
        self.assertEqual(geometry["final_dimensions_hw"], [28, 42])

    def test_budget_limits_extreme_aspect_ratio(self):
        geometry = calculate_geometry((10, 10_000, 3), "budget", self.patch, self.policy)
        self.assertLessEqual(max(geometry["resized_dimensions_hw"]), 1008)
        self.assertLessEqual(geometry["patch_count"], 512)
        self.assertTrue(geometry["long_edge_limit_applied"])

    def test_patch_budget_can_reduce_scale(self):
        policy = ResolutionPolicy(target_area=10_000_000, max_long_edge=10_000, max_patch_count=8)
        geometry = calculate_geometry((100, 100, 3), "budget", self.patch, policy)
        self.assertLessEqual(geometry["patch_count"], 8)
        self.assertTrue(geometry["patch_count_limit_applied"])

    def test_aspect_ratio_within_rounding_tolerance(self):
        height, width = 37, 211
        geometry = calculate_geometry((height, width, 3), "budget", self.patch, self.policy)
        resized_height, resized_width = geometry["resized_dimensions_hw"]
        error = abs(resized_width / resized_height - width / height)
        tolerance = 0.5 / resized_height + width * 0.5 / (height * resized_height)
        self.assertLessEqual(error, tolerance)

    def test_invalid_configurations(self):
        invalid = [
            ResolutionPolicy(target_area=0),
            ResolutionPolicy(max_long_edge=0),
            ResolutionPolicy(max_patch_count=0),
            ResolutionPolicy(padding_rgb=(0, 0, 256)),
            ResolutionPolicy(interpolation="unknown"),
        ]
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(PreprocessingError):
                calculate_geometry((10, 20, 3), "budget", self.patch, policy)


class PixelPreservationTests(unittest.TestCase):
    def test_padding_preserves_complete_resized_image(self):
        image = np.arange(31 * 101 * 3, dtype=np.uint8).reshape(31, 101, 3)
        policy = ResolutionPolicy()
        geometry = calculate_geometry(image.shape, "native", (14, 14), policy)
        resized, padded = resize_and_pad(image, geometry, policy)
        left, top, _, _ = geometry["padding_ltrb"]
        height, width = resized.shape[:2]
        self.assertTrue(np.array_equal(resized, image))
        self.assertTrue(np.array_equal(padded[top : top + height, left : left + width], image))

    def test_exact_tensor_reconstruction(self):
        image = np.random.default_rng(0).integers(0, 256, (35, 129, 3), dtype=np.uint8)
        processor = FakeProcessor()
        prepared = prepare_variable_input(
            image, processor, "budget", (14, 14), ResolutionPolicy()
        )
        self.assertEqual(processor.last_geometry_flags, (False, False))
        self.assertTrue(
            prepared.metadata["display_reconstruction"]["matches_prepared_uint8_exactly"]
        )
        self.assertTrue(np.array_equal(prepared.display_bgr, prepared.padded_bgr))
        display, details = reconstruct_tensor_display(prepared.pixel_values, processor)
        self.assertTrue(np.array_equal(display, prepared.padded_bgr))
        self.assertEqual(details["clipped_value_count"], 0)


if __name__ == "__main__":
    unittest.main()
