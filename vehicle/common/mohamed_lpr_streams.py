"""Fenced, isolated v3 Streams exchange. Engine memory is not a recovery store.

Only PlateWorker can plan or publish review decisions. EnginePeer never rebuilds
pixels/models from Redis. A new LiveEngine requires a fresh namespace; reconnect
and peer replacement explicitly reuse the same live Engine object and tickets.
"""
from dataclasses import asdict, dataclass, field
import copy
from hashlib import sha256
import re
import json
import time
from uuid import UUID

from .mohamed_lpr_contract import Result, digest, require
from .mohamed_lpr_engine import BindingError, RetirementPending, V3EngineAdapter
from .mohamed_lpr_harness import Harness
from .mohamed_plate_policy import Thresholds
from .mohamed_lpr_stream_codec import Codec, Scope, STREAMS, canonical, crop, hashed, local, public_bound, scalar_json
from .mohamed_lpr_stream_policy import Waiting, reduce_event
from .plate_requests.ownership import CLAIM_ENGINE
from .redis_scalar.journal import Journal, GUARD, PRODUCE
from .redis_scalar.worker import Worker


BEGIN = GUARD + '''
if redis.call('GET',KEYS[3])~=ARGV[4] then return redis.error_reply('ENGINE_OWNER_LOST') end
local old=redis.call('HGET',KEYS[2],ARGV[2])
if old then
 if old~=ARGV[3] then return redis.error_reply('NATIVE_INTENT_CONFLICT') end
 return 'retained'
end
local count=tonumber(redis.call('HGET',KEYS[2],'native_intents') or '0')
if count>=256 then return redis.error_reply('CAPACITY_EXCEEDED') end
redis.call('HSET',KEYS[2],ARGV[2],ARGV[3],'native_intents',count+1)
return 'prepared'
'''
OFFER = "if redis.call('GET',KEYS[2])~=ARGV[4] then return redis.error_reply('ENGINE_OWNER_LOST') end\n" + PRODUCE


class V3Journal(Journal):
    """Reuse Lua state/intent/fixed-XADD/XACK fencing without editing v2."""
    def __init__(self, client, *, namespace, scope, role, consumer, **options):
        require(role in {"plate-v3", "engine-v3"}, "invalid_v3_role")
        options.setdefault("lease_ms", 60000)
        options.setdefault("max_events", 512)
        options.setdefault("max_stream", 512)
        options.setdefault("max_state_bytes", 1024 * 1024)
        super().__init__(client, namespace=namespace, trusted_org=scope.org_id, role="plate_requests", consumer=consumer, **options)
        self.prefix = f"{namespace}:{{mohamed-lpr-v3.{UUID(scope.org_id)}}}:"
        self.role = role
        self.group = "mohamed-" + role
        self.lease = self.prefix + role + ":lease"
        self.fence = self.prefix + role + ":fence"
        self.state = self.prefix + role + ":journal"
        self.max_output_bytes = 262144

    def key(self, suffix):
        require(suffix in STREAMS.values(), "not_a_v3_stream")
        return self.prefix + suffix

    def require_org(self, org):
        require(self.prefix.endswith(f":{{mohamed-lpr-v3.{UUID(str(org))}}}:"), "wrong_v3_partition")

    def handoff(self, **kwargs):
        if kwargs.get("state") is not None:
            # Preserve v3's original float/int identity inside the unchanged P1
            # journal serializer; no numeric normalization may rewrite an op ID.
            kwargs["state"] = {"v3_json": canonical(kwargs["state"]).decode()}
        return super().handoff(**kwargs)

    def read_state(self, key):
        raw = self.client.hget(self.state, "vehicle:" + key)
        if raw is None:
            return None
        require(len(raw) <= self.max_state_bytes, "v3_state_budget")
        wrapper = json.loads(raw)
        require(type(wrapper) is dict and set(wrapper) == {"v3_json"} and type(wrapper["v3_json"]) is str,
                "wrong_v3_state_profile")
        return scalar_json(wrapper["v3_json"].encode(), max_bytes=self.max_state_bytes,
                           max_depth=40, max_nodes=500000)


@dataclass
class Slot:
    harness: object
    mode: str
    thresholds: Thresholds
    adapters: dict = field(default_factory=dict)
    tickets: dict = field(default_factory=dict)
    seal: dict | None = None
    completion: dict | None = None
    blocked: str | None = None

    @property
    def engine(self):
        return self.harness.engine


