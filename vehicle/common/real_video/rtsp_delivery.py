"""Private fixture handshake; unchanged Aggregator and commit-before-ACK consumer."""
from dataclasses import asdict
import json
import os
import time


def projection_hook(control, stop, deadline, *, engine, models, live, plate, owned, report):
    from ..mohamed_lpr_delivery import TunisDeliveryCodec, OCRBinding
    from ..mohamed_lpr_delivery_stream import TunisDeliveryWorker, SOURCE
    from ..observation_evidence import RecognitionIdentity
    from ..redis_scalar.worker import load_codec
    from ..redis_scalar.synthetic import IDENTITY_NAMESPACE
    provenance = models.expected('registration')
    binding = OCRBinding(provenance.model_ref,
        RecognitionIdentity(provenance.model_id + ':' + provenance.model_ref, 1, 'ocr'))
    codec = TunisDeliveryCodec(base=load_codec(live.scope.org_id, IDENTITY_NAMESPACE),
                              scope=live.scope, bindings=(binding,))
    contexts = {}
    for session, slot in live.slots.items():
        bound = slot.adapters[slot.engine.crop.generation].bound
        sid = bound.resource.binding.candidate.identity.service_track_id
        contexts[session] = dict(bound=bound, closed=engine.pending[sid], expected_sessions=tuple(
            k for k, s in live.slots.items() if s.engine.crop.vehicle_lifetime_id == slot.engine.crop.vehicle_lifetime_id))
    namespace = live.journal.prefix.split(':')[0]
    worker = TunisDeliveryWorker(owned.client, codec=codec, namespace=namespace,
        contexts=contexts, proof_reader=plate.journal.read_state)
    try:
        worker.setup()
        for _ in range(len(contexts) * 2 + 2):
            worker.step()
        count = owned.client.xlen(worker.journal.key('db1.delivery.v1'))
        report['db1_publications'] = count
        projection_pending = owned.client.xpending(worker.journal.key(SOURCE), worker.journal.group)['pending']
        if projection_pending:
            raise RuntimeError('rtsp_projection_incomplete')
        if not count:
            return dict(db1_records=0, db1_publications=0, projection_pending=0,
                        delivery_status='review_only_no_supported_private_read')
        report['db1_unconsumed'] = count
        ready = control / ('ready-' + live.scope.run_id + '.json')
        done = control / ('done-' + live.scope.run_id + '.json')
        body = dict(scope=asdict(live.scope), namespace=namespace, bindings=[asdict(binding)],
                    redis_socket=str(owned.socket), identity_namespace=str(IDENTITY_NAMESPACE), deliveries=count)
        temporary = ready.with_suffix('.tmp')
        temporary.write_text(json.dumps(body)); os.chmod(temporary, 0o600); temporary.replace(ready)
        try:
            while not done.exists():
                if stop.is_set() or time.monotonic() >= deadline:
                    raise RuntimeError('rtsp_db1_confirmation_deadline')
                time.sleep(.02)
            result = json.loads(done.read_text())
            if result.get('status') != 'PASS':
                raise RuntimeError('rtsp_owned_db1_failed')
            report['db1_unconsumed'] = 0
        finally:
            groups = owned.client.xinfo_groups(worker.journal.key('db1.delivery.v1'))
            group = next((g for g in groups if g['name'] == b'db1-natural-v1'), None)
            report['pending']['db1'] = group['pending'] if group is not None else 0
            report['db1_group_lag'] = group.get('lag') if group is not None else count
        return dict(db1_records=result['db1_records'], db1_publications=count, db1=result,
                    pending=dict(db1=result['db1_pending']), projection_pending=0)
    finally:
        worker.journal.release()


def consume_owned(control):
    """Called only in the explicitly configured PG host, never in CUDA Engine."""
    import redis
    from ..db1_consumer import RedisDB1Consumer
    from ..mohamed_lpr_delivery import TunisDeliveryCodec, OCRBinding
    from ..mohamed_lpr_delivery_stream import DeliveryJournal
    from ..mohamed_lpr_stream_codec import Scope
    from ..mohamed_lpr_test_database import OwnedDeliveryDatabase
    from ..observation_evidence import RecognitionIdentity
    from ..redis_scalar.worker import load_codec
    scope = Scope(**control['scope'])
    codec = TunisDeliveryCodec(base=load_codec(scope.org_id, control['identity_namespace']), scope=scope,
        bindings=tuple(OCRBinding(b['model_ref'], RecognitionIdentity(**b['identity'])) for b in control['bindings']))
    client = redis.Redis(unix_socket_path=control['redis_socket'], socket_timeout=2)
    result = dict(status='PASS', db1_records=0, db1_pending=0, confirmed=[], cleanup=[])
    try:
        journal = DeliveryJournal(client, namespace=control['namespace'], codec=codec, consumer='rtsp-db1')
        rows = client.xrange(journal.key('db1.delivery.v1'))
        if len(rows) != control['deliveries']:
            raise RuntimeError('rtsp_delivery_count_mismatch')
        for _, fields in rows:
            raw = fields[b'json']
            with OwnedDeliveryDatabase(codec, raw) as db:
                natural = db.consumer(); entry = natural.prepare(raw)
                reader = RedisDB1Consumer(client, journal, natural, name='rtsp-db1')
                boundary = []
                def hook(phase):
                    if phase == 'committed':
                        state = db.inspect(entry)  # Separate restricted connection before XACK.
                        assert state['rows']['fact_plate_read'] == state['rows']['fact_vehicle'] == 1
                        assert state['acceptances'] == 1
                        assert client.xpending(journal.key('db1.delivery.v1'), reader.group)['pending'] == 1
                        boundary.append(state)
                natural.hook = hook
                delivered = reader.step(before_process=lambda _, actual: _same_payload(actual, raw))
                assert delivered.disposition == 'new' and len(boundary) == 1 and reader.last_delivery.ack_reply == 1
                result['db1_records'] += delivered.fact_count
                result['confirmed'].append(dict(before_ack=boundary[0], ack_reply=1,
                    acceptance_sha256=delivered.acceptance_sha256,
                    run_id=db.fixture.res.run.run_id, source_kind=db.fixture.res.run.source_kind,
                    source_media_key=db.fixture.res.run.source_media_key, time_basis=codec.decode(raw).life.context.time_basis))
            result['cleanup'].append(db.cleanup)
        result['db1_pending'] = client.xpending(journal.key('db1.delivery.v1'), reader.group)['pending']
        assert result['db1_pending'] == 0
        return result
    finally:
        client.close()


def _same_payload(actual, expected):
    if actual != expected:
        raise RuntimeError('rtsp_delivery_identity_mismatch')
