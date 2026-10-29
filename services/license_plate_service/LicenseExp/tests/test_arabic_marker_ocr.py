import unittest

import numpy as np

from arabic_marker_ocr import (
    ArabicOCRExperimentError,
    category_from_normalized_marker,
    extract_marker_crops,
    fixed_filter_variants,
    normalize_arabic_marker,
    parse_nt_evidence,
    unwrap_paddle_result,
)


class NormalizationTests(unittest.TestCase):
    def test_fixed_normalization_preserves_letter_identity(self):
        self.assertEqual(normalize_arabic_marker(" ﻧَـ ﺕ "), "نت")
        self.assertEqual(normalize_arabic_marker("شُرْطَة"), "شرطة")
        self.assertEqual(normalize_arabic_marker("ت و ن س"), "تونس")
        self.assertNotEqual(normalize_arabic_marker("ت"), normalize_arabic_marker("ن"))

    def test_exact_mapping_and_unknown(self):
        self.assertEqual(category_from_normalized_marker("نت"), "nt")
        self.assertEqual(category_from_normalized_marker("شرطة"), "shorta")
        self.assertEqual(category_from_normalized_marker("تونس"), "tunis")
        self.assertEqual(category_from_normalized_marker("شرطه"), "unknown")
        self.assertEqual(category_from_normalized_marker(""), "unknown")

    def test_conservative_nt_boundaries_and_conflicts(self):
        for value in ("ن ت", "نت", "214172نت", "نت-103679", "١٢٣ ن ت ٤٥"):
            self.assertEqual(parse_nt_evidence(value)["decision"], "present")
        self.assertEqual(parse_nt_evidence("بنت")["decision"], "absent")
        self.assertEqual(parse_nt_evidence("نتيجة")["decision"], "absent")
        self.assertEqual(parse_nt_evidence("تونس 123")["decision"], "conflicting")
        self.assertEqual(parse_nt_evidence("شرطة")["decision"], "conflicting")
        self.assertEqual(parse_nt_evidence("")["decision"], "absent")


class CropTests(unittest.TestCase):
    def records(self):
        records = []
        for image_id in range(4, 11):
            bbox = [1, 2, 5, 7]
            if image_id == 5:
                bbox = [89, 6, 120, 27]
                image = np.zeros((35, 129, 3), dtype=np.uint8)
            else:
                image = np.zeros((10, 10, 3), dtype=np.uint8)
            records.append({"id": str(image_id), "marker_bbox": bbox, "image": image})
        return records

    def test_exclusive_crop_dimensions_and_corrected_five(self):
        records, crops = extract_marker_crops(self.records())
        self.assertEqual([record["id"] for record in records], [str(i) for i in range(4, 11)])
        self.assertEqual(crops[1].shape[:2], (21, 31))
        self.assertEqual(crops[0].shape[:2], (5, 4))

    def test_invalid_bounds_fail(self):
        records = self.records()
        records[0]["marker_bbox"] = [0, 0, 11, 10]
        with self.assertRaises(ArabicOCRExperimentError):
            extract_marker_crops(records)


class OutputPreservationTests(unittest.TestCase):
    def test_raw_output_is_not_normalized_in_place(self):
        raw = " نَـ ت "
        text, confidence, payload = unwrap_paddle_result({"res": {"rec_text": raw, "rec_score": 0.75}})
        self.assertEqual(text, raw)
        self.assertEqual(confidence, 0.75)
        self.assertEqual(payload["res"]["rec_text"], raw)

    def test_empty_output_is_preserved(self):
        text, confidence, _ = unwrap_paddle_result({"res": {"rec_text": "", "rec_score": 0.1}})
        self.assertEqual(text, "")
        self.assertEqual(confidence, 0.1)


class FixedFilterTests(unittest.TestCase):
    def test_variants_preserve_dimensions_and_three_channels(self):
        image = np.zeros((11, 17, 3), dtype=np.uint8)
        image[:, :8] = (15, 60, 220)
        variants = fixed_filter_variants(image)
        self.assertEqual(list(variants), ["original", "otsu", "canny", "otsu_canny"])
        np.testing.assert_array_equal(variants["original"], image)
        for variant in variants.values():
            self.assertEqual(variant.shape, image.shape)
            self.assertEqual(variant.dtype, np.uint8)

    def test_processed_channels_are_identical_and_binary(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        image[:, 15:] = 255
        variants = fixed_filter_variants(image)
        for condition in ("otsu", "canny", "otsu_canny"):
            variant = variants[condition]
            np.testing.assert_array_equal(variant[:, :, 0], variant[:, :, 1])
            np.testing.assert_array_equal(variant[:, :, 1], variant[:, :, 2])
            self.assertTrue(set(np.unique(variant)).issubset({0, 255}))

    def test_invalid_input_fails(self):
        with self.assertRaises(ArabicOCRExperimentError):
            fixed_filter_variants(np.zeros((10, 10), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()

