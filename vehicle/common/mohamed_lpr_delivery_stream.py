"""Fenced opt-in Aggregator publication. Sealed v3 input is never a DB1 input."""
from hashlib import sha256
from uuid import UUID
from .db1_rows.core import require
from .mohamed_lpr_delivery import ProjectionGap
from .mohamed_lpr_stream_codec import STREAMS
from .redis_scalar.journal import Journal
from .redis_scalar.worker import Worker
from .redis_scalar.codec import bounded_json

SOURCE = STREAMS['plate_result']


class DeliveryJournal(Journal):
    def __init__(self, client, *, namespace, codec, consumer, **options):
        options.setdefault('lease_ms',60000)
        super().__init__(client,namespace=namespace,trusted_org=codec.org,role='aggregator_plate',consumer=consumer,**options)
        self.group='mohamed-tunis-projection-v1'
        self.lease=self.prefix+'mohamed-tunis-projection-v1:lease'
        self.fence=self.prefix+'mohamed-tunis-projection-v1:fence'
        self.state=self.prefix+'mohamed-tunis-projection-v1:journal'
        self.source_prefix=f'{namespace}:{{mohamed-lpr-v3.{UUID(str(codec.org))}}}:'

    def key(self, suffix):
        if suffix == SOURCE:
            return self.source_prefix+suffix
        return super().key(suffix)

    def read_state(self, key):
        raw=self.client.hget(self.state,'vehicle:'+key)
        return None if raw is None else bounded_json(raw,max_bytes=self.max_state_bytes,
            max_depth=40,max_nodes=300000,max_string=2*1024*1024)


class TunisDeliveryWorker(Worker):
    """Context supplies frozen Engine metadata, never pixel handles on the wire.

    New worker instances reuse retained intents; accepted canonical output and
    complete v3 evidence precede input ACK. The DB1 consumer separately commits
    and ACKs db1.delivery.v1 using its own group. No cluster-slot claim is made.
    """
    def __init__(self, client, *, codec, namespace, contexts, proof_reader, consumer='tunis-projector', **options):
        self.client,self.codec,self.contexts,self.proof_reader=client,codec,contexts,proof_reader
        self.journal=DeliveryJournal(client,namespace=namespace,codec=codec,consumer=consumer,**options)
        self.sources=[SOURCE];self.cursor={SOURCE:'0-0'};self.prefer_pending={SOURCE:True};self.next_source=0

    def process(self, source, entry_id, fields):
        require(set(fields)=={b'json'},'INVALID_MESSAGE','one scalar message required')
        raw=fields[b'json'];message=self.codec.source.read(raw)
        require(message['kind']=='plate_result','WRONG_STREAM','sealed result required')
        checksum=sha256(raw).hexdigest();marker='seen:'+message['event_id']
        old=self.journal.seen(marker)
        require(old is None or old.decode()==checksum,'PAYLOAD_CONFLICT','accepted sealed result changed')
        if old is not None:
            self.journal.handoff(source=source,entry_id=entry_id,marker=marker,digest=checksum)
            return
        session=message['body']['crop']['session_id']
        require(session in self.contexts,'MISSING_RECEIPT','original Engine closure/context required')
        context=self.contexts[session]
        proof=self.proof_reader(session)
        require(proof['status']=='terminal' and proof['result']==message['body'],
                'PLATE_DECISION_MISMATCH','source must match retained terminal journal')
        outputs=[]
        try:
            delivery=self.codec.from_engine(sealed_raw=raw,proof=proof['log'],**context)
        except ProjectionGap as error:
            state=dict(status='review_only',reason=str(error),sealed_json=raw.decode(),
                       proof_json=__import__('json').dumps(proof['log'],ensure_ascii=False,allow_nan=False))
        else:
            state=dict(status='published',source_sha256=checksum,delivery_sha256=sha256(delivery).hexdigest(),
                       sealed_json=raw.decode(),proof_json=self.codec.decode(delivery).body['plate']['proof_json'])
            outputs=[('db1.delivery.v1',delivery)]
        self.journal.handoff(source=source,entry_id=entry_id,marker=marker,digest=checksum,
                             vehicle=session,state=state,outputs=outputs)
