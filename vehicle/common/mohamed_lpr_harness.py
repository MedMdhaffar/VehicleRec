"""In-memory Plate planning / Engine admission. Injected scalar results only.

There is deliberately no callback that could execute a model or emit delivery.
Unknown/missing/failed outcomes are retired local observations, never retries.
"""
from dataclasses import dataclass

from .mohamed_lpr_contract import (
    Crop, Dependency, Request, Result, VERSION, digest, request, require,
)
from .mohamed_plate_policy import Decision, DinoScores, Evidence, Marker, OCR, Thresholds, decide


def _recheck(old, new):
    require(type(new) is Crop and new.parent == old.parent, "recheck_parent_mismatch")
    require(new.generation == old.generation + 1 and new.crop_id != old.crop_id,
            "recheck_generation_mismatch")


def _validate_result(result, requests, results):
    require(type(result) is Result, "typed_result_required")
    op = result.request.operation_id
    require(op in requests, "unsolicited_result")
    require(result.request == requests[op], "result_binding_mismatch")
    for dep in result.request.dependencies:
        require(dep.operation_id in results and digest(results[dep.operation_id]) == dep.result_sha256,
                "result_dependency_mismatch")


class PlateSession:
    """Owns interpretation and follow-ups, without an execution/transport API."""

    def __init__(self, crop, *, tunis_mode, thresholds=Thresholds()):
        require(type(crop) is Crop, "typed_crop_required")
        # Validate mandatory mode/settings even before the first model result.
        decide(Evidence(crop_size=(crop.width, crop.height)), tunis_mode=tunis_mode, thresholds=thresholds)
        self.crop, self.tunis_mode, self.thresholds = crop, tunis_mode, thresholds
        self._requests, self._results = {}, {}
        self._retired_generations = set()
        self._sealed = None
        self._blocked = False

    @property
    def requests(self):
        return tuple(self._requests.values())

    @property
    def decision(self):
        if self._sealed is not None:
            return self._sealed.decision
        evidence = {r.request.role: r.evidence() for r in self._results.values()}
        for r in self.requests:
            if r.role not in evidence:
                cls = DinoScores if r.role == "visual" else Marker if r.role == "marker" else OCR
                evidence[r.role] = cls(r.operation_id, "pending")
        return decide(Evidence(**evidence,
                               crop_size=(self.crop.width, self.crop.height)),
                      tunis_mode=self.tunis_mode, thresholds=self.thresholds)

    def plan(self):
        """Return only newly scheduled operations; replay cannot dispatch twice."""
        require(not self._blocked, "plate_evidence_conflict")
        if self._sealed is not None:
            return ()
        decision = self.decision
        role = next((r for r in ("category_ocr", "visual", "registration", "marker", "left", "right")
                     if decision.reason == r + "_missing"), None)
        # An explicit unavailable execution also says *_missing. It must not loop.
        if role is None or any(r.role == role for r in self._requests.values()):
            return ()
        # Regional sides are siblings: right can arrive before left. Neither
        # depends on the other; both depend on the exact marker and category chain.
        roles = ("left", "right") if role == "left" else (role,)
        used = tuple((r, e) for r, e in decision.evidence_used if r not in {"left", "right"})
        deps = tuple(Dependency(e.evidence_id, digest(self._results[e.evidence_id])) for _, e in used)
        made = []
        for next_role in roles:
            region = None
            if next_role in {"left", "right"}:
                marker = next(r for r in self._results.values() if r.request.role == "marker")
                region = ((0, 0, marker.bbox[0], self.crop.height) if next_role == "left" else
                          (marker.bbox[2], 0, self.crop.width, self.crop.height))
            r = request(self.crop, next_role, sequence=len(self._requests) + 1,
                        tunis_mode=self.tunis_mode, thresholds=self.thresholds,
                        dependencies=deps, region_xyxy=region)
            self._requests[r.operation_id] = r
            made.append(r)
        return tuple(made)

    def receive(self, result):
        require(type(result) is Result, "typed_result_required")
        if self._sealed is not None:
            return "sealed"  # Frozen local decision; no new work or mutation.
        if result.request.crop.generation in self._retired_generations:
            require(result.request.crop.parent == self.crop.parent, "stale_parent_mismatch")
            return "stale"
        _validate_result(result, self._requests, self._results)
        old = self._results.get(result.request.operation_id)
        if old is not None:
            if digest(old) != digest(result):
                self._blocked = True
                raise ValueError("conflicting_result_retry")
            return "replay"
        require(not self._blocked, "plate_evidence_conflict")
        self._results[result.request.operation_id] = result
        return "recorded"

    def recheck(self, crop):
        require(self._sealed is None and not self._blocked, "plate_generation_closed")
        _recheck(self.crop, crop)
        self._retired_generations.add(self.crop.generation)
        self.crop = crop
        self._requests, self._results = {}, {}

    def seal(self, engine):
        if self._sealed is not None:
            return self._sealed
        require(not self._blocked, "plate_evidence_conflict")
        require(len(self._requests) == len(self._results) and bool(self._requests), "premature_seal")
        # A missing follow-up must be requested, not sealed as missing evidence.
        probe = PlateSession(self.crop, tunis_mode=self.tunis_mode, thresholds=self.thresholds)
        probe._requests, probe._results = dict(self._requests), dict(self._results)
        require(not probe.plan(), "premature_seal")
        require(engine.crop == self.crop and engine.current_requests == self.requests, "seal_ledger_mismatch")
        require(tuple(digest(r) for r in engine.current_results) ==
                tuple(digest(self._results[r.operation_id]) for r in self.requests),
                "seal_evidence_mismatch")
        engine.check_settled()
        seal = LocalSeal(VERSION, self.crop,
                         tuple((r.operation_id, digest(self._results[r.operation_id])) for r in self.requests),
                         self.decision)
        engine.close(seal)
        self._sealed = seal
        return seal


