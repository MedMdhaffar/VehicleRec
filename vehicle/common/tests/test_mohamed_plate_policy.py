"""Synthetic policy tests only; no source checkout, models, broker or database needed."""
from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import subprocess
import sys
import unittest

from vehicle.common.mohamed_plate_policy import (
    Decision, DinoScores, Evidence, Marker, OCR, ReferenceScore, Thresholds,
    category_markers, decide, normalize_digits, normalize_ocr,
)


def ocr(role, text, confidence=.96):
    return OCR(role, "succeeded", text, confidence, "injected-" + role)


def visual(category, similarity=.3):
    return DinoScores("visual", "succeeded", tuple(
        ReferenceScore(str(index), name, similarity if name == category else -.95)
        for index, name in enumerate(("idara", "shorta", "tunis"), 1)), "injected-dino", "injected-gallery")


def sample(category="idara", text="001 - 002", confidence=.96):
    return Evidence(ocr("category_ocr", "ن ت" if category == "nt" else ""),
                    visual(category) if category != "nt" else None,
                    ocr("registration", text, confidence),
                    Marker("marker", "succeeded", True, .98, (40, 0, 60, 30), "injected-marker"),
                    crop_size=(100, 30))


def run(evidence, mode="legacy_whole", **kwargs):
    return decide(evidence, tunis_mode=mode, **kwargs)


