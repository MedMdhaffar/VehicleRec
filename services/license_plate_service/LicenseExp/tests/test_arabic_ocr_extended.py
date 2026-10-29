import unittest
from pathlib import Path

from arabic_marker_ocr import fixed_filter_variants
from evaluate_arabic_ocr_extended import load_manifest


class ExtendedManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.manifest = load_manifest(Path("arabic_ocr_extended_manifest.json"))

    def test_ids_categories_and_approved_nt_boxes(self):
        self.assertEqual([record["id"] for record in self.records], [str(i) for i in range(4, 13)])
        self.assertEqual(
            [record["category"] for record in self.records].count("nt"), 4
        )
        self.assertEqual(self.records[2]["marker_bbox"], [332, 22, 483, 98])
        self.assertEqual(self.records[3]["marker_bbox"], [362, 14, 536, 97])

    def test_all_variants_preserve_each_crop_shape(self):
        for record in self.records:
            variants = fixed_filter_variants(record["crop"])
            for variant in variants.values():
                self.assertEqual(variant.shape, record["crop"].shape)

    def test_legacy_renumbering_map_is_complete(self):
        mapping = {
            record["id"]: record["baseline_v4_id"]
            for record in self.records if record["baseline_v4_id"] is not None
        }
        self.assertEqual(
            mapping,
            {"4": "4", "5": "5", "8": "6", "9": "7", "10": "8", "11": "9", "12": "10"},
        )


if __name__ == "__main__":
    unittest.main()
