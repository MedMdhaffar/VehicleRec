"""Owned native Redis + injected models. No footage, GPU or external service."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from uuid import uuid4
from datetime import timedelta

from vehicle.common.tests import test_mohamed_lpr_engine as injected
from vehicle.common.tests.test_mohamed_lpr_engine import Runtime
from vehicle.common.mohamed_lpr_contract import Dependency, Result, canonical, digest, request
from vehicle.common.mohamed_plate_policy import Thresholds
from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis, client_preflight
from vehicle.common.mohamed_lpr_stream_codec import Codec, Scope, STREAMS, hashed, local
from vehicle.common.mohamed_lpr_stream_policy import memory_replay, replay
from vehicle.common.mohamed_lpr_streams import LiveEngine, PlateWorker, EnginePeer, V3Journal, pending, pump

SERVER = Path(os.environ.get("VEHICLE_REDIS_SERVER", "/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server"))


class Crash(RuntimeError):
    pass


class StreamsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SERVER.is_file():
            raise RuntimeError("owned_native_redis_binary_missing")
        cls.owned = OwnedRedis(SERVER).__enter__()
        cls.client = cls.owned.client

    @classmethod
    def tearDownClass(cls):
        cls.owned.__exit__(None, None, None)
        if not cls.owned.cleaned:
            raise AssertionError("owned_redis_cleanup_failed")
        print('V3_REDIS_CLEANUP {"server_exited":true,"private_aof_directory_removed":true}', flush=True)

    def setUp(self):
        self.fixtures = []
        self.workers = []
        self.children = []
        self.lives = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for worker in self.workers:
            try:
                worker.journal.release()
            except Exception:
                pass
        for live in self.lives:
            for slot in live.slots.values():
                for adapter in slot.adapters.values():
                    if adapter.active is not None:
                        adapter.active["context"].retired()  # Controlled synchronous fake, no native reader remains.
                        adapter.poll_retirement()
                    adapter.release(discard=True)
        for fixture in self.fixtures:
            fixture.doCleanups()

    def make(self, category="nt", text="0007", mode="legacy_whole", *, runtime=None, thresholds=Thresholds(), worker_options=None):
        fixture = injected.EngineAdapterTests("runTest")
        fixture.setUp()
        self.fixtures.append(fixture)
        bound = fixture.bound()
        c = bound.scalar
        scope = Scope(c.org_id, c.run_id, c.camera_id, uuid4().hex)
        ns = "lpr" + uuid4().hex[:20]
        runtime = runtime or Runtime(category, text)
        live = LiveEngine(self.client, namespace=ns, scope=scope, runtime=runtime)
        self.lives.append(live)
        offered = live.add(bound, tunis_mode=mode, thresholds=thresholds)
        plate = PlateWorker(self.client, codec=live.codec, namespace=ns, consumer="plate", **(worker_options or {}))
        engine = EnginePeer(self.client, live=live, namespace=ns, consumer="engine")
        plate.setup(); engine.setup()
        self.workers.extend((plate, engine))
        return fixture, live, plate, engine, offered

    def rows(self, live, kind):
        return self.client.xrange(live.journal.key(STREAMS[kind]))

    def result(self, live):
        rows = self.rows(live, "plate_result")
        self.assertEqual(len(rows), 1)
        return live.codec.read(rows[0][1][b"json"])["body"]

    def parity(self, live, c):
        slot = live.slots[c.session_id]
        reference = memory_replay(c, slot.mode, slot.thresholds, live.captured(c.session_id))
        self.assertEqual(canonical(self.result(live)["decision"]), canonical(asdict(reference.decision)))
        return reference

    def finish(self, live, plate, engine):
        live.flush_offers()
        pump(plate, engine)
        self.assertEqual((pending(self.client, plate), pending(self.client, engine)), (0, 0))
        self.assertEqual(live.pending_native, 0)
        self.assertTrue(all(a.bound.resource.released and a.bound.resource.release_calls == 1
                            for s in live.slots.values() for a in s.adapters.values()))

    def inject_duplicate(self, live, kind, raw=None):
        rows = self.rows(live, kind)
        last = rows[-1][0].decode()
        major, minor = map(int, last.split("-"))
        return self.client.xadd(live.journal.key(STREAMS[kind]), {b"json": raw or rows[-1][1][b"json"]}, id=f"{major}-{minor + 1}")

    def test_four_categories_both_modes_exact_parity_counts_and_cleanup(self):
        for mode in ("legacy_whole", "marker_first"):
            for category, text in (("nt", "0007"), ("idara", "001 - 002"), ("shorta", "0007"), ("tunis", "001T0007")):
                with self.subTest(mode=mode, category=category):
                    _, live, p, e, c = self.make(category, text, mode)
                    self.finish(live, p, e)
                    d = self.parity(live, c).decision
                    self.assertEqual((d.category, d.status), (category, "accepted"))
                    expected = 2 if category == "nt" else 5 if category == "tunis" and mode == "marker_first" else 4 if category == "tunis" else 3
                    self.assertEqual(sum(live.runtime.calls.values()), expected)
                    self.assertTrue(all(n == 1 for n in live.runtime.calls.values()))
                    self.assertEqual(self.rows(live, "diagnostic"), [])

    def test_missing_transport_is_reported_without_model_or_media_dependency(self):
        from unittest.mock import patch
        with patch.dict(sys.modules, {"redis": None}):
            with self.assertRaisesRegex(RuntimeError, "^missing_runtime_package_redis$"):
                client_preflight()

    def test_duplicate_each_input_during_progress_does_not_repeat_native_work(self):
        _, live, p, e, c = self.make("tunis", "001T0007")
        live.flush_offers()
        self.inject_duplicate(live, "offer")
        p.step()
        self.inject_duplicate(live, "requests")
        e.step()
        self.inject_duplicate(live, "executions")
        self.finish(live, p, e)
        before = dict(live.runtime.calls)
        for kind in ("requests", "seal", "executions", "settled", "plate_result"):
            self.inject_duplicate(live, kind)
        for _ in range(30):
            p.step(); e.step()
        self.assertEqual(live.runtime.calls, before)
        self.assertEqual(len(self.rows(live, "plate_result")), 2)  # One external duplicate, no second producer publication.
        self.assertEqual((pending(self.client, p), pending(self.client, e)), (0, 0))

    def test_float_one_and_score_boundaries_keep_v3_identity(self):
        class Boundaries(Runtime):
            def invoke(self, role, image, context):
                result = super().invoke(role, image, context)
                if role != "visual":
                    result["confidence"] = 1.0
                return result
        rt = Boundaries("tunis", "001T0007")
        _, live, p, e, c = self.make(runtime=rt, thresholds=Thresholds(private_ocr=1.0))
        self.finish(live, p, e)
        self.parity(live, c)
        state = p.journal.read_state(c.session_id)
        self.assertIs(type(state["log"][0]["body"]["thresholds"]["private_ocr"]), float)

    def test_reordered_region_results_match_memory_path(self):
        _, live, p, e, c = self.make("tunis", mode="marker_first")
        original = e.apply
        def reordered(message):
            session, state, outputs = original(message)
            if message["kind"] == "requests" and len(message["body"]["items"]) == 2:
                v = live.codec.read(outputs[0][1])
                v["body"]["items"].reverse()
                outputs = [(STREAMS["executions"], live.codec.make("executions", v["body"]))]
            return session, state, outputs
        e.apply = reordered
        self.finish(live, p, e)
        self.parity(live, c)
        self.assertEqual(live.runtime.calls["left"], live.runtime.calls["right"])

    def test_execution_delivered_before_offer_waits_in_pel_then_progresses(self):
        _, live, p, e, c = self.make()
        from vehicle.common.mohamed_lpr_harness import Harness
        h = Harness(c, tunis_mode="legacy_whole")
        r, = h.advance()
        first = Result(r, "native-" + r.operation_id, "mohamed-cuda-engine-v3", "succeeded", True,
                       live.runtime.expected("category_ocr"), raw_text="ن ت", confidence=.97)
        # Transport reordering is bounded and cannot create requests itself.
        raw = live.codec.make("executions", dict(batch_id=hashed([r.operation_id]), items=[asdict(first)]))
        self.client.xadd(live.journal.key(STREAMS["executions"]), {"json": raw}, id="1-0")
        p.step()
        self.assertEqual(pending(self.client, p), 1)
        self.assertIsNone(p.journal.read_state(c.session_id))
        live.flush_offers()
        p.step()
        self.finish(live, p, e)
        self.parity(live, c)
        self.assertEqual(live.runtime.calls["category_ocr"], 1)

    def test_invalid_registration_after_category_success_stays_unresolved(self):
        for category, text in (("nt", "00 07"), ("idara", "001002"), ("shorta", "00-07"), ("tunis", "001T00077")):
            _, live, p, e, c = self.make(category, text)
            self.finish(live, p, e)
            d = self.parity(live, c).decision
            self.assertEqual((d.category, d.status, d.reason_kind), (category, "unresolved", "invalid_format"))

    def test_native_execution_failure_uses_phase1_fallback_and_error_states(self):
        class Failing(Runtime):
            def invoke(self, role, image, context):
                self.fail = role == "category_ocr"
                return super().invoke(role, image, context)
        _, live, p, e, c = self.make(runtime=Failing("idara", "001 - 002"))
        self.finish(live, p, e)
        d = self.parity(live, c).decision
        self.assertEqual(d.status, "accepted")
        self.assertEqual(d.warnings, ("category_ocr_failed_visual_fallback",))

    def test_fresh_engine_cannot_recover_lost_crop_process_from_redis(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step()
        with self.assertRaisesRegex(ValueError, "engine_crops_unrecoverable"):
            LiveEngine(self.client, namespace=p.journal.prefix.split(":")[0],
                       scope=replace(live.scope, engine_id=uuid4().hex), runtime=Runtime())
        self.assertFalse(live.slots[c.session_id].adapters[1].bound.resource.released)

    def test_v2_streams_groups_and_db1_keys_are_not_available(self):
        _, live, p, e, _ = self.make()
        self.finish(live, p, e)
        for suffix in ("plate.requests.v2", "results.plate", "db1.delivery.v1"):
            with self.assertRaises(ValueError):
                live.journal.key(suffix)
        keys = self.client.keys(live.journal.prefix + "*")
        self.assertTrue(all(b"vehicle-v1" not in k and b"db1" not in k for k in keys))
        self.assertNotEqual(p.journal.group, "plate-requests-v2")

    def test_private_handles_pixels_and_source_candidate_ids_do_not_cross_wire(self):
        fixture, live, p, e, _ = self.make()
        original = fixture.resources[0]
        self.finish(live, p, e)
        forbidden = (original.binding.handle_id, original.binding.candidate.candidate_id)
        def leaves(value):
            if isinstance(value, dict):
                return [s for v in value.values() for s in leaves(v)]
            if isinstance(value, list):
                return [s for v in value for s in leaves(v)]
            return [value] if isinstance(value, str) else []
        for kind in STREAMS:
            for _, fields in self.rows(live, kind):
                for value in forbidden:
                    self.assertNotIn(value, leaves(json.loads(fields[b"json"])))
        self.assertTrue(original.released)

    def test_stale_generation_never_executes_or_changes_better_crop_decision(self):
        import numpy as np
        fixture, live, p, e, first = self.make()
        live.flush_offers(); p.step(); e.step(); p.step()  # Old registration request is now queued, not executed.
        fixture.receipt = replace(fixture.receipt, frame_index=1, utc=fixture.receipt.utc + timedelta(seconds=1),
                                  monotonic=fixture.receipt.monotonic + 1)
        fixture.image = np.full((30, 100, 3), 70, dtype=np.uint8)
        second = live.add(fixture.bound(), tunis_mode="legacy_whole", generation=2)
        self.assertEqual(first.parent, second.parent)
        self.assertNotEqual(first.frame_id, second.frame_id)
        live.runtime.text = "0009"
        live.flush_offers()
        p.next_source = p.sources.index(STREAMS["offer"])
        p.step()
        self.finish(live, p, e)
        body = self.result(live)
        self.assertEqual((body["crop"]["generation"], body["decision"]["normalized_registration"]), (2, "0009"))
        results = live.captured(first.session_id)
        old = [r for r in results if r.request.role == "registration" and r.request.crop.generation == 1]
        self.assertEqual(len(old), 1)
        self.assertEqual((old[0].outcome, old[0].error_code), ("missing", "generation_superseded_before_start"))
        self.assertEqual(dict(live.runtime.calls), {"category_ocr": 2, "registration": 1})
        state = p.journal.read_state(first.session_id)
        _, proposal, _, _ = replay(live.codec, state["log"])
        self.assertEqual(canonical(body["decision"]), canonical(asdict(proposal.decision)))

    def test_conflicting_and_stale_late_messages_cannot_change_sealed_state(self):
        _, live, p, e, c = self.make()
        self.finish(live, p, e)
        before = canonical(p.journal.read_state(c.session_id))
        original = live.codec.read(self.rows(live, "executions")[-1][1][b"json"])
        changed = json.loads(json.dumps(original["body"]))
        changed["items"][0]["raw_text"] = "9999"
        self.inject_duplicate(live, "executions", live.codec.make("executions", changed))
        self.inject_duplicate(live, "offer")
        for _ in range(15):
            p.step(); e.step()
        self.assertEqual(canonical(p.journal.read_state(c.session_id)), before)
        self.assertEqual(len(self.rows(live, "plate_result")), 1)
        self.assertEqual(sum(live.runtime.calls.values()), 2)
        self.assertEqual(len(self.rows(live, "diagnostic")), 1)

    def test_conflicting_execution_before_seal_blocks_review_publication(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step(); e.step()  # First category execution has not reached Plate yet.
        original = live.codec.read(self.rows(live, "executions")[0][1][b"json"])
        changed = json.loads(json.dumps(original["body"]))
        changed["items"][0]["raw_text"] = "different synthetic evidence"
        self.inject_duplicate(live, "executions", live.codec.make("executions", changed))
        for _ in range(25):
            p.step(); e.step()
        self.assertEqual(p.journal.read_state(c.session_id)["status"], "blocked")
        self.assertEqual(self.rows(live, "plate_result"), [])
        self.assertFalse(live.slots[c.session_id].adapters[1].bound.resource.released)

    def test_wrong_partition_and_crop_do_not_mutate_valid_state_or_execute(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step()
        before = canonical(p.journal.read_state(c.session_id))
        offer = live.codec.read(self.rows(live, "offer")[0][1][b"json"])
        invalid = json.loads(json.dumps(offer))
        invalid["scope"]["org_id"] = "87654321-4321-4321-8321-210987654321"
        self.inject_duplicate(live, "offer", canonical(invalid))
        for _ in range(5):
            p.step()
        self.assertEqual(canonical(p.journal.read_state(c.session_id)), before)
        request = live.codec.read(self.rows(live, "requests")[0][1][b"json"])
        request["body"]["items"][0]["crop"]["crop_id"] = "wrong-crop"
        self.inject_duplicate(live, "requests", canonical(request))
        e.step()  # The genuine first request may execute once.
        calls = dict(live.runtime.calls)
        for _ in range(5):
            e.step()
        self.assertEqual(live.runtime.calls, calls)

    def test_valid_hash_wrong_execution_binding_cannot_poison_plate_state(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step(); e.step()
        before = canonical(p.journal.read_state(c.session_id))
        original = local("result", live.codec.read(self.rows(live, "executions")[0][1][b"json"])["body"]["items"][0])
        r = original.request
        variants = (
            (replace(c, crop_id="wrong-crop"), ()),
            (replace(c, vehicle_lifetime_id="wrong-vehicle"), ()),
            (replace(c, crop_id="wrong-generation", generation=2), ()),
            (c, (Dependency("0" * 64, "1" * 64),)),
        )
        for changed_crop, deps in variants:
            forged = request(changed_crop, r.role, sequence=r.sequence, tunis_mode=r.tunis_mode,
                             thresholds=r.thresholds, dependencies=deps)
            result = replace(original, request=forged)
            raw = live.codec.make("executions", dict(batch_id=hashed([forged.operation_id]), items=[asdict(result)]))
            entry = self.inject_duplicate(live, "executions", raw)
            stream = live.journal.key(STREAMS["executions"])
            delivered = self.client.xreadgroup(p.journal.group, p.journal.consumer, {stream: ">"}, count=10)
            fields = next(fields for _, rows in delivered for sid, fields in rows if sid == entry)
            p.process(STREAMS["executions"], entry, fields)
            self.assertEqual(canonical(p.journal.read_state(c.session_id)), before)
        self.finish(live, p, e)
        self.parity(live, c)

    def test_valid_hash_wrong_crop_vehicle_and_dependency_rejected_before_native_intent(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step(); e.step(); p.step()
        legitimate = local("request", live.codec.read(self.rows(live, "requests")[-1][1][b"json"])["body"]["items"][0])
        variants = (
            (replace(c, crop_id="different-crop"), legitimate.dependencies),
            (replace(c, vehicle_lifetime_id="different-vehicle"), legitimate.dependencies),
            (c, (Dependency(legitimate.dependencies[0].operation_id, "0" * 64),)),
        )
        before = live.snapshot(live.slots[c.session_id])
        for changed_crop, deps in variants:
            forged = request(changed_crop, legitimate.role, sequence=legitimate.sequence,
                             tunis_mode=legitimate.tunis_mode, thresholds=legitimate.thresholds,
                             dependencies=deps)
            raw = live.codec.make("requests", dict(batch_id=hashed([forged.operation_id]), items=[asdict(forged)]))
            entry = self.inject_duplicate(live, "requests", raw)
            stream = live.journal.key(STREAMS["requests"])
            delivered = self.client.xreadgroup(e.journal.group, e.journal.consumer, {stream: ">"}, count=10)
            fields = next(fields for _, rows in delivered for sid, fields in rows if sid == entry)
            e.process(STREAMS["requests"], entry, fields)
            self.assertEqual(live.snapshot(live.slots[c.session_id]), before)
            self.assertEqual(self.client.hget(e.journal.state, "native_intents"), b"1")
            self.assertEqual(dict(live.runtime.calls), {"category_ocr": 1})
        self.assertEqual(len(self.rows(live, "engine_diagnostic")), 3)
        self.finish(live, p, e)
        self.parity(live, c)

    def test_same_live_engine_reconnect_around_native_call_never_reexecutes(self):
        import redis
        for phase in ("before_native", "after_native"):
            _, live, p, e, c = self.make()
            live.flush_offers(); p.step()
            def fail(at):
                if at == phase:
                    raise Crash(phase)
            live.hook = fail
            with self.assertRaises(Crash):
                e.step()
            calls = sum(live.runtime.calls.values())
            self.assertEqual(calls, 0 if phase == "before_native" else 1)
            self.assertEqual(pending(self.client, e), 1)
            e.journal.release()
            live.hook = lambda phase: None
            replacement_client = redis.Redis(unix_socket_path=str(self.owned.socket), socket_timeout=2)
            self.addCleanup(replacement_client.close)
            self.client.connection_pool.disconnect()  # Same Engine/crops/tickets, new transport connections.
            replacement = EnginePeer(replacement_client, live=live, namespace=e.journal.prefix.split(":")[0], consumer="reconnected")
            replacement.setup(); self.workers.append(replacement)
            self.finish(live, p, replacement)
            self.parity(live, c)
            self.assertEqual(dict(live.runtime.calls), {"category_ocr": 1, "registration": 1})

    def test_may_have_started_without_result_or_active_proof_is_not_retried(self):
        _, live, p, e, c = self.make()
        live.flush_offers(); p.step()
        live.hook = lambda phase: (_ for _ in ()).throw(Crash()) if phase == "native_entered" else None
        with self.assertRaises(Crash):
            e.step()
        e.journal.release()
        live.hook = lambda phase: None
        replacement = EnginePeer(self.client, live=live, namespace=e.journal.prefix.split(":")[0], consumer="unknown-replacement")
        replacement.setup(); self.workers.append(replacement)
        for _ in range(8):
            replacement.step(); p.step()
        self.assertEqual(live.runtime.calls, {})
        self.assertEqual(pending(self.client, replacement), 1)
        self.assertEqual(self.rows(live, "plate_result"), [])
        self.assertFalse(live.slots[c.session_id].adapters[1].bound.resource.released)
        self.assertEqual(self.client.hget(e.journal.state, "native_intents"), b"1")

    def test_unretired_native_return_holds_work_pel_and_crop_until_actual_ack(self):
        _, live, p, e, c = self.make()
        live.runtime.defer = True
        live.flush_offers(); p.step(); e.step()
        self.assertEqual(live.pending_native, 1)
        self.assertEqual(pending(self.client, e), 1)
        self.assertEqual(self.rows(live, "executions"), [])
        for _ in range(5):
            e.step()
        self.assertEqual(sum(live.runtime.calls.values()), 1)
        self.assertFalse(live.slots[c.session_id].adapters[1].bound.resource.released)
        live.runtime.defer = False
        live.runtime.ctx.retired()
        self.finish(live, p, e)
        self.parity(live, c)

    def test_premature_seal_cannot_settle_unretired_execution_or_release_pixels(self):
        _, live, p, e, c = self.make()
        live.runtime.defer = True
        live.flush_offers(); p.step(); e.step()
        slot = live.slots[c.session_id]
        operation = next(iter(slot.engine._requests))
        raw = live.codec.make("seal", dict(crop=asdict(c), operation_results=[[operation, "0" * 64]],
                                           decision_sha256="0" * 64))
        self.client.xadd(live.journal.key(STREAMS["seal"]), {"json": raw}, id="1-0")
        for _ in range(4):
            e.step(); p.step()
        self.assertIsNone(slot.seal)
        self.assertEqual(self.rows(live, "settled"), [])
        self.assertEqual(self.rows(live, "plate_result"), [])
        self.assertEqual(live.pending_native, 1)
        self.assertFalse(slot.adapters[1].bound.resource.released)
        live.runtime.defer = False
        live.runtime.ctx.retired()
        self.finish(live, p, e)
        self.parity(live, c)

    def test_lost_publish_and_ack_replies_repair_exact_outputs_without_native_retry(self):
        from vehicle.common.redis_scalar.journal import PUBLISH, FINISH
        from redis.exceptions import ConnectionError
        for role, script, after in (("engine", PUBLISH, True), ("plate", PUBLISH, True),
                                    ("engine", FINISH, False), ("engine", FINISH, True),
                                    ("plate", FINISH, False), ("plate", FINISH, True)):
            _, live, p, e, c = self.make()
            target = e if role == "engine" else p
            actual = self.client
            class LostReply:
                lost = False
                def __getattr__(self, name):
                    return getattr(actual, name)
                def eval(proxy, statement, *args):
                    if statement == script and not proxy.lost:
                        proxy.lost = True
                        if after:
                            actual.eval(statement, *args)
                        raise ConnectionError("injected ambiguous Redis reply")
                    return actual.eval(statement, *args)
            proxy = LostReply()
            target.client = target.journal.client = proxy
            live.flush_offers()
            errors = 0
            for _ in range(150):
                for worker in (p, e):
                    try:
                        worker.step()
                    except ConnectionError:
                        errors += 1
                if all(s.completion for s in live.slots.values()) and not pending(self.client, p) and not pending(self.client, e):
                    break
            self.assertEqual(errors, 1)
            self.parity(live, c)
            self.assertEqual(dict(live.runtime.calls), {"category_ocr": 1, "registration": 1})
            self.assertTrue(live.slots[c.session_id].adapters[1].bound.resource.released)
            self.assertEqual((pending(self.client, p), pending(self.client, e)), (0, 0))

    def test_fenced_old_owner_cannot_publish_or_ack_after_replacement(self):
        from redis.exceptions import ResponseError
        _, live, p, e, c = self.make(worker_options=dict(lease_ms=200))
        def crash(phase):
            if phase == "prepared":
                raise Crash()
        p.journal.hook = crash
        live.flush_offers()
        with self.assertRaises(Crash):
            p.step()
        old_token = p.journal.token
        intent_raw = self.client.hget(p.journal.state, "pending")
        intent = json.loads(intent_raw)
        deadline = time.monotonic() + 3
        while self.client.get(p.journal.lease) is not None and time.monotonic() < deadline:
            time.sleep(.01)
        replacement = PlateWorker(self.client, codec=live.codec, namespace=p.journal.prefix.split(":")[0], consumer="fenced-replacement")
        replacement.setup(); self.workers.append(replacement)
        replacement.step()
        self.assertNotEqual(replacement.journal.token, old_token)
        from vehicle.common.redis_scalar.journal import PUBLISH, FINISH
        output = intent["outputs"][0]
        with self.assertRaises(ResponseError):
            p.journal.call(PUBLISH, [p.journal.lease, p.journal.state, p.journal.key(output["stream"])],
                           old_token, intent_raw, output["id"], output["data"], p.journal.max_stream)
        with self.assertRaises(ResponseError):
            p.journal.call(FINISH, [p.journal.lease, p.journal.state, p.journal.key(intent["source"]),
                                    p.journal.key(output["stream"])], old_token, intent_raw, p.journal.group, intent["entry_id"])
        self.finish(live, replacement, e)
        self.parity(live, c)

    def test_actual_plate_worker_kill_around_publication_ack_and_result(self):
        matrix = [(phase, "offer") for phase in ("before_prepare", "prepared", "published", "before_ack", "acknowledged")]
        matrix += [("published", "executions"), ("prepared", "settled"), ("before_ack", "settled")]
        for phase, kind in matrix:
            with self.subTest(phase=phase, kind=kind):
                _, live, p, e, c = self.make()
                tag = uuid4().hex
                scope_path = self.owned.path / (tag + ".scope.json")
                phase_path = self.owned.path / (tag + ".phase")
                scope_path.write_text(json.dumps(asdict(live.scope)))
                log = (self.owned.path / (tag + ".worker.log")).open("w")
                self.addCleanup(log.close)
                child = subprocess.Popen([sys.executable, "-m", "vehicle.common.mohamed_lpr_streams",
                    "--redis-socket", str(self.owned.socket), "--scope-file", str(scope_path),
                    "--namespace", p.journal.prefix.split(":")[0], "--consumer", "crash-worker",
                    "--lease-ms", "250", "--pause-at", phase, "--pause-kind", kind, "--phase-file", str(phase_path)],
                    stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                self.children.append(child)
                live.flush_offers()
                deadline = time.monotonic() + 12
                while not phase_path.exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        self.fail("Plate worker exited before requested failure boundary")
                    e.step()
                    time.sleep(.005)
                self.assertTrue(phase_path.exists(), "worker did not reach failure boundary")
                before = {k: self.rows(live, k) for k in ("requests", "plate_result")}
                child.kill(); child.wait(timeout=5)
                while self.client.get(p.journal.lease) is not None and time.monotonic() < deadline:
                    time.sleep(.01)
                replacement = PlateWorker(self.client, codec=live.codec, namespace=p.journal.prefix.split(":")[0], consumer="replacement")
                replacement.setup(); self.workers.append(replacement)
                self.finish(live, replacement, e)
                self.parity(live, c)
                for k, rows in before.items():
                    self.assertEqual(self.rows(live, k)[:len(rows)], rows)
                self.assertEqual(dict(live.runtime.calls), {"category_ocr": 1, "registration": 1})


if __name__ == "__main__":
    unittest.main()
