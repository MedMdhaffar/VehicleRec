"""One connection's opt-in native Engine / Redis v3 review and delivery hook."""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import time
from uuid import uuid4

from ..engine_plate import native_access
from ..mohamed_lpr_engine import BindingError, bind_crop
from ..mohamed_lpr_stream_codec import Scope, STREAMS, canonical
from ..mohamed_lpr_stream_policy import memory_replay
from ..mohamed_lpr_streams import LiveEngine, PlateWorker, EnginePeer, pending
from ..observation_evidence import RecognitionIdentity
from ..vehicle_observation import RunEnd
from .plate_engine import CombinedEngine, VideoPlateLimits


class V3RTSPSession:
    def __init__(self, context, clock, fps, deadline, stop, *, assets, models, owned,
                 tunis_mode, max_frames, max_seconds, max_crops, delivery_hook=None,
                 engine=None, detector=None):
        self.context, self.clock = context, clock
        self.deadline, self.stop = deadline, stop
        self.models, self.owned, self.mode = models, owned, tunis_mode
        self.max_crops, self.delivery_hook = max_crops, delivery_hook
        self.frames, self.live, self.plate, self.peer = {}, None, None, None
        self.resources, self.closed = [], False
        self.start_calls = dict(models.calls)
        self.native = engine is None
        self.vehicle_placement = self.detector_placement = None
        self.record = dict(accepted=0, unresolved=0, incomplete=0, decisions=[], db1_records=0,
                           db1_publications=0, pending=dict(plate=0, engine=0, db1=0), parity=True)
        if engine is None:
            from ..mohamed_lpr_native import CudaPlateDetector
            from .local import LocalDetector, pixel_operations
            detector = detector or CudaPlateDetector(assets, frame_rate=fps)
            detector.frame_rate = fps
            with native_access():
                try:
                    detector.load_backend()
                    vehicle = LocalDetector(assets.path('vehicle'), assets.path('vehicle-tracker'),
                                            device='cuda:0', frame_rate=fps, threads=1)
                except BaseException:
                    detector.close()
                    raise
            estimate, quality = pixel_operations()
            try:
                engine = CombinedEngine(context, clock, vehicle, detector, {},
                    algorithm=RecognitionIdentity('t1-hsv-review', 1, 'classify'), quality_algorithm='t1-colour-quality',
                    plate_quality_algorithm='t1-plate-crop-quality', estimate=estimate, quality_fn=quality,
                    limits=VideoPlateLimits(max_frames=max_frames, max_seconds=max_seconds, max_attempts_total=max_crops))
            except BaseException:
                with native_access():
                    vehicle.close(); detector.close()
                raise
        self.engine = engine
        if self.native:
            self.vehicle_placement = vehicle.placement
            self.detector_placement = detector.placement

    def admit(self, packet):
        if self.closed or packet.receipt is not self.clock.last_valid_receipt:
            raise BindingError('stale_rtsp_frame_receipt')
        self.frames[packet.receipt.frame_index] = (packet.receipt, sha256(packet.pixels.tobytes(order='C')).hexdigest())
        self.engine.process(packet)
        if self.native:
            vehicle = self.engine.colour.detector.target.target
            if vehicle.placement['parameter_devices'] != ['cuda:0']:
                raise BindingError('rtsp_vehicle_cpu_fallback')

    def finish(self, cause, *, trigger):
        self.cause = cause
        self.engine.finish(cause, trigger=trigger)

    def settle(self):
        e = self.engine
        self.record['closed_vehicles'] = len(e.pending)
        from ..observation_evidence import VehicleIdentity
        from ..mohamed_lpr_engine import identity_key
        self.record['closures'] = [dict(vehicle_lifetime_id=identity_key(VehicleIdentity.from_lifetime(v.owner.lifetime)),
            cause=v.owner.lifetime.closure.value, frames=v.owner.lifetime.frames_observed,
            first_frame=v.owner.lifetime.first_receipt.frame_index, last_frame=v.owner.lifetime.last_receipt.frame_index)
            for v in e.pending.values()]
        self.record['associations'] = dict(Counter(row['association']['status'] for row in e.plate.associations))
        selected = [(s, r) for s in e.plate.lives.values() for _, r in s.best.values()]
        self.resources = list({id(r): r for r in e.plate.buffers}.values())
        # No detection/usable crop is explicitly incomplete, never "no plate".
        selected_sids = {s.life.service_track_id for s, _ in selected}
        self.record['incomplete'] = sum(sid not in selected_sids for sid in e.pending)
        if self.cause in (RunEnd.PROCESS_ERROR, RunEnd.CLOCK_DISCONTINUITY) or e.failure:
            self.record['incomplete'] = max(1, len(e.pending))
            self.record['incomplete_reason'] = 'engine_or_clock_failure'
            return self.record
        if not selected:
            if e.pending:
                self.record['incomplete_reason'] = 'no_associated_crop_evidence'
            return self.record
        if len(selected) > self.max_crops:
            raise BindingError('rtsp_crop_budget')
        if any(s.blocker is not None for s, _ in selected):
            self.record['incomplete'] = max(1, len(e.pending))
            self.record['incomplete_reason'] = 'association_evidence_blocked'
            return self.record
        if self.stop.is_set() or time.monotonic() >= self.deadline:
            self.record['incomplete'] = max(self.record['incomplete'], len(selected))
            self.record['incomplete_reason'] = 'stopped_before_v3_settlement'
            return self.record
        scope = Scope(str(self.context.org_id), self.context.run_id, self.context.camera.camera_id, uuid4().hex)
        namespace = 'rtsp-v3-' + uuid4().hex[:20]
        self.live = LiveEngine(self.owned.client, namespace=namespace, scope=scope, runtime=self.models)
        for s, resource in selected:
            receipt, frame_hash = self.frames[resource.binding.candidate.receipt.frame_index]
            bound = bind_crop(resource, s.life, plate_epoch=e.plate.plate_epoch,
                              frame_receipt=receipt, frame_sha256=frame_hash)
            self.live.add(bound, tunis_mode=self.mode)
        self.plate = PlateWorker(self.owned.client, codec=self.live.codec, namespace=namespace, consumer='rtsp-plate')
        self.peer = EnginePeer(self.owned.client, live=self.live, namespace=namespace, consumer='rtsp-engine')
        self.plate.setup(); self.peer.setup(); self.live.flush_offers()
        while True:
            if time.monotonic() >= self.deadline or self.stop.is_set():
                self.record['incomplete'] += len(selected)
                self.record['incomplete_reason'] = 'stopped_before_v3_settlement'
                return self.record
            self.plate.step(); self.peer.step()
            if (all(s.completion is not None for s in self.live.slots.values())
                    and pending(self.owned.client, self.plate) == pending(self.owned.client, self.peer) == 0):
                break
        rows = self.owned.client.xrange(self.live.journal.key(STREAMS['plate_result']))
        if len(rows) != len(selected):
            raise BindingError('rtsp_result_count')
        for _, fields in rows:
            body = self.live.codec.read(fields[b'json'])['body']
            slot = self.live.slots[body['crop']['session_id']]
            before = dict(self.models.calls)
            reference = memory_replay(slot.engine.crop, self.mode, slot.thresholds,
                                      self.live.captured(slot.engine.crop.session_id))
            if canonical(body['decision']) != canonical(asdict(reference.decision)) or before != self.models.calls:
                raise BindingError('rtsp_scalar_replay_parity')
            self.record['decisions'].append(dict(category=reference.decision.category, status=reference.decision.status,
                reason_kind=reference.decision.reason_kind, decision_sha256=body['decision_sha256']))
        self.record['accepted'] = sum(d['status'] == 'accepted' for d in self.record['decisions'])
        self.record['unresolved'] = len(rows) - self.record['accepted']
        if self.delivery_hook is not None:
            before = dict(self.models.calls)
            self.record.update(self.delivery_hook(engine=e, models=self.models, live=self.live, plate=self.plate,
                                                 owned=self.owned, report=self.record))
            if before != self.models.calls:
                raise BindingError('rtsp_delivery_invoked_recognition')
        # The closed parent is acknowledged only after its review output and
        # any requested fixture delivery are confirmed. Incomplete closures
        # stay explicit; closing the decoder never turns them into no-plate.
        acknowledged=[]
        for sid in sorted(selected_sids):
            if sid in e.pending:
                e.acknowledge(sid)
                acknowledged.append(sid)
        self.record['lifecycle_acknowledged']=len(acknowledged)
        return self.record

    def close(self):
        if self.closed:
            return self.cleanup
        active = self.live.pending_native if self.live else 0
        if self.live:
            self.resources += [a.bound.resource for s in self.live.slots.values() for a in s.adapters.values()]
        resources = list({id(r): r for r in self.resources + list(self.engine.plate.buffers)}.values())
        if not active:
            if self.live:
                for s in self.live.slots.values():
                    for adapter in s.adapters.values():
                        adapter.release(discard=True)  # Explicit incomplete bounded-run abort, not a false seal.
            for resource in resources:
                resource.release()
            self.engine.close()  # Owns its native gates.
        pending_counts = {name: pending(self.owned.client, worker) if worker else 0
                          for name, worker in (('plate', self.plate), ('engine', self.peer))}
        pending_counts['db1'] = self.record.get('pending', {}).get('db1', 0)
        self.record['pending'] = pending_counts
        self.record['group_lag'] = {}
        for name, worker in (('plate', self.plate), ('engine', self.peer)):
            if worker:
                self.record['group_lag'][name] = sum(
                    (g.get('lag') or 0) for source in worker.sources
                    for g in self.owned.client.xinfo_groups(worker.journal.key(source))
                    if g['name'].decode() == worker.journal.group)
        if self.live:
            self.record['operations'] = dict(admitted=sum(len(s.engine._requests) for s in self.live.slots.values()),
                retired=sum(len(s.engine._results) for s in self.live.slots.values()))
        for name, worker in (('plate', self.plate), ('engine', self.peer)):
            self.record[name+'_intent'] = bool(worker and self.owned.client.hexists(worker.journal.state, 'pending'))
        stages = defaultdict(lambda: dict(calls=0, seconds=0.0, failed=0))
        starts = {}
        for event in self.engine.audit.events:
            if event['phase'] == 'start':
                starts[event['kind']] = event['monotonic']
            else:
                stages[event['kind']]['calls'] += 1
                stages[event['kind']]['seconds'] += event['monotonic'] - starts.pop(event['kind'])
        if self.live:
            for slot in self.live.slots.values():
                for adapter in slot.adapters.values():
                    for event in adapter.events:
                        stages[event['role']]['calls'] += 1
                        stages[event['role']]['seconds'] += event['seconds']
                        stages[event['role']]['failed'] += event['outcome'] != 'succeeded'
        self.record.update(model_calls={k: v - self.start_calls.get(k, 0) for k, v in self.models.calls.items()},
                           vehicle_plate_calls=self.engine.audit.calls, per_stage=dict(stages),
                           execution_errors=sum(s['failed'] for s in stages.values()),
                           vehicle_placement=self.vehicle_placement, detector_placement=self.detector_placement)
        if not active:
            for worker in (self.plate, self.peer):
                if worker:
                    worker.journal.release()
        self.cleanup = dict(complete=not active and self.engine.closed, unretired_operations=active,
            retained_crops=sum(not r.released for r in resources), crop_allocations=len(resources),
            released=sum(r.released for r in resources), release_calls=sum(r.release_calls for r in resources),
            retained_closures=len(self.engine.pending),
            incomplete_aborted=bool(self.record.get('incomplete')))
        self.closed = not active
        return self.cleanup