class LiveEngine:
    def __init__(self, client, *, namespace, scope, runtime, hook=None):
        self.client, self.scope, self.runtime = client, scope, runtime
        self.codec = Codec(scope)
        self.journal = V3Journal(client, namespace=namespace, scope=scope, role="engine-v3", consumer="live-owner")
        self.owner_key = self.journal.prefix + "engine-live-owner"
        self.owner_bytes = canonical(dict(scope=asdict(scope), recovery="same-live-engine-only"))
        result = client.eval(CLAIM_ENGINE, 1, self.owner_key, self.journal.prefix, self.owner_bytes)
        require(result and result[0] == 1, "engine_crops_unrecoverable_requires_fresh_namespace")
        self.slots, self.offers = {}, []
        self.confirmed = set()
        self.hook = hook or (lambda phase: None)

    def check_owner(self):
        require(self.client.get(self.owner_key) == self.owner_bytes, "live_engine_owner_lost")

    def add(self, bound, *, tunis_mode, thresholds=Thresholds(), generation=None):
        bound = public_bound(bound, generation=generation)
        c = bound.scalar
        require((c.org_id, c.run_id, c.camera_id) ==
                (self.scope.org_id, self.scope.run_id, self.scope.camera_id), "engine_partition_mismatch")
        key = c.session_id
        require(sum(len(s.adapters) for s in self.slots.values()) < 16, "live_crop_handle_budget")
        if key not in self.slots:
            require(len(self.slots) < 8, "live_crop_session_budget")
            slot = Slot(Harness(c, tunis_mode=tunis_mode, thresholds=thresholds), tunis_mode, thresholds)
        else:
            slot = self.slots[key]
            require((slot.mode, slot.thresholds) == (tunis_mode, thresholds) and slot.seal is None,
                    "live_generation_closed")
            slot.harness.recheck(c)
        adapter = V3EngineAdapter(self.runtime, bound, slot.harness)
        slot.adapters[c.generation] = adapter
        self.slots[key] = slot
        body = dict(crop=asdict(c), tunis_mode=tunis_mode, thresholds=asdict(thresholds))
        self.offers.append(self.codec.make("offer", body))
        return c

    def flush_offers(self):
        for ordinal, raw in enumerate(self.offers, 1):
            if ordinal in self.confirmed:
                continue
            self.client.eval(OFFER, 2, self.journal.key(STREAMS["offer"]), self.owner_key,
                             f"{ordinal}-0", raw, self.journal.max_stream, self.owner_bytes)
            self.confirmed.add(ordinal)

    def validate_requests(self, body):
        requests = tuple(local("request", v) for v in body["items"])
        c = requests[0].crop
        require(c.session_id in self.slots, "unknown_live_crop")
        slot = self.slots[c.session_id]
        require(slot.blocked is None, "engine_session_blocked")
        require(c.generation in slot.adapters and c == slot.adapters[c.generation].bound.scalar, "wrong_live_crop")
        require(all((r.tunis_mode, r.thresholds) == (slot.mode, slot.thresholds) for r in requests), "wrong_live_policy")
        probe = copy.copy(slot.engine)
        probe._requests, probe._results = dict(slot.engine._requests), dict(slot.engine._results)
        if c != slot.engine.crop:
            require(slot.seal is None, "closed_stale_admission")
            # A previously offered crop can have authorized work still in the
            # request stream when a recheck overtakes it. Validate its original
            # generation topology, then retire it as known-not-started below.
            probe.crop = c
        for r in requests:
            probe.admit(r)  # Validate entire batch before mutating live admission.
        return slot, requests

    def execute_once(self, slot, request):
        op = request.operation_id
        if op in slot.engine._results:
            return slot.engine._results[op]
        adapter = slot.adapters[request.crop.generation]
        if op in adapter.results:
            result = adapter.results[op]
        elif adapter.active is not None:
            require(adapter.active["request"] == request, "different_active_operation")
            result = adapter.poll_retirement()
            if result is None:
                raise Waiting("native_not_retired")
        elif slot.tickets.get(op) == "entered":
            # There is no result or active invocation proving native outcome.
            # Even a failure between this marker and the call cannot be guessed.
            raise Waiting("execution_may_have_started")
        elif request.crop != slot.engine.crop:
            result = Result(request, "skipped-" + op, "v3-live-engine", "missing", True,
                            error_code="generation_superseded_before_start")
        else:
            if self.pending_native:
                raise Waiting("another_native_operation_not_retired")
            self.hook("before_native")
            slot.tickets[op] = "entered"
            self.hook("native_entered")
            try:
                result = adapter.execute(request)
            except RetirementPending:
                raise Waiting("native_not_retired") from None
            self.hook("after_native")
        slot.engine.receive(result)
        slot.tickets[op] = "retired"
        return result

    def captured(self, session):
        slot = self.slots[session]
        return tuple(slot.engine._results.values())

    def snapshot(self, slot):
        return dict(version="mohamed-engine-state-v3", crop=asdict(slot.engine.crop),
                    status="completed" if slot.completion else "sealed" if slot.seal else "open",
                    request_ids=list(slot.engine._requests),
                    result_hashes={op: digest(r) for op, r in slot.engine._results.items()},
                    seal=slot.seal, completion=slot.completion)

    def reconcile_cleanup(self, journal):
        for session, slot in self.slots.items():
            state = journal.read_state(session)
            if state and state["status"] == "completed":
                require(slot.completion == state["completion"], "live_completion_mismatch")
                for adapter in slot.adapters.values():
                    adapter.release(discard=True)  # Review handshake confirmed; no DB1 delivery claim.

    @property
    def pending_native(self):
        return sum(a.active is not None for s in self.slots.values() for a in s.adapters.values())


