"""Owned console DB1 host: existing consumer, disposable fixtures, real XACK.

Runs separately from CUDA. Control/evidence contain no DSN, URI or plate text.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def consume(directory):
    from vehicle.common.console_runs import atomic_json
    from vehicle.common.console_stack import configure, database_probe
    from vehicle.common.db1_consumer import RedisDB1Consumer
    from vehicle.common.mohamed_lpr_test_database import OwnedDeliveryDatabase
    from vehicle.common.redis_scalar.worker import load_request_codec
    from vehicle.common.redis_scalar.journal import Journal
    import redis

    configure()
    database_probe()
    control = json.loads((directory/'db1-control.json').read_text())
    codec = load_request_codec(control['org'], control['identity_namespace']).v1
    client = redis.Redis(unix_socket_path=control['redis_socket'], socket_timeout=2)
    journal = Journal(client, namespace=control['namespace'], trusted_org=codec.org,
                      role='aggregator_plate', consumer='console-db1')
    progress = dict(status='running', tracks={}, evidence=[], cleanup=[])
    def save():
        atomic_json(directory/'db1-progress.json', progress)
    try:
        raw_rows = client.xrange(journal.key('db1.delivery.v1'))
        if len(raw_rows) != control['deliveries']:
            raise RuntimeError('db1_delivery_count_mismatch')
        for entry_id, fields in raw_rows:
            raw = fields[b'json']
            message = codec.decode(raw)
            sid = str(message.life.service_track_id)
            # Retain the exact canonical wire envelope privately for inspection
            # even if a later transaction fails. It is never served by the API.
            path = directory/('db1-wire-'+sid+'.json')
            if path.exists():
                if path.read_bytes() != raw:
                    raise RuntimeError('db1_retained_wire_mismatch')
            else:
                fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as retained:
                    retained.write(raw)
            state = dict(postgres_commit='pending', redis_ack='pending')
            progress['tracks'][sid] = state
            save()
            # Exact existing Phase 5 fixture; each delivery has its own owned
            # database and restricted LOGIN. No application writes to maintenance.
            media = control.get('source_media')
            if media is not None:
                media = dict(media, ts=message.life.context.started_at, bucket='owned-console-fixture',
                             object_key='clips/'+media['sha256']+'.mp4')
            with OwnedDeliveryDatabase(codec, raw, source_media=media) as db:
                natural = db.consumer()
                entry = natural.prepare(raw)
                reader = RedisDB1Consumer(client, journal, natural, name='console-db1')
                boundary = []
                def hook(phase):
                    if phase == 'committed':
                        state['postgres_commit'] = 'committed'
                        save()  # The consumer has confirmed the transaction.
                        snapshot = db.inspect(entry)  # Separate real PG connection.
                        expected = {fact.row_kind: 1 for fact in entry.construction.ready_rows}
                        assert all(snapshot['rows'][kind] == count for kind, count in expected.items())
                        assert snapshot['acceptances'] == 1
                        assert snapshot['row_receipts'] == len(expected)
                        if media is not None:
                            with db.fixture.connect() as connection:
                                with connection.transaction(), connection.cursor() as cursor:
                                    cursor.execute("SELECT set_config('app.org_id',%s,true)", (str(codec.org),))
                                    cursor.execute('SELECT sha256,bytes,width,height FROM public.media_object WHERE media_key=%s',
                                                   (db.fixture.res.run.source_media_key,))
                                    assert cursor.fetchone() == tuple(media[k] for k in ('sha256','bytes','width','height'))
                            snapshot['source_media'] = {k:media[k] for k in ('sha256','bytes','width','height')}
                        pending = client.xpending_range(journal.key('db1.delivery.v1'), reader.group,
                                                        min=entry_id, max=entry_id, count=1)
                        assert len(pending) == 1
                        boundary.append(dict(before_ack=snapshot, exact_entry_pending=True,
                                             entry_id=entry_id.decode(), track_id=int(sid)))
                        save()
                natural.hook = hook
                def same_payload(actual_id, actual_raw):
                    if actual_id != entry_id or actual_raw != raw:
                        raise RuntimeError('db1_physical_delivery_mismatch')
                result = reader.step(before_process=same_payload)
                assert result.disposition == 'new' and len(boundary) == 1
                assert reader.last_delivery.ack_reply == 1
                assert not client.xpending_range(journal.key('db1.delivery.v1'), reader.group,
                                                min=entry_id, max=entry_id, count=1)
                state['redis_ack'] = 'acknowledged'
                progress['evidence'].append(dict(**boundary[0], ack_reply=1,
                    fact_count=result.fact_count, acceptance_sha256=result.acceptance_sha256,
                    source_kind=db.fixture.res.run.source_kind,
                    source_media_key=db.fixture.res.run.source_media_key,
                    database_settings=db.settings))
                save()
            progress['cleanup'].append(db.cleanup)
            save()
        group = next(g for g in client.xinfo_groups(journal.key('db1.delivery.v1'))
                     if g['name'] == b'db1-natural-v1') if raw_rows else None
        assert group is None or group['pending'] == group['lag'] == 0
        progress.update(status='completed', pending=0, lag=0)
        save()
    except Exception:
        progress['status'] = 'failed'
        save()
        raise
    finally:
        client.close()


if __name__ == '__main__':
    # Never print exception text: SQL/native errors may contain private values.
    try:
        consume(Path(sys.argv[1]).resolve(strict=True))
    except Exception:
        raise SystemExit(1) from None