class MohamedPlatePolicyTests(unittest.TestCase):
    def test_four_category_reads_and_display_separation(self):
        cases = (("tunis", "001T0007", "001 0007", "001 تونس 0007", "تونس"),
                 ("idara", "001 - 002", "001 - 002", "001 - 002", None),
                 ("nt", "0007", "0007", "0007 nt", "nt"),
                 ("shorta", "0007", "0007", "shorta 0007", "shorta"))
        for category, text, normalized, display, label in cases:
            with self.subTest(category=category):
                decision = run(sample(category, text))
                self.assertIsInstance(decision, Decision)
                self.assertEqual(decision.category, category)
                self.assertEqual(decision.status, "accepted")
                self.assertIsNone(decision.reason)
                self.assertEqual(decision.observed_text, (("registration", text),))
                self.assertEqual(decision.normalized_registration, normalized)
                self.assertEqual((decision.display_text, decision.display_label), (display, label))

    def test_tunis_mode_is_required_and_truthy_string_switch_is_rejected(self):
        with self.assertRaises(TypeError):
            decide(sample())
        for mode in ("0", "1", "", "automatic", False, None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                decide(sample(), tunis_mode=mode)

    def test_explicit_nt_precedes_even_stronger_visual_scores(self):
        evidence = replace(sample("nt", "0007"), visual=visual("shorta", 1.0))
        decision = run(evidence)
        self.assertEqual((decision.category, decision.category_source), ("nt", "ocr_nt_evidence"))
        self.assertEqual(decision.status, "accepted")
        self.assertNotIn("visual", dict(decision.evidence_used))
        self.assertIsNone(decision.nearest_similarity)

    def test_nt_precedence_does_not_hide_contradictory_category_markers(self):
        for marker in ("شرطة", "تونس"):
            evidence = replace(sample("nt", "0007"), category_ocr=ocr("category_ocr", "ن ت " + marker),
                               visual=visual("idara"))
            decision = run(evidence)
            self.assertEqual(decision.category, "nt")
            self.assertEqual((decision.status, decision.reason_kind, decision.reason),
                             ("unresolved", "conflict", "contradictory_category_marker"))
            self.assertIn("category_ocr", dict(decision.evidence_used))
            self.assertNotIn("visual", dict(decision.evidence_used))

    def test_category_arabic_normalization_and_letter_boundaries(self):
        for text in ("ن ت", "نـت", "نَ تِ", "نت"):
            self.assertTrue(category_markers(text)[0])
        for text in ("بنت", "نتب", "NT", "", "إدارة"):
            self.assertFalse(category_markers(text)[0])
        # The approved source parser intentionally does not add Idara to its conflict set.
        self.assertEqual(category_markers("إدارة"), (False, ()))

    def test_visual_cosine_has_no_acceptance_threshold_or_probability_conversion(self):
        decision = run(replace(sample(), visual=visual("idara", -.8)))
        self.assertEqual((decision.category, decision.status), ("idara", "accepted"))
        self.assertEqual(decision.nearest_similarity, -.8)
        self.assertAlmostEqual(decision.category_margin, .15)
        self.assertFalse(hasattr(decision, "category_probability"))

    def test_visual_tie_uses_numeric_reference_id_not_input_order(self):
        rows = (ReferenceScore("10", "shorta", .5), ReferenceScore("2", "idara", .5))
        for scores in (rows, tuple(reversed(rows))):
            dino = DinoScores("visual", "succeeded", scores, "injected-dino", "gallery")
            decision = run(replace(sample(), visual=dino))
            self.assertEqual((decision.category, decision.nearest_reference_id), ("idara", "2"))
            self.assertTrue(decision.visual_tie)
            self.assertEqual(decision.category_margin, 0)

    def test_visual_tie_tolerance_matches_source(self):
        rows = (ReferenceScore("2", "idara", .5), ReferenceScore("10", "shorta", .5 + 5e-13))
        decision = run(replace(sample(), visual=DinoScores("visual", "succeeded", rows, "dino", "gallery")))
        self.assertEqual(decision.category, "idara")
        self.assertEqual(decision.nearest_similarity, .5 + 5e-13)
        self.assertTrue(decision.visual_tie)

    def test_arabic_inference_failure_retains_source_visual_fallback(self):
        failed = OCR("category_ocr", "failed", model_ref="injected-arabic")
        decision = run(replace(sample(), category_ocr=failed))
        self.assertEqual((decision.category, decision.status), ("idara", "accepted"))
        self.assertEqual(decision.warnings, ("category_ocr_failed_visual_fallback",))
        self.assertEqual(dict(decision.evidence_used)["category_ocr"], failed)

    def test_missing_category_ocr_does_not_fabricate_negative_nt_evidence(self):
        decision = run(replace(sample(), category_ocr=None))
        self.assertEqual((decision.status, decision.reason_kind), ("unresolved", "missing_evidence"))
        self.assertIsNone(decision.category)
        self.assertNotIn("visual", dict(decision.evidence_used))

    def test_unfinished_category_ocr_cannot_be_bypassed_with_visual_scores(self):
        for state, kind in (("pending", "pending_evidence"), ("unknown", "unknown_outcome"),
                            ("not_started", "missing_evidence")):
            decision = run(replace(sample(), category_ocr=OCR("category_ocr", state)))
            self.assertEqual(decision.reason_kind, kind)
            self.assertNotIn("visual", dict(decision.evidence_used))

    def test_registration_ocr_cannot_substitute_for_category_ocr(self):
        evidence = replace(sample("idara", "ن ت 0007"), category_ocr=OCR("category_ocr", "failed"))
        decision = run(evidence)
        self.assertEqual(decision.category, "idara")
        self.assertEqual(decision.reason_kind, "conflict")

    def test_visual_execution_states_are_distinct(self):
        for state, kind in (("missing", "missing_evidence"), ("not_started", "missing_evidence"),
                            ("pending", "pending_evidence"), ("failed", "inference_failure"),
                            ("unknown", "unknown_outcome")):
            with self.subTest(state=state):
                decision = run(replace(sample(), visual=DinoScores("visual", state)))
                self.assertEqual((decision.status, decision.reason_kind), ("unresolved", kind))
                self.assertEqual(decision.reason, "visual_" + state)

    def test_registration_states_keep_successful_category_without_accepting_number(self):
        for state in ("missing", "not_started", "pending", "failed", "unknown"):
            with self.subTest(state=state):
                decision = run(replace(sample(), registration=OCR("registration", state)))
                self.assertEqual(decision.category, "idara")
                self.assertEqual(decision.status, "unresolved")
                self.assertIsNone(decision.normalized_registration)
                self.assertFalse(decision.registration_fields)
                self.assertFalse(decision.observed_text)

    def test_completed_empty_ocr_is_not_missing_or_failed_execution(self):
        for category in ("idara", "nt", "shorta", "tunis"):
            decision = run(sample(category, ""))
            self.assertEqual((decision.status, decision.reason_kind), ("unresolved", "invalid_format"))
            self.assertEqual(decision.observed_text, (("registration", ""),))

    def test_unicode_digits_and_leading_zeros_for_each_category(self):
        cases = (("idara", "٠٠١ — ۰۰۲", "001 - 002"), ("nt", "ن ت ０００７", "0007"),
                 ("shorta", "شرطة ٠٠۰۷", "0007"), ("tunis", "٠٠١ تونس ۰۰۰۷", "001 0007"))
        for category, text, expected in cases:
            with self.subTest(category=category):
                decision = run(sample(category, text))
                self.assertEqual(decision.status, "accepted")
                self.assertEqual(decision.normalized_registration, expected)
                self.assertEqual(decision.observed_text[0][1], text)

    def test_idara_requires_an_observed_separator_and_preserves_groups(self):
        for separator in ("-", "—", "−", "－"):
            d = run(sample("idara", "إدارة ٠٠١ " + separator + " ۰۰۲"))
            self.assertEqual(dict(d.registration_fields), {"first_group": "001", "second_group": "002"})
        for text, reason in (("001002", "missing_idara_separator"), ("001 002", "missing_idara_separator"),
                             ("001-002-003", "ambiguous_digit_groups"), ("ABC-002", "unexpected_ocr_text")):
            d = run(sample("idara", text))
            self.assertEqual((d.status, d.reason), ("unresolved", reason))

    def test_nt_and_shorta_do_not_join_ambiguous_digit_groups(self):
        for category in ("nt", "shorta"):
            for text in ("00 07", "00-07"):
                d = run(sample(category, text))
                self.assertEqual(d.reason, "ambiguous_digit_groups")

    def test_optional_whole_category_labels_are_not_added_to_observed_registration(self):
        for category, prefix in (("nt", "NT"), ("nt", "ن ت"), ("shorta", "POLICE"), ("shorta", "شرطة")):
            d = run(sample(category, prefix + " 0007"))
            self.assertEqual(d.normalized_registration, "0007")
            self.assertEqual(d.observed_text[0][1], prefix + " 0007")
            self.assertEqual(d.display_label, category)

    def test_unknown_letters_and_missing_registration_digits_remain_unresolved(self):
        for category, text in (("nt", "NT"), ("shorta", "شرطة"), ("nt", "00O7"), ("shorta", "?0007")):
            self.assertEqual(run(sample(category, text)).status, "unresolved")

    def test_registration_and_category_ocr_contradictions_block_number(self):
        for category, text in (("idara", "شرطة 001-002"), ("nt", "تونس 0007"), ("shorta", "NT 0007")):
            d = run(sample(category, text))
            self.assertEqual((d.category, d.reason_kind), (category, "conflict"))
        evidence = replace(sample(), category_ocr=ocr("category_ocr", "تونس"))
        self.assertEqual(run(evidence).reason, "contradictory_category_marker")

    def test_category_success_with_rejected_number_does_not_expose_accepted_fields(self):
        d = run(sample("idara", "001 002", 1.0))
        self.assertEqual(d.category, "idara")
        self.assertEqual((d.status, d.reason_kind), ("unresolved", "invalid_format"))
        self.assertIsNone(d.display_text)
        self.assertFalse(d.registration_fields)
        self.assertEqual(dict(d.evidence_used)["registration"].confidence, 1.0)

    def test_whole_policies_use_exact_native_score_boundary(self):
        for category, text in (("idara", "001-002"), ("nt", "0007"), ("shorta", "0007")):
            for confidence, accepted in ((math.nextafter(.9, 0), False), (.9, True), (math.nextafter(.9, 1), True)):
                d = run(sample(category, text, confidence))
                self.assertEqual(d.status == "accepted", accepted)

    def test_tunis_preserves_source_rounded_reader_boundary_in_both_modes(self):
        for confidence, accepted in ((.89999, True), (.89994, False), (.9, True)):
            evidence = replace(sample("tunis", "001T0007", confidence),
                               left=ocr("left", "001", confidence), right=ocr("right", "0007", confidence))
            for mode in ("legacy_whole", "marker_first"):
                self.assertEqual(run(evidence, mode).status == "accepted", accepted)

    def test_legacy_ambiguous_read_requires_matching_confident_regions(self):
        evidence = replace(sample("tunis", "001007"), left=ocr("left", "001", .5), right=ocr("right", "007", .5))
        d = run(evidence)
        self.assertEqual((d.status, d.normalized_registration), ("accepted", "001 007"))
        self.assertEqual([role for role, _ in d.observed_text], ["registration", "left", "right"])
        self.assertEqual(run(replace(evidence, right=ocr("right", "007", .49994))).reason, "ambiguous_fields")

    def test_legacy_conflicting_regions_do_not_resolve_ambiguous_read(self):
        evidence = replace(sample("tunis", "001007"), left=ocr("left", "002"), right=ocr("right", "007"))
        self.assertEqual(run(evidence).reason, "conflicting_readings")

    def test_legacy_explicit_read_does_not_use_unrequested_regional_failures(self):
        evidence = replace(sample("tunis", "001T0007"), left=OCR("left", "failed"), right=OCR("right", "unknown"))
        d = run(evidence)
        self.assertEqual(d.status, "accepted")
        self.assertNotIn("left", dict(d.evidence_used))

    def test_marker_first_does_not_use_or_invent_whole_crop_registration(self):
        evidence = replace(sample("tunis"), registration=OCR("registration", "failed"),
                           left=ocr("left", "٠٠١"), right=ocr("right", "۰۰۰۷"))
        d = run(evidence, "marker_first")
        self.assertEqual((d.status, d.normalized_registration), ("accepted", "001 0007"))
        self.assertEqual(d.observed_text, (("left", "٠٠١"), ("right", "۰۰۰۷")))
        self.assertNotIn("registration", dict(d.evidence_used))

    def test_marker_first_missing_and_invalid_region_geometry(self):
        evidence = replace(sample("tunis"), left=ocr("left", "001"), right=ocr("right", "0007"))
        self.assertEqual(run(replace(evidence, crop_size=None), "marker_first").reason_kind, "missing_evidence")
        for box in ((0, 0, 60, 30), (40, 0, 100, 30), (40, 0, 110, 30)):
            d = run(replace(evidence, marker=replace(evidence.marker, bbox=box)), "marker_first")
            self.assertEqual(d.reason, "invalid_marker_regions")

    def test_marker_first_missing_failed_unknown_and_invalid_sides_do_not_fall_back(self):
        evidence = replace(sample("tunis", "001T0007"), left=ocr("left", "001"), right=ocr("right", "0007"))
        for right, kind in ((None, "missing_evidence"), (OCR("right", "failed"), "inference_failure"),
                            (OCR("right", "unknown"), "unknown_outcome"), (ocr("right", "00O7"), "invalid_format")):
            self.assertEqual(run(replace(evidence, right=right), "marker_first").reason_kind, kind)

    def test_missing_marker_is_distinct_from_successful_no_marker(self):
        evidence = sample("tunis", "001T0007")
        self.assertEqual(run(replace(evidence, marker=None)).reason_kind, "missing_evidence")
        no_marker = Marker("marker", "succeeded", False, 0.0, model_ref="injected-marker")
        self.assertEqual(run(replace(evidence, marker=no_marker)).reason_kind, "insufficient_evidence")

    def test_marker_score_boundary_and_tunis_category_conflict(self):
        evidence = sample("tunis", "001T0007")
        self.assertEqual(run(replace(evidence, marker=replace(evidence.marker, confidence=.5))).status, "accepted")
        self.assertEqual(run(replace(evidence, marker=replace(evidence.marker, confidence=math.nextafter(.5, 0)))).status, "unresolved")
        self.assertEqual(run(replace(evidence, category_ocr=ocr("category_ocr", "شرطة"))).reason, "conflicting_category_evidence")

    def test_tunis_field_lengths_and_ambiguous_markerless_reads(self):
        for text in ("0001T0007", "001T00007", "00O1T0007", "001 تونس", "0010007"):
            d = run(replace(sample("tunis", text), marker=Marker("marker", "succeeded", False, 0., model_ref="marker")))
            self.assertEqual(d.status, "unresolved")

    def test_thresholds_are_injected_without_a_visual_threshold(self):
        self.assertEqual(run(sample("nt", "0007", .8)).status, "unresolved")
        self.assertEqual(run(sample("nt", "0007", .8), thresholds=Thresholds(whole_ocr=.8)).status, "accepted")
        self.assertEqual(normalize_digits("٠۰０"), "000")
        self.assertEqual(normalize_ocr("  ن ت ٠٠٧  "), "ن ت 007")

    def test_decision_retains_mode_thresholds_and_scalar_geometry(self):
        settings = Thresholds(whole_ocr=.8)
        evidence = sample()
        decision = run(evidence, "marker_first", thresholds=settings)
        self.assertEqual(decision.tunis_mode, "marker_first")
        self.assertEqual(decision.thresholds, settings)
        self.assertEqual(decision.crop_size, evidence.crop_size)
        self.assertEqual(decision.policy_version, "mohamed-lpr-plate-policy-phase1-v1")

    def test_marker_first_private_and_regional_thresholds_are_independent(self):
        evidence = replace(sample("tunis"), left=ocr("left", "001", .4), right=ocr("right", "0007", .4))
        settings = Thresholds(private_ocr=.3)
        self.assertEqual(run(evidence, "marker_first", thresholds=settings).reason, "insufficient_ocr_confidence")

    def test_typed_immutable_evidence_and_no_shared_state(self):
        evidence = sample()
        before = repr(evidence)
        first = run(evidence)
        run(sample("nt", "0007"))
        self.assertEqual(run(evidence), first)
        self.assertEqual(repr(evidence), before)
        with self.assertRaises(FrozenInstanceError):
            first.category = "nt"
        with self.assertRaises(ValueError):
            replace(evidence, registration=replace(evidence.registration, evidence_id="category_ocr"))

    def test_invalid_scores_and_payloads_cannot_be_treated_as_absence(self):
        for value in (True, "0.9", float("nan"), float("inf"), -.01, 1.01):
            with self.assertRaises(ValueError):
                ocr("registration", "synthetic", value)
        for value in (float("nan"), -1.01, 1.01, True):
            with self.assertRaises(ValueError):
                ReferenceScore("1", "idara", value)
        with self.assertRaises(ValueError):
            OCR("registration", "failed", "synthetic", .9)
        with self.assertRaises(ValueError):
            DinoScores("visual", "succeeded", (), "dino", "index")
        with self.assertRaises(ValueError):
            ReferenceScore("1", "nt", .9)
        with self.assertRaises(ValueError):
            DinoScores("visual", "succeeded", [ReferenceScore("1", "idara", .9)], "dino", "index")

    def test_fresh_standard_library_only_import_and_execution(self):
        root = str(Path(__file__).resolve().parents[3])
        code = '''
import sys
class Guard:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'numpy','cv2','torch','paddle','paddleocr','django','redis','psycopg','onnxruntime','ultralytics'}:
            raise AssertionError('forbidden dependency')
sys.meta_path.insert(0, Guard())
sys.path.insert(0, sys.argv[1])
from vehicle.common.mohamed_plate_policy import Evidence, decide
result = decide(Evidence(), tunis_mode='legacy_whole')
assert result.reason_kind == 'missing_evidence'
assert not any(name.startswith('vehicle.common.plate_requests') for name in sys.modules)
print('pure import and decision passed')
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, root], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pure import and decision passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