class StreamWorker(Worker):
    def __init__(self, client, *, codec, namespace, role, consumer, **options):
        self.codec, self.client, self.role = codec, client, role
        self.journal = V3Journal(client, namespace=namespace, scope=codec.scope, role=role, consumer=consumer, **options)
        kinds = ("offer", "executions", "settled") if role == "plate-v3" else ("requests", "seal", "plate_result")
        self.sources = [STREAMS[k] for k in kinds]
        self.cursor = {s: "0-0" for s in self.sources}
        self.prefer_pending = {s: True for s in self.sources}
        self.next_source = 0
        self.current_kind = None

    def step(self):
        try:
            changed = super().step()
        except Exception as error:
            if "STALE_OWNER" in str(error):
                self.journal.token = None  # New fence must replay retained state/intents.
            raise
        return changed

    def process(self, source, entry_id, fields):
        raw = fields.get(b"json", b"")
        message = None
        try:
            require(set(fields) == {b"json"}, "one_scalar_json_field_required")
            message = self.codec.read(raw)
            self.current_kind = message["kind"]
            require(source == STREAMS[message["kind"]] and source in self.sources, "wrong_v3_stream")
            marker, checksum = "seen:" + message["event_id"], hashed(message)
            seen = self.journal.seen(marker)
            require(seen is None or seen.decode() == checksum, "conflicting_v3_event")
            if seen is not None:
                self.journal.handoff(source=source, entry_id=entry_id, marker=marker, digest=checksum)
                return
            session, state, outputs = self.apply(message)
            self.journal.handoff(source=source, entry_id=entry_id, marker=marker, digest=checksum,
                                 vehicle=session, state=state, outputs=outputs)
        except Waiting:
            return  # Keep PEL entry. Fair polling can receive its prerequisite.
        except (ValueError, BindingError) as error:
            # Bounded diagnostics never retain exceptions/native input strings.
            session = state = None
            if (message is not None and message["kind"] != "diagnostic" and self.role == "plate-v3"
                    and str(error) in {"conflicting_v3_event", "conflicting_result_retry",
                                       "plate_evidence_conflict", "engine_evidence_conflict"}):
                c = self.codec.binding(message["kind"], message["body"])
                old = self.journal.read_state(c.session_id)
                bound = old and any(item["kind"] == "offer" and item["body"]["crop"] == asdict(c)
                                    for item in old["log"])
                if bound and old["status"] not in {"terminal", "blocked"} and c.generation == old["generation"]:
                    session, state = c.session_id, dict(old, status="blocked")
            body = dict(code="conflicting_or_invalid_v3_input", source=source, input_sha256=sha256(raw).hexdigest())
            self.journal.handoff(source=source, entry_id=entry_id, marker="rejected:" + source + ":" + entry_id.decode(),
                                 digest=sha256(raw).hexdigest(), vehicle=session, state=state,
                                 outputs=[(STREAMS["diagnostic" if self.role == "plate-v3" else "engine_diagnostic"], self.codec.make("diagnostic", body))])


class PlateWorker(StreamWorker):
    def __init__(self, client, *, codec, namespace, consumer, **options):
        super().__init__(client, codec=codec, namespace=namespace, consumer=consumer, role="plate-v3", **options)

    def apply(self, message):
        c = self.codec.binding(message["kind"], message["body"])
        state, outputs = reduce_event(self.codec, message, self.journal.read_state(c.session_id))
        return c.session_id, state, outputs


