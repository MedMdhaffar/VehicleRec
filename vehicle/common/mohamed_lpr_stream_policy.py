"""Replayable Plate state. The unchanged Phase 1/2 policy owns all decisions."""
from dataclasses import asdict

from .mohamed_lpr_contract import require
from .mohamed_lpr_harness import EngineLedger, Harness, PlateSession
from .mohamed_plate_policy import Thresholds
from .mohamed_lpr_stream_codec import STREAMS, crop, hashed, local, seal_body


class Waiting(Exception):
    """Keep input in PEL; a different source can supply the prerequisite."""


def replay(codec, log):
    plate = engine = proposal = terminal = None
    batches = {}
    output = []
    for message in log:
        output = []
        kind, body = message["kind"], message["body"]
        if kind == "offer":
            offered = crop(body["crop"])
            if plate is None:
                plate = PlateSession(offered, tunis_mode=body["tunis_mode"], thresholds=Thresholds(**body["thresholds"]))
                engine = EngineLedger(offered)
            else:
                require(proposal is None, "plate_already_sealed")
                require((body["tunis_mode"], body["thresholds"]) ==
                        (plate.tunis_mode, asdict(plate.thresholds)), "offer_policy_changed")
                if offered == plate.crop:
                    continue
                plate.recheck(offered)
                engine.recheck(offered)
        elif plate is None:
            raise Waiting("offer_not_received")
        elif kind == "executions":
            require(body["batch_id"] in batches, "unrequested_execution_batch")
            expected = batches[body["batch_id"]]
            results = tuple(local("result", item) for item in body["items"])
            require({r.request.operation_id for r in results} == {r.operation_id for r in expected}, "execution_batch_changed")
            for result in results:
                require(result.request in expected, "execution_request_changed")
                engine.receive(result)
                plate.receive(result)
        elif kind == "settled":
            require(proposal is not None and body == seal_body(proposal), "settlement_changed")
            terminal = dict(**body, decision=asdict(proposal.decision))
            output = [(STREAMS["plate_result"], codec.make("plate_result", terminal))]
            continue
        else:
            raise ValueError("unexpected_plate_input")
        if proposal is not None:
            continue
        work = plate.plan()
        if work:
            for request in work:
                engine.admit(request)
            batch_id = hashed(sorted(r.operation_id for r in work))
            batches[batch_id] = work
            output = [(STREAMS["requests"], codec.make("requests", dict(batch_id=batch_id, items=[asdict(r) for r in work])))]
        elif plate.requests and len(plate.requests) == len(engine.current_results):
            try:
                proposal = plate.seal(engine)
            except ValueError as error:
                if str(error) not in {"executions_not_retired", "premature_seal"}:
                    raise
            else:
                output = [(STREAMS["seal"], codec.make("seal", seal_body(proposal)))]
    return plate, proposal, terminal, output


def reduce_event(codec, message, previous):
    require(message["kind"] in {"offer", "executions", "settled"}, "wrong_plate_stream")
    if previous is not None:
        require(previous["version"] == "mohamed-plate-state-v3", "wrong_plate_state_version")
        incoming = codec.binding(message["kind"], message["body"])
        require(list(incoming.parent) == previous["parent"], "plate_parent_changed")
        if previous["status"] in {"terminal", "blocked"}:
            require(any(old["event_id"] == message["event_id"] and old["body_sha256"] == message["body_sha256"]
                        for old in previous["log"]), "closed_generation_late_input")
            return previous, []  # Immutable terminal/blocked generation.
        log = previous["log"]
    else:
        log = []
    require(len(log) < 128, "plate_log_budget")
    updated = log + [message]
    plate, proposal, terminal, output = replay(codec, updated)
    state = dict(version="mohamed-plate-state-v3", status="terminal" if terminal else "sealing" if proposal else "open",
                 log=updated, generation=plate.crop.generation,
                 parent=list(plate.crop.parent), result=terminal)
    return state, output


def memory_replay(crop_record, mode, thresholds, captured):
    """Separate Phase 2 harness replay, never a second model execution."""
    harness = Harness(crop_record, tunis_mode=mode, thresholds=thresholds)
    results = {r.request.operation_id: r for r in captured}
    while True:
        requests = harness.advance()
        if not requests:
            return harness.seal()
        for request in requests:
            require(request.operation_id in results and results[request.operation_id].request == request,
                    "captured_replay_missing_execution")
            harness.inject(results[request.operation_id])