class EngineLedger:
    """Admission and exact evidence, never category/number interpretation.

    Receiving a Result asserts the injected execution has fully retired. A future
    native adapter must wait for actual resource retirement before making it.
    """

    def __init__(self, crop):
        require(type(crop) is Crop, "typed_crop_required")
        self.crop = crop
        self._requests, self._results = {}, {}
        self._blocked = False
        self._seal = None

    @property
    def scheduled(self):
        return tuple(self._requests.values())

    @property
    def current_requests(self):
        return tuple(r for r in self.scheduled if r.crop == self.crop)

    @property
    def current_results(self):
        return tuple(self._results[r.operation_id] for r in self.current_requests if r.operation_id in self._results)

    def admit(self, r):
        require(type(r) is Request, "typed_request_required")
        if r.operation_id in self._requests:
            require(self._requests[r.operation_id] == r, "conflicting_request_retry")
            return "replay"
        require(self._seal is None and not self._blocked, "engine_generation_closed")
        require(r.crop == self.crop, "request_crop_mismatch")
        current = self.current_requests
        require(r.sequence == len(current) + 1 and r.role not in {p.role for p in current}, "request_order_mismatch")
        if current:
            require((r.tunis_mode, r.thresholds) == (current[0].tunis_mode, current[0].thresholds), "policy_binding_mismatch")
        roles = set()
        for dep in r.dependencies:
            original = self._requests.get(dep.operation_id)
            result = self._results.get(dep.operation_id)
            require(original is not None and original.crop == r.crop and result is not None
                    and digest(result) == dep.result_sha256, "request_dependency_mismatch")
            require(result.outcome == "succeeded" or (original.role == "category_ocr" and result.outcome == "failed"),
                    "dependency_not_usable")
            roles.add(original.role)
        expected = {"category_ocr"}
        if r.role == "category_ocr":
            expected = set()
        else:
            if any(p.role == "visual" for p in current) and r.role != "visual":
                expected.add("visual")
            if r.role in {"left", "right"}:
                expected.add("marker")
            if r.tunis_mode == "legacy_whole" and r.role in {"marker", "left", "right"}:
                expected.add("registration")
        require(roles == expected, "request_dependency_roles_mismatch")
        region = (0, 0, self.crop.width, self.crop.height)
        if r.role in {"left", "right"}:
            marker = next(self._results[d.operation_id] for d in r.dependencies
                          if self._requests[d.operation_id].role == "marker")
            require(marker.bbox is not None, "region_marker_missing")
            region = ((0, 0, marker.bbox[0], self.crop.height) if r.role == "left" else
                      (marker.bbox[2], 0, self.crop.width, self.crop.height))
        require(r.region_xyxy == region, "request_region_mismatch")
        self._requests[r.operation_id] = r
        return "admitted"

    def receive(self, result):
        require(type(result) is Result, "typed_result_required")
        if self._seal is not None:
            return "sealed"
        _validate_result(result, self._requests, self._results)
        op = result.request.operation_id
        old = self._results.get(op)
        if old is not None:
            if digest(old) != digest(result):
                self._blocked = True
                raise ValueError("conflicting_result_retry")
            return "replay"
        require(not self._blocked, "engine_evidence_conflict")
        self._results[op] = result
        return "recorded" if result.request.crop == self.crop else "stale_retired"

    def recheck(self, crop):
        require(self._seal is None and not self._blocked, "engine_generation_closed")
        _recheck(self.crop, crop)
        require(crop.generation <= 64, "generation_budget_exceeded")
        self.crop = crop  # Old in-flight work remains in the retirement ledger.

    def check_settled(self):
        require(not self._blocked, "engine_evidence_conflict")
        require(set(self._requests) == set(self._results), "executions_not_retired")

    def close(self, seal):
        self.check_settled()
        require(self._seal is None or self._seal == seal, "conflicting_seal")
        self._seal = seal


@dataclass(frozen=True)
class LocalSeal:
    version: str
    crop: Crop
    request_results: tuple[tuple[str, str], ...]
    decision: Decision


class Harness:
    """Convenience test driver. Only explicit scalar injection advances execution."""

    def __init__(self, crop, *, tunis_mode, thresholds=Thresholds()):
        self.plate = PlateSession(crop, tunis_mode=tunis_mode, thresholds=thresholds)
        self.engine = EngineLedger(crop)

    def advance(self):
        require(not self.engine._blocked, "engine_evidence_conflict")
        work = self.plate.plan()
        for r in work:
            self.engine.admit(r)
        return work

    def inject(self, result):
        status = self.engine.receive(result)
        self.plate.receive(result)
        return status

    def recheck(self, crop):
        # Validate both sides before mutating either one.
        require(self.plate._sealed is None and not self.plate._blocked and not self.engine._blocked,
                "generation_closed")
        _recheck(self.plate.crop, crop)
        require(crop.generation <= 64, "generation_budget_exceeded")
        self.engine.recheck(crop)
        self.plate.recheck(crop)

    def seal(self):
        return self.plate.seal(self.engine)