class EnginePeer(StreamWorker):
    def __init__(self, client, *, live, namespace, consumer, **options):
        live.check_owner()
        self.live = live
        super().__init__(client, codec=live.codec, namespace=namespace, consumer=consumer, role="engine-v3", **options)
        require(self.journal.prefix == live.journal.prefix, "engine_peer_namespace_mismatch")

    def step(self):
        self.live.check_owner()
        changed = super().step()
        if self.journal.token:
            self.live.reconcile_cleanup(self.journal)
        return changed

    def apply(self, message):
        kind, body = message["kind"], message["body"]
        c = self.codec.binding(kind, body)
        require(c.session_id in self.live.slots, "unknown_engine_session")
        slot = self.live.slots[c.session_id]
        if kind == "requests":
            slot, requests = self.live.validate_requests(body)
            # Recoverable may-start intent precedes *all* native calls. PEL holds
            # the input; cached live tickets/results supply reconnect proof.
            self.journal.call(BEGIN, [self.journal.lease, self.journal.state, self.live.owner_key],
                              self.journal.token, "native:" + message["event_id"], hashed(message), self.live.owner_bytes)
            for request in requests:
                if request.crop == slot.engine.crop or request.operation_id in slot.engine._requests:
                    slot.engine.admit(request)
                else:
                    # Entire original-generation batch was validated in a probe
                    # before BEGIN. It can only produce a missing/retired result.
                    slot.engine._requests[request.operation_id] = request
                slot.tickets.setdefault(request.operation_id, "scheduled")
            results = [self.live.execute_once(slot, r) for r in requests]
            output = self.codec.make("executions", dict(batch_id=body["batch_id"], items=[asdict(r) for r in results]))
            return c.session_id, self.live.snapshot(slot), [(STREAMS["executions"], output)]
        require(c == slot.engine.crop, "wrong_seal_crop")
        slot.engine.check_settled()
        expected = [[r.operation_id, digest(slot.engine._results[r.operation_id])] for r in slot.engine.current_requests]
        require(body["operation_results"] == expected, "seal_operation_set_changed")
        if kind == "seal":
            require(slot.seal is None or slot.seal == body, "seal_changed")
            slot.engine.close(body)
            slot.seal = body
            outputs = [(STREAMS["settled"], self.codec.make("settled", body))]
        else:
            require(kind == "plate_result" and slot.seal is not None
                    and {k: v for k, v in body.items() if k != "decision"} == slot.seal, "review_completion_changed")
            slot.completion = body
            outputs = []
        return c.session_id, self.live.snapshot(slot), outputs


def pending(client, worker):
    return sum(client.xpending(worker.journal.key(s), worker.journal.group)["pending"] for s in worker.sources)


def pump(plate, engine, *, turns=500):
    """Bounded local driver; separately hosted workers use the same step API."""
    for _ in range(turns):
        plate.step()
        engine.step()
        slots = tuple(engine.live.slots.values())
        if slots and all(s.completion is not None for s in slots) and not pending(engine.client, engine) and not pending(plate.client, plate):
            return
    raise RuntimeError("v3_exchange_did_not_settle")


def main(argv=None):
    import argparse
    import json
    from pathlib import Path
    import redis
    p = argparse.ArgumentParser(description="Explicit isolated v3 Plate worker; never a DB1 producer")
    p.add_argument("--redis-socket", required=True)
    p.add_argument("--scope-file", required=True, type=Path)
    p.add_argument("--namespace", required=True)
    p.add_argument("--consumer", required=True)
    p.add_argument("--lease-ms", type=int, default=60000)
    p.add_argument("--pause-at", choices=("before_prepare", "prepared", "published", "before_ack", "acknowledged"))
    p.add_argument("--pause-kind", choices=("offer", "executions", "settled"))
    p.add_argument("--phase-file", type=Path)
    a = p.parse_args(argv)
    if a.pause_at and a.phase_file is None:
        p.error("pause-at requires phase-file")
    client = redis.Redis(unix_socket_path=a.redis_socket, socket_timeout=2)
    worker = None
    def hook(phase):
        if phase == a.pause_at and (a.pause_kind is None or worker.current_kind == a.pause_kind):
            a.phase_file.write_text(phase)
            while True:
                time.sleep(.025)
    worker = PlateWorker(client, codec=Codec(Scope(**json.loads(a.scope_file.read_text()))), namespace=a.namespace,
                         consumer=a.consumer, lease_ms=a.lease_ms, hook=hook)
    worker.setup()
    while True:
        try:
            if not worker.step():
                time.sleep(.01)
        except redis.RedisError:
            time.sleep(.05)


if __name__ == "__main__":
    main()
