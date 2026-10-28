import unittest

import numpy as np

from arabic_marker_ocr import (
    ArabicOCRExperimentError,
    category_from_normalized_marker,
    extract_marker_crops,
    normalize_arabic_marker,
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


if __name__ == "__main__":
    unittest.main()

