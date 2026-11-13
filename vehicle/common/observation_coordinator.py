"""Single closure-delivery owner for synchronous shared vehicle consumers.

Consumers implement process_vehicle_frame, finalize_vehicle and release_finalized.
Each final result must be immutable. No decoder, detector or inference lives here.
"""
from dataclasses import dataclass, field


class PendingResultsFull(RuntimeError):
    pass


class PendingCleanup(RuntimeError):
    pass


@dataclass
class _Cleanup:
    sink: object = None
    accepted: bool = False
    acknowledged: bool = False
    released: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class CompletedVehicle:
    vehicle: object
    consumer_evidence: tuple[tuple[str, object], ...]


class ObservationCoordinator:
    def __init__(self, classification, consumers, *, max_pending=128, on_completed=None):
        if classification.pipeline.lifecycle is None:
            raise ValueError('coordinator requires classification with explicit run context')
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError('max_pending must be a positive integer')
        self.classification = classification
        self.consumers = tuple(consumers.items())
        if not self.consumers:
            raise ValueError('coordinator requires at least one consumer')
        if len({id(consumer) for _, consumer in self.consumers}) != len(self.consumers):
            raise ValueError('register each consumer once')
        context = classification.pipeline.lifecycle.context
        for name, consumer in self.consumers:
            if not isinstance(name, str) or not name or consumer.context != context:
                raise ValueError('consumer names and run context must match the owner')
        self.max_pending = max_pending
        self.on_completed = on_completed
        self._completed = {}
        self._cleanup = {}
        classification.claim_closure_delivery(self)
        for _, consumer in self.consumers:
            consumer.claim_result_delivery(self)

    def _complete_cleanup(self, service_id):
        progress = self._cleanup[service_id]
        if not progress.accepted:
            if progress.sink is not None:
                progress.sink(self._completed[service_id])
            progress.accepted = True  # Successful sink return is the acceptance point.
        if not progress.acknowledged:
            self.classification.acknowledge_finalized_observations((service_id,), owner=self)
            progress.acknowledged = True
        for name, consumer in self.consumers:
            if name not in progress.released:
                # Retry after an exception, including after a release side effect.
                # Consumers must make repeated release of a finalized ID harmless.
                consumer.release_finalized(service_id, owner=self)
                progress.released.add(name)
        del self._cleanup[service_id]
        if progress.sink is not None:
            del self._completed[service_id]

    def flush_closures(self):
        # Cleanup survives owner acknowledgment and is retried independently of
        # classification's pending list. Accepted results never revisit the sink.
        for service_id in tuple(self._cleanup):
            self._complete_cleanup(service_id)
        for vehicle in self.classification.pending_finalized_observations(owner=self):
            service_id = vehicle.service_track_id
            if service_id not in self._completed:
                if len(self._completed) >= self.max_pending:
                    raise PendingResultsFull('drain completed evidence, then retry flush_closures; nothing was discarded')
                results = tuple((name, consumer.finalize_vehicle(vehicle, owner=self))
                                for name, consumer in self.consumers)
                self._completed[service_id] = CompletedVehicle(vehicle, results)
                self._cleanup[service_id] = _Cleanup(sink=self.on_completed)
            self._complete_cleanup(service_id)

    def _consume(self, frame, observation):
        for _, consumer in self.consumers:
            consumer.process_vehicle_frame(frame, observation)
        self.flush_closures()

    def process_video(self, *args, **kwargs):
        if 'on_observation' in kwargs:
            raise ValueError('register consumers with the coordinator instead of another callback')
        try:
            return self.classification.process_video(*args, on_observation=self._consume, **kwargs)
        finally:
            self.flush_closures()

    def capture_receipt(self, *args, **kwargs):
        try:
            return self.classification.capture_receipt(*args, **kwargs)
        finally:
            self.flush_closures()

    def process_frame(self, frame, frame_index, *, receipt):
        try:
            update = self.classification.process_frame(frame, frame_index, receipt=receipt)
            self._consume(frame, update.observation)
            return update
        except KeyboardInterrupt:
            self.classification.stop()
            raise
        except BaseException:
            self.classification._finish_for('process_error')
            raise
        finally:
            self.flush_closures()

    def finish(self, *args, **kwargs):
        try:
            return self.classification.finish(*args, **kwargs)
        finally:
            self.flush_closures()

    def stop(self):
        try:
            return self.classification.stop()
        finally:
            self.flush_closures()

    def reset_tracking(self):
        try:
            return self.classification.reset_tracking()
        finally:
            self.flush_closures()

    def clock_fault(self, reason):
        try:
            return self.classification.clock_fault(reason)
        finally:
            self.flush_closures()

    def drain_completed(self):
        if self._cleanup:
            raise PendingCleanup('pending acceptance/cleanup; retry flush_closures before draining')
        result = tuple(self._completed.values())
        self._completed.clear()
        # Integrated consumers explicitly drain legacy history too.
        self.classification.drain_finalized_profiles()
        return result
