"""Invented scalar executions only. No native imports or infrastructure."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
import unittest

from vehicle.common.mohamed_lpr_contract import (
    Crop, Dependency, Provenance, ReferenceIndex, Request, Result, VERSION,
    canonical, decode, digest, encode, request,
)
from vehicle.common.mohamed_lpr_harness import EngineLedger, Harness, PlateSession
from vehicle.common.mohamed_plate_policy import Evidence, ReferenceScore, Thresholds, decide


def crop(**changes):
    values = dict(org_id="org", run_id="run", camera_id="camera", session_id="session",
                  vehicle_lifetime_id="vehicle-lifetime", plate_track_id="plate-7",
                  plate_track_epoch="tracker-epoch", association_id="association",
                  candidate_id="candidate", frame_id="frame-1", frame_sha256="a" * 64,
                  crop_id="crop-1", crop_sha256="b" * 64, width=100, height=30, generation=1)
    return Crop(**(values | changes))


def result(r, *, category="idara", text=None, confidence=.96, outcome="succeeded", **changes):
    payload = dict(request=r, execution_id="execution-" + r.operation_id,
                   executor_ref="synthetic-executor", outcome=outcome, retired=True,
                   provenance=Provenance("synthetic-" + r.role, "c" * 64, "d" * 64,
                                         "e" * 64, "synthetic-runtime", "injected-scalar"))
    if outcome != "succeeded":
        payload.update(error_code="synthetic_" + outcome)
    elif r.role == "visual":
        entries = (("1", "idara"), ("2", "shorta"), ("3", "tunis"))
        payload.update(reference_index=ReferenceIndex("synthetic-index", "f" * 64, entries),
                       scores=tuple(ReferenceScore(i, c, .3 if c == category else -.95) for i, c in entries))
    elif r.role == "marker":
        payload.update(detected=True, confidence=confidence, bbox=(40, 0, 60, 30))
    else:
        default = {"category_ocr": "ن ت" if category == "nt" else "", "registration": "001 - 002",
                   "left": "001", "right": "0007"}[r.role]
        payload.update(raw_text=default if text is None else text, confidence=confidence)
    return Result(**(payload | changes))


def drive(category, *, mode, text=None, overrides=None, harness=None):
    h = harness or Harness(crop(), tunis_mode=mode)
    changes = overrides or {}
    for _ in range(8):
        work = h.advance()
        if not work:
            return h
        for r in reversed(work):  # Independent regions intentionally arrive right first.
            args = dict(category=category)
            if r.role == "registration" and text is not None:
                args["text"] = text
            args.update(changes.get(r.role, {}))
            h.inject(result(r, **args))
    raise AssertionError("follow-up loop exceeded bounded operation set")


class HarnessTests(unittest.TestCase):
    def test_all_categories_and_tunis_modes_match_pinned_policy(self):
        for category, text in (("nt", "٠٠٠٧"), ("shorta", "0007"),
                               ("idara", "٠٠١ — ٠٠٢"), ("tunis", "001T0007")):
            for mode in ("legacy_whole", "marker_first"):
                with self.subTest(category=category, mode=mode):
                    h = drive(category, mode=mode, text=text)
                    decision = h.seal().decision
                    self.assertEqual((decision.category, decision.status), (category, "accepted"))
                    evidence = Evidence(**{r.request.role: r.evidence() for r in h.engine.current_results},
                                        crop_size=(100, 30))
                    self.assertEqual(decision, decide(evidence, tunis_mode=mode))
                    self.assertEqual(h.seal(), h.seal())
                    self.assertEqual(h.advance(), ())

    def test_mode_is_required_and_truthiness_not_supported(self):
        with self.assertRaises(TypeError):
            Harness(crop())
        for mode in ("0", "", False, None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                Harness(crop(), tunis_mode=mode)

    def test_nt_skips_dino_and_separates_display_from_observation(self):
        h = drive("nt", mode="legacy_whole", text="٠٠٠٧")
        self.assertEqual([r.role for r in h.engine.scheduled], ["category_ocr", "registration"])
        d = h.seal().decision
        self.assertEqual(d.normalized_registration, "0007")
        self.assertEqual(d.observed_text, (("registration", "٠٠٠٧"),))
        self.assertEqual(d.display_label, "nt")

    def test_contradictory_nt_still_precedes_dino_and_rejects_number(self):
        h = drive("nt", mode="marker_first", text="0007",
                  overrides={"category_ocr": dict(text="ن ت شرطة")})
        d = h.seal().decision
        self.assertEqual((d.category, d.reason_kind), ("nt", "conflict"))
        self.assertNotIn("visual", [r.role for r in h.engine.scheduled])

    def test_category_success_does_not_accept_invalid_number(self):
        for category, bad in (("nt", "00 07"), ("shorta", "00-07"),
                              ("idara", "001002"), ("tunis", "001T00077")):
            h = drive(category, mode="legacy_whole", text=bad)
            d = h.seal().decision
            self.assertEqual((d.category, d.status, d.reason_kind), (category, "unresolved", "invalid_format"))
            self.assertIsNone(d.normalized_registration)

    def test_dino_only_after_completed_negative_or_failed_arabic(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        self.assertEqual(r.role, "category_ocr")
        self.assertEqual(h.plate.decision.reason_kind, "pending_evidence")
        self.assertEqual(h.advance(), ())
        h.inject(result(r, text=""))
        visual, = h.advance()
        self.assertEqual(visual.role, "visual")
        self.assertEqual(visual.dependencies, (Dependency(r.operation_id, digest(h.engine.current_results[0])),))

    def test_actual_category_failure_has_visual_fallback_without_external_calls(self):
        h = drive("idara", mode="legacy_whole", overrides={"category_ocr": dict(outcome="failed")})
        self.assertEqual(h.seal().decision.status, "accepted")
        self.assertEqual(h.plate.decision.warnings, ("category_ocr_failed_visual_fallback",))

    def test_missing_unknown_and_failure_outcomes_remain_distinct(self):
        for role in ("category_ocr", "visual", "registration", "marker", "left", "right"):
            for outcome, kind in (("missing", "missing_evidence"), ("unknown", "unknown_outcome"),
                                  ("failed", "inference_failure")):
                category = "tunis" if role in {"marker", "left", "right"} else "idara"
                mode = "marker_first" if category == "tunis" else "legacy_whole"
                h = drive(category, mode=mode, overrides={role: dict(outcome=outcome)})
                d = h.seal().decision
                if role == "category_ocr" and outcome == "failed":
                    self.assertEqual(d.status, "accepted")
                else:
                    self.assertEqual((d.status, d.reason_kind, d.reason), ("unresolved", kind, role + "_" + outcome))
                if role == "category_ocr" and outcome != "failed":
                    self.assertEqual(len(h.engine.scheduled), 1)

    def test_tunis_legacy_orders_whole_then_marker_then_optional_regions(self):
        h = drive("tunis", mode="legacy_whole", text="0010007")  # Unique split; no regions.
        self.assertEqual([r.role for r in h.engine.scheduled], ["category_ocr", "visual", "registration", "marker"])
        h = drive("tunis", mode="legacy_whole", text="001007",
                  overrides={"right": dict(text="007")})
        self.assertEqual([r.role for r in h.engine.scheduled],
                         ["category_ocr", "visual", "registration", "marker", "left", "right"])
        left, right = h.engine.scheduled[-2:]
        self.assertEqual(left.dependencies, right.dependencies)
        self.assertEqual((left.region_xyxy, right.region_xyxy), ((0, 0, 40, 30), (60, 0, 100, 30)))
        self.assertEqual(h.seal().decision.normalized_registration, "001 007")

    def test_tunis_marker_first_has_no_whole_prerequisite(self):
        h = drive("tunis", mode="marker_first")
        self.assertEqual([r.role for r in h.engine.scheduled], ["category_ocr", "visual", "marker", "left", "right"])
        self.assertEqual(h.engine.scheduled[-1].dependencies, h.engine.scheduled[-2].dependencies)
        self.assertEqual(h.seal().decision.observed_text, (("left", "001"), ("right", "0007")))

    def test_bad_marker_stops_regions_and_preserves_source_reason(self):
        for mode in ("legacy_whole", "marker_first"):
            h = drive("tunis", mode=mode, text="001T0007",
                      overrides={"marker": dict(detected=False, bbox=None)})
            self.assertEqual(h.seal().decision.reason, "insufficient_marker_evidence")
            self.assertNotIn("left", [r.role for r in h.engine.scheduled])
        h = drive("tunis", mode="marker_first", overrides={"marker": dict(bbox=(0, 0, 60, 30))})
        self.assertEqual(h.seal().decision.reason, "invalid_marker_regions")

    def test_ambiguous_and_conflicting_legacy_readings(self):
        h = drive("tunis", mode="legacy_whole", text="001007")
        self.assertEqual(h.seal().decision.reason, "conflicting_readings")
        h = drive("tunis", mode="legacy_whole", text="001007",
                  overrides={"marker": dict(detected=False, bbox=None)})
        self.assertEqual(h.seal().decision.reason, "ambiguous_fields")

    def test_score_boundaries_remain_source_rules(self):
        for confidence, accepted in ((.9, True), (.89999, False)):
            h = drive("idara", mode="marker_first", overrides={"registration": dict(confidence=confidence)})
            self.assertEqual(h.seal().decision.status == "accepted", accepted)
        h = drive("tunis", mode="legacy_whole", text="001T0007",
                  overrides={"registration": dict(confidence=.89999), "marker": dict(confidence=.5)})
        self.assertEqual(h.seal().decision.status, "accepted")  # Retained source rounding.
        h = Harness(crop(), tunis_mode="marker_first", thresholds=Thresholds(private_ocr=.5))
        h = drive("tunis", mode="marker_first", harness=h,
                  overrides={"left": dict(confidence=.5), "right": dict(confidence=.5),
                             "marker": dict(confidence=.5)})
        self.assertEqual(h.seal().decision.status, "accepted")
        for score in (-1., 0., 1.):
            rows = (ReferenceScore("1", "idara", score), ReferenceScore("2", "shorta", -1.),
                    ReferenceScore("3", "tunis", -1.))
            h = drive("idara", mode="legacy_whole", overrides={"visual": dict(scores=rows)})
            self.assertEqual(h.seal().decision.status, "accepted")

    def test_identical_replay_never_schedules_again(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        self.assertEqual(h.engine.admit(decode(encode(r))), "replay")
        v = result(r, category="nt")
        h.inject(v)
        next_work = h.advance()
        for _ in range(5):
            self.assertEqual(h.inject(decode(encode(v))), "replay")
            self.assertEqual(h.advance(), ())
        self.assertEqual(len(h.engine.scheduled), 2)
        h.inject(result(next_work[0], text="0007"))
        seal = h.seal()
        self.assertEqual(h.inject(v), "sealed")
        self.assertIs(h.seal(), seal)

    def test_conflicting_retry_blocks_seal_and_keeps_original_evidence(self):
        h = drive("nt", mode="legacy_whole", text="0007")
        original = h.engine.current_results[-1]
        for mutation in (dict(raw_text="0008"), dict(execution_id="other-execution"),
                         dict(provenance=replace(original.provenance, config_sha256="0" * 64))):
            with self.assertRaisesRegex(ValueError, "conflicting_result_retry"):
                h.inject(replace(original, **mutation))
            self.assertEqual(h.engine.current_results[-1], original)
            with self.assertRaises(ValueError):
                h.seal()

    def test_plate_independently_blocks_conflicting_retry(self):
        h = drive("nt", mode="legacy_whole", text="0007")
        with self.assertRaisesRegex(ValueError, "conflicting_result_retry"):
            h.plate.receive(replace(h.engine.current_results[-1], raw_text="0008"))
        with self.assertRaises(ValueError):
            h.seal()

    def test_wrong_crop_frame_parent_generation_and_dependency_are_rejected(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        for changes in (dict(crop_id="other"), dict(frame_id="other"), dict(frame_sha256="0" * 64),
                        dict(crop_sha256="0" * 64), dict(vehicle_lifetime_id="other"),
                        dict(plate_track_epoch="other"), dict(association_id="other"), dict(generation=2)):
            other = request(replace(r.crop, **changes), r.role, sequence=1, tunis_mode=r.tunis_mode,
                            thresholds=r.thresholds)
            with self.assertRaises(ValueError):
                h.engine.admit(other)
            with self.assertRaises(ValueError):
                h.inject(result(other))
        h.inject(result(r, category="nt"))
        good, = h.advance()
        bad = request(good.crop, good.role, sequence=2, tunis_mode=good.tunis_mode, thresholds=good.thresholds,
                      dependencies=(Dependency(r.operation_id, "0" * 64),))
        with self.assertRaises(ValueError):
            h.engine.admit(bad)
        with self.assertRaises(ValueError):
            h.inject(result(bad))

    def test_admission_rejects_missing_dependencies_policy_changes_and_wrong_region(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        h.inject(result(r, category="nt"))
        deps = (Dependency(r.operation_id, digest(h.engine.current_results[0])),)
        for kwargs in (dict(dependencies=()), dict(tunis_mode="marker_first"),
                       dict(thresholds=Thresholds(whole_ocr=.8)), dict(region_xyxy=(1, 0, 100, 30))):
            args = dict(sequence=2, tunis_mode="legacy_whole", thresholds=Thresholds(), dependencies=deps)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                h.engine.admit(request(crop(), "registration", **args))

    def test_reordered_siblings_produce_identical_request_ids_and_seal(self):
        a = Harness(crop(), tunis_mode="marker_first")
        b = Harness(crop(), tunis_mode="marker_first")
        for _ in range(3):
            for h in (a, b):
                r, = h.advance()
                h.inject(result(r, category="tunis"))
        ar, br = a.advance(), b.advance()
        self.assertEqual(ar, br)
        for r in ar:
            a.inject(result(r))
        for r in reversed(br):
            b.inject(result(r))
        self.assertEqual(a.seal(), b.seal())

    def test_unscheduled_out_of_order_result_cannot_create_future_work(self):
        h = Harness(crop(), tunis_mode="marker_first")
        h.advance()
        r = request(crop(), "registration", sequence=2, tunis_mode="marker_first", thresholds=Thresholds())
        with self.assertRaisesRegex(ValueError, "unsolicited_result"):
            h.inject(result(r))
        self.assertEqual(len(h.engine.scheduled), 1)

    def test_better_crop_fences_stale_work_but_waits_for_retirement(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        old, = h.advance()
        h.recheck(crop(candidate_id="better", crop_id="crop-2", crop_sha256="0" * 64,
                       frame_id="frame-2", frame_sha256="1" * 64, generation=2))
        h = drive("nt", mode="legacy_whole", text="0009", harness=h)
        with self.assertRaisesRegex(ValueError, "executions_not_retired"):
            h.seal()
        self.assertEqual(h.inject(result(old, category="shorta")), "stale_retired")
        d = h.seal().decision
        self.assertEqual((d.category, d.normalized_registration), ("nt", "0009"))
        self.assertNotIn(old.operation_id, [e.evidence_id for _, e in d.evidence_used])

    def test_recheck_rejects_parent_changes_and_same_generation_atomically(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        for c in (crop(crop_id="new"), crop(crop_id="new", generation=2, association_id="other")):
            with self.assertRaises(ValueError):
                h.recheck(c)
            self.assertEqual(h.plate.crop, h.engine.crop)
            self.assertEqual(h.plate.crop.generation, 1)

    def test_stale_registration_cannot_replace_a_new_crop_number(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        arabic, = h.advance()
        h.inject(result(arabic, category="nt"))
        old_read, = h.advance()
        h.recheck(crop(crop_id="better", crop_sha256="0" * 64, generation=2))
        h = drive("idara", mode="legacy_whole", text="003 - 004", harness=h)
        self.assertEqual(h.inject(result(old_read, text="9999")), "stale_retired")
        self.assertEqual(h.seal().decision.normalized_registration, "003 - 004")

    def test_conflict_cannot_schedule_any_more_engine_work(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        h.inject(result(r, category="nt"))
        with self.assertRaises(ValueError):
            h.inject(result(r, text=""))
        before = h.plate.requests
        with self.assertRaises(ValueError):
            h.advance()
        self.assertEqual(h.plate.requests, before)

    def test_premature_seals_reject_without_freezing(self):
        h = Harness(crop(), tunis_mode="marker_first")
        with self.assertRaises(ValueError):
            h.seal()
        for _ in range(3):
            r, = h.advance()
            with self.assertRaises(ValueError):
                h.seal()
            h.inject(result(r, category="tunis"))
            with self.assertRaises(ValueError):
                h.seal()
        left, right = h.advance()
        h.inject(result(right))
        with self.assertRaises(ValueError):
            h.seal()
        h.inject(result(left))
        self.assertEqual(h.seal().decision.status, "accepted")

    def test_late_conflicting_and_unsolicited_evidence_cannot_change_seal(self):
        h = drive("nt", mode="legacy_whole", text="0007")
        sealed = h.seal()
        before = digest(sealed)
        self.assertEqual(h.inject(replace(h.engine.current_results[-1], raw_text="0008")), "sealed")
        rogue = request(crop(crop_id="other"), "category_ocr", sequence=1,
                        tunis_mode="legacy_whole", thresholds=Thresholds())
        self.assertEqual(h.inject(result(rogue)), "sealed")
        self.assertEqual(digest(h.seal()), before)
        with self.assertRaises(ValueError):
            h.recheck(crop(crop_id="crop-2", generation=2))

    def test_seal_requires_engine_and_plate_to_share_exact_evidence(self):
        h = drive("nt", mode="legacy_whole", text="0007")
        other = drive("nt", mode="legacy_whole", text="0008")
        with self.assertRaisesRegex(ValueError, "seal_evidence_mismatch"):
            h.plate.seal(other.engine)


class ContractTests(unittest.TestCase):
    def test_all_request_and_result_shapes_round_trip_and_validate_schema(self):
        from jsonschema import Draft202012Validator
        path = Path(__file__).resolve().parents[3] / "contracts/vehicle_mohamed_lpr_v3.schema.json"
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        h = drive("tunis", mode="legacy_whole", text="001007", overrides={"right": dict(text="007")})
        for v in (*h.engine.scheduled, *h.engine.current_results):
            self.assertEqual(decode(encode(v)), v)
            validator.validate(json.loads(encode(v)))
        for outcome in ("failed", "missing", "unknown"):
            v = result(h.engine.scheduled[0], outcome=outcome, provenance=None)
            validator.validate(json.loads(encode(v)))
            self.assertEqual(decode(encode(v)), v)

    def test_codec_rejects_open_shapes_tampering_duplicate_keys_and_nonfinite(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        v = json.loads(encode(result(r)))
        mutations = []
        for where, key, value in (((), "extra", True), (("body",), "extra", True),
                                  (("body", "request"), "role", "marker"),
                                  (("body", "request", "crop"), "width", True),
                                  (("body",), "outcome", "pending"),
                                  (("body",), "retired", False),
                                  (("body", "provenance"), "artifact_sha256", "path-to-model")):
            copy = json.loads(json.dumps(v))
            part = copy
            for k in where:
                part = part[k]
            part[key] = value
            mutations.append(canonical(copy))
        mutations += [b'{"version":1,"version":2}', b'{"value":NaN}', b'[' * 2000, b'x' * 65537]
        for raw in mutations:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                decode(raw)

    def test_provenance_is_actual_and_part_of_dependency_generation(self):
        h = Harness(crop(), tunis_mode="legacy_whole")
        r, = h.advance()
        a = result(r, category="nt")
        b = replace(a, provenance=replace(a.provenance, device="other-device", config_sha256="0" * 64))
        self.assertNotEqual(digest(a), digest(b))
        h.inject(b)
        follow, = h.advance()
        self.assertEqual(follow.dependencies, (Dependency(r.operation_id, digest(b)),))
        self.assertEqual(b.evidence().model_ref, digest(b.provenance))

    def test_success_requires_provenance_failure_has_no_success_payload(self):
        r, = Harness(crop(), tunis_mode="legacy_whole").advance()
        for changes in (dict(provenance=None), dict(confidence=float("nan")),
                        dict(outcome="failed", error_code="error"), dict(retired=False), dict(confidence=True)):
            with self.assertRaises(ValueError):
                replace(result(r), **changes)
        v = result(r, outcome="failed", provenance=None)
        self.assertIsNone(v.evidence().model_ref)

    def test_dino_requires_complete_ordered_index_and_cosines_not_probabilities(self):
        r = request(crop(), "visual", sequence=2, tunis_mode="legacy_whole", thresholds=Thresholds())
        valid = result(r)
        for changes in (dict(reference_index=None), dict(scores=valid.scores[:-1]),
                        dict(scores=tuple(reversed(valid.scores))),
                        dict(scores=(ReferenceScore("wrong", "idara", .3),) + valid.scores[1:]),
                        dict(raw_text="not-a-score")):
            with self.assertRaises(ValueError):
                replace(valid, **changes)
        with self.assertRaises(ValueError):
            ReferenceScore("1", "nt", .9)
        with self.assertRaises(ValueError):
            ReferenceScore("1", "idara", 1.01)

    def test_no_native_transport_or_database_imports(self):
        script = """
import sys
from vehicle.common.mohamed_lpr_harness import Harness
from vehicle.common.tests.test_mohamed_lpr_harness import crop
assert not ({'torch','cv2','paddle','django','redis','psycopg','psycopg2'} & set(sys.modules))
assert Harness(crop(), tunis_mode='marker_first').advance()[0].role == 'category_ocr'
"""
        run = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
