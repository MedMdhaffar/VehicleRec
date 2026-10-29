import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from license_plate_plugin.config import ResolutionPolicy
from license_plate_plugin.errors import PluginError
from license_plate_plugin.ocr import unwrap_paddle_result
from license_plate_plugin.parser import normalize_arabic_marker, parse_nt_evidence
from license_plate_plugin.preprocessing import calculate_geometry, resize_and_pad
from license_plate_plugin.reference_index import ReferenceIndex
from license_plate_plugin.representations import region_coordinates


class PreprocessingTests(unittest.TestCase):
    def test_authoritative_whole_geometries(self):
        cases = {
            (159, 680): ([108, 463], [112, 464], [7, 29]),
            (203, 650): ([125, 401], [128, 416], [8, 26]),
            (35, 129): ([117, 430], [128, 432], [8, 27]),
        }
        for shape, expected in cases.items():
            with self.subTest(shape=shape):
                result = calculate_geometry((*shape, 3), (16, 16), ResolutionPolicy())
                self.assertEqual(result["resized_dimensions_hw"], expected[0])
                self.assertEqual(result["final_dimensions_hw"], expected[1])
                self.assertEqual(result["patch_grid_hw"], expected[2])

    def test_complete_content_is_preserved(self):
        image = np.random.default_rng(1).integers(0, 256, (35, 129, 3), dtype=np.uint8)
        geometry = calculate_geometry(image.shape, (16, 16), ResolutionPolicy())
        resized, padded = resize_and_pad(image, geometry, ResolutionPolicy())
        left, top, _, _ = geometry["padding_ltrb"]
        h, w = resized.shape[:2]
        np.testing.assert_array_equal(padded[top : top + h, left : left + w], resized)

    def test_regions_preserve_exact_rounding(self):
        self.assertEqual(
            region_coordinates(101, 20),
            {"left": [0, 0, 51, 20], "centre": [25, 0, 76, 20], "right": [50, 0, 101, 20]},
        )


class ParserTests(unittest.TestCase):
    def test_normalization_and_digit_boundaries(self):
        self.assertEqual(normalize_arabic_marker(" ﻧَـ ﺕ "), "نت")
        for value in ("ن ت", "نت", "214172نت", "نت-103679", "نت١0379"):
            self.assertEqual(parse_nt_evidence(value)["decision"], "present")

    def test_embedded_and_conflicting_text(self):
        self.assertEqual(parse_nt_evidence("بنت")["decision"], "absent")
        self.assertEqual(parse_nt_evidence("نتيجة")["decision"], "absent")
        self.assertEqual(parse_nt_evidence("شرطة")["decision"], "conflicting")
        self.assertEqual(parse_nt_evidence("تونس 123")["decision"], "conflicting")

    def test_raw_ocr_output_is_preserved(self):
        raw = " نَـ ت ١٢٣ "
        text, confidence, payload = unwrap_paddle_result(
            {"res": {"rec_text": raw, "rec_score": 0.75}}
        )
        self.assertEqual(text, raw)
        self.assertEqual(confidence, 0.75)
        self.assertEqual(payload["res"]["rec_text"], raw)


class IndexTests(unittest.TestCase):
    def make_index(self, directory: Path) -> ReferenceIndex:
        path = directory / "index.npz"
        compatibility = {"model": "fixture", "policy": 1}
        vectors = np.zeros((3, 1536), dtype=np.float32)
        vectors[0, 0] = 1.0
        vectors[1, 0] = 0.9; vectors[1, 1] = np.sqrt(1 - 0.9**2)
        vectors[2, 1] = 1.0
        refs = [
            {"id": "1", "category": "a", "physical_plate_id": None, "group_id": None},
            {"id": "2", "category": "a", "physical_plate_id": None, "group_id": None},
            {"id": "3", "category": "b", "physical_plate_id": None, "group_id": None},
        ]
        from license_plate_plugin.utils import canonical_sha256
        return ReferenceIndex(path, vectors, refs, compatibility, canonical_sha256(compatibility))

    def test_self_reference_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = self.make_index(Path(tmp))
            result = index.match(index.embeddings[0], exclude_ids=("1",), expected_category="a")
            self.assertEqual(result["nearest_reference_id"], "2")

    def test_incompatible_index_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            index = self.make_index(directory)
            index.save("manifest", {})
            with self.assertRaises(PluginError):
                ReferenceIndex.load(index.path, {"model": "different"})


if __name__ == "__main__":
    unittest.main()

