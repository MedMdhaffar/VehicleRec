"""Explicit worker entry point: python -m vehicle.common.redis_scalar.worker."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import signal
import sys
import time
from uuid import UUID

from contracts.db1.reference import canonical_json_bytes
from ..db1_rows import ContractValidation, UUID5Policy
from ..db1_rows.core import Blocked
from .codec import Codec, vehicle_key
from .journal import Journal
from .reducer import reduce_message

ROOT=Path(__file__).resolve().parents[3]
DEFAULT_LEASE_MS={'colour':2000,'aggregator':2000,'plate':60000,'aggregator_plate':60000,'plate_requests':60000}


def load_codec(org, identity_namespace):
    return Codec(schema=json.loads((ROOT/'contracts/vehicle_redis_scalar_v1.schema.json').read_text()),
        trusted_org=org,identity_policy=UUID5Policy(UUID(str(identity_namespace))),
        validation=ContractValidation(json.loads((ROOT/'contracts/db1/rows.v0.schema.json').read_text()),
            json.loads((ROOT/'contracts/db1/diagnostics.v1.schema.json').read_text())))


def load_plate_codec(org,identity_namespace):
    from .plate_codec import PlateCodec
    return PlateCodec(schema=json.loads((ROOT/'contracts/vehicle_redis_plate_v1.schema.json').read_text()),
                      v0=load_codec(org,identity_namespace))


def load_request_codec(org,identity_namespace):
    from ..plate_requests.codec import RequestCodec
    return RequestCodec(load_plate_codec(org,identity_namespace),json.loads((ROOT/'contracts/vehicle_plate_requests_v2.schema.json').read_text()))


def connect(destination):
    # Imported and connected only by explicit CLI/harness invocation, never import.
    import redis
    return redis.Redis.from_url(destination,socket_timeout=2,socket_connect_timeout=2,
                                decode_responses=False)


class Worker:
    def __init__(self, client, *, codec, namespace, role, consumer, **options):
        self.codec,self.client,self.role=codec,client,role
        options.setdefault('lease_ms',DEFAULT_LEASE_MS.get(role,2000))
        self.journal=Journal(client,namespace=namespace,trusted_org=codec.org,role=role,consumer=consumer,**options)
        self.sources={'colour':['engine.evidence.colour'],
                      'aggregator':['engine.lifecycle','results.colour'],
                      'plate':['engine.evidence.plate'],
                      'aggregator_plate':['engine.lifecycle.plate','results.colour','results.plate']}[role]
        self.cursor={s:'0-0' for s in self.sources}
        self.next_source=0
        self.prefer_pending={source:True for source in self.sources}

    def setup(self):
        from redis.exceptions import ResponseError
        for source in self.sources:
            try:
                self.client.xgroup_create(self.journal.key(source),self.journal.group,id='0-0',mkstream=True)
            except ResponseError as error:
                if not str(error).startswith('BUSYGROUP'):
                    raise

    def process(self,source,entry_id,fields):
        j=self.journal
        raw=fields.get(b'json',b'')
        digest=sha256(raw).hexdigest()
        marker='rejected:'+source+':'+entry_id.decode()
        try:
            if set(fields)!={b'json'}:
                raise Blocked('INVALID_MESSAGE','one json field required')
            message=self.codec.decode(raw)
            expected={'engine.evidence.colour':'colour.batch','engine.lifecycle':'vehicle.closed','results.colour':'colour.terminal','engine.evidence.plate':'plate.batch',
                      'engine.lifecycle.plate':'vehicle.closed.plate','results.plate':'plate.terminal'}
            if message.kind!=expected[source]:
                raise Blocked('WRONG_STREAM','message type differs from input stream')
            digest=sha256(message.canonical).hexdigest()
            marker='seen:'+message.event_id
            seen=j.seen(marker)
            if seen is not None and seen.decode()!=digest:
                raise Blocked('PAYLOAD_CONFLICT','logical event identity content changed')
            if seen is not None:
                # handoff's duplicate path ACKs under a fence after pending repair.
                j.handoff(source=source,entry_id=entry_id,marker=marker,digest=digest)
                return
            key=vehicle_key(message.life)
            state,outputs=reduce_message(self.codec,self.role,message,j.read_state(key))
            j.handoff(source=source,entry_id=entry_id,marker=marker,digest=digest,
                      vehicle=key,state=state,outputs=outputs)
        except Blocked as error:
            if self.role=='plate' and error.code=='WAITING_DEPENDENCY':
                return  # keep source in PEL; pending/new fairness can accept the missing initial revision
            if self.role in {'plate','aggregator_plate'} and error.code=='CAPACITY_EXCEEDED':
                raise  # no ACK/seen marker/diagnostic substitution for recoverable byte capacity
            # Keep hostiles/credentials/exception bodies out of retained diagnostics.
            diagnostic=canonical_json_bytes(dict(schema_version='vehicle-redis-diagnostic-v1',
                org_id=str(self.codec.org),code=error.code,source=source,input_sha256=sha256(raw).hexdigest()))
            j.handoff(source=source,entry_id=entry_id,marker='rejected:'+source+':'+entry_id.decode(),
                digest=sha256(raw).hexdigest(),outputs=[('diagnostics.'+self.role,diagnostic)])

    def step(self):
        j=self.journal
        if not j.token:
            if not j.acquire():
                return False
        j.renew()
        if j.repair():
            return True
        # Round-robin streams first; each stream independently alternates its
        # pending/new preference, avoiding correlation between two global toggles.
        sources=self.sources[self.next_source:]+self.sources[:self.next_source]
        for source in sources:
            for pending in (self.prefer_pending[source],not self.prefer_pending[source]):
                if pending:
                    claimed=self.client.xautoclaim(j.key(source),j.group,j.consumer,0,
                        start_id=self.cursor[source],count=1)
                    self.cursor[source]=claimed[0]
                    rows=claimed[1]
                else:
                    # A Redis BLOCK timeout is not a wall-clock upper bound.
                    # Scan without blocking; the CLI already backs off when idle.
                    received=self.client.xreadgroup(j.group,j.consumer,{j.key(source):'>'},count=1)
                    rows=received[0][1] if received else []
                if rows:
                    # Advance before processing, so a failed attempt cannot pin
                    # the same source at the front on the next bounded turn.
                    self.next_source=(self.sources.index(source)+1)%len(self.sources)
                    self.prefer_pending[source]=not pending
                    j.renew()  # fail closed if polling outlived this ownership
                    self.process(source,*rows[0])
                    return True
        return False


def arguments(parser):
    parser.add_argument('--redis',required=True,help='Explicit redis://, rediss:// or unix:// destination')
    parser.add_argument('--namespace',required=True)
    parser.add_argument('--org',required=True,type=UUID,help='Trusted deployment org binding, not learned from messages')
    parser.add_argument('--identity-namespace',required=True,type=UUID,help='Existing stable natural UUID5 namespace')
    parser.add_argument('--consumer',required=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--role',required=True,choices=['colour','aggregator','plate','aggregator_plate','plate_requests'])
    parser.add_argument('--lease-ms',type=int,default=None)
    parser.add_argument('--max-events',type=int,default=256)
    parser.add_argument('--max-vehicles',type=int,default=128)
    parser.add_argument('--max-stream',type=int,default=256)
    parser.add_argument('--run-seconds',type=float,default=0,help='0 runs until SIGINT/SIGTERM')
    parser.add_argument('--pause-at',choices=['before_prepare','prepared','published','before_ack','acknowledged'],help='Explicit crash-test hook only')
    parser.add_argument('--resume-file',type=Path,help='Explicit phase-test release marker')
    parser.add_argument('--pause-kind',choices=['crop','admission','evidence','sealed'],help='Restrict v2 crash-test hook to this input kind')
    parser.add_argument('--phase-file',type=Path,help='Crash-test marker, not a state store')
    args=parser.parse_args(argv)
    if args.pause_at and not args.phase_file:
        parser.error('--pause-at requires --phase-file')
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    def hook(phase):
        if phase==args.pause_at and (not args.pause_kind or getattr(worker,'current_kind',None)==args.pause_kind):
            args.phase_file.write_text(phase)
            while not stopping and not (args.resume_file and args.resume_file.exists()):
                time.sleep(.025)
    client=None
    try:
        codec=(load_request_codec if args.role=='plate_requests' else load_plate_codec if args.role in {'plate','aggregator_plate'} else load_codec)(args.org,args.identity_namespace)
        client=connect(args.redis)
        from ..plate_requests.worker import RequestWorker
        worker=(RequestWorker if args.role=='plate_requests' else Worker)(client,codec=codec,namespace=args.namespace,role=args.role,consumer=args.consumer,
            lease_ms=args.lease_ms if args.lease_ms is not None else DEFAULT_LEASE_MS[args.role],max_events=args.max_events,max_vehicles=args.max_vehicles,max_stream=args.max_stream,hook=hook)
        worker.setup()
        deadline=time.monotonic()+args.run_seconds if args.run_seconds else None
        from redis.exceptions import RedisError
        while not stopping and (deadline is None or time.monotonic()<deadline):
            try:
                if not worker.step():
                    time.sleep(.05)
            except Blocked as error:
                if error.code!='CAPACITY_EXCEEDED':
                    raise
                print(json.dumps(dict(component=args.role,status='CAPACITY_EXCEEDED')),file=sys.stderr,flush=True)
                time.sleep(.05)
            except RedisError as error:
                code=str(error).split()[0]
                # Do not print Redis URL or unsanitized server/client errors.
                safe=code if code in {'CAPACITY_EXCEEDED','STALE_OWNER','PAYLOAD_CONFLICT',
                    'REVISION_CONFLICT','REPAIR_REQUIRED','PUBLICATION_CONFLICT','OUTPUT_MISSING','INTENT_CHANGED'} else 'REDIS_RETRY'
                print(json.dumps(dict(component=args.role,status=safe)),file=sys.stderr,flush=True)
                if safe=='STALE_OWNER':
                    worker.journal.token=None
                time.sleep(.05)
        try:
            if worker.journal.token:
                worker.journal.repair()
                worker.journal.release()
        except RedisError:
            print(json.dumps(dict(component=args.role,status='RECOVERY_PENDING')),file=sys.stderr)
        return 0
    except Exception:
        print(json.dumps(dict(status='STARTUP_OR_INPUT_FAILED')),file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__=='__main__':
    raise SystemExit(main())
