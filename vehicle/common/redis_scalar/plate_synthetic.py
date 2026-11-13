"""Explicit injected Engine transcript publisher; never opens media or models."""
import argparse
from dataclasses import replace
import json

from ..plate_metadata import (PlateCandidate,PlateCropAttempt,PlateOperation,PlateTranscript,
    PlateModel,MarkerScalar,PlateMetadata)
from ..observation_evidence import VehicleIdentity
from ..colour_metadata import ColourMetadata
from ..aggregator import Aggregator,Branch
from ..vehicle_frame import VehicleBoxObservation
from .synthetic import fixture as colour_fixture
from .codec import pack_life,unpack_attempt,unpack_class_samples
from .plate_codec import pack_transcript
from .journal import Journal,PRODUCE
from .worker import load_plate_codec,connect,arguments

SCENARIOS=('whole','region-left-first','region-right-first','unreadable','zero_detection',
           'uncertain','disabled','unsupported','waiting')


def fixture(codec,*,track=1,scenario='whole',revision=0):
    if scenario not in SCENARIOS:raise ValueError('explicit injected scenario required')
    original,colour_batch=colour_fixture(codec.v0,track=track)
    old=codec.v0.decode(original);life=old.life
    identity=VehicleIdentity.from_lifetime(life)
    candidate=PlateCandidate(identity,'injected-candidate',1,11,life.first_receipt,
        'associated',True,'unregistered-injected-crop','a'*64,(10,10,110,40),(100,30),
        None,.9,'injected-quality-v1')
    ocr=PlateModel('injected-paddle-artifact',2,'ocr')
    operations=[PlateOperation('whole',1,(),'whole_ocr','succeeded',ocr,'123 تونس 4567',.96),
        PlateOperation('marker',2,(),'marker','succeeded',PlateModel('injected-marker-artifact',1,'detect'),
            marker=MarkerScalar(True,.98,(40,0,60,30),50.))]
    if scenario.startswith('region-'):
        operations[0]=replace(operations[0],raw_text='123456')
        left=PlateOperation('left',3,('marker','whole'),'left_ocr','succeeded',replace(ocr,version=5),'123',.97)
        right=PlateOperation('right',4,('marker','whole'),'right_ocr','succeeded',replace(ocr,version=9),'456',.98)
        if scenario=='region-right-first':left,right=replace(left,sequence=4),replace(right,sequence=3)
        operations.extend((left,right))
    if scenario=='unreadable':operations[0]=replace(operations[0],raw_text='unresolved')
    settlement='settled';admission=True
    if scenario=='waiting' and revision==0:
        operations[0]=replace(operations[0],outcome='pending',model=None,raw_text=None,confidence=None)
        settlement='open';admission=False
    if scenario=='uncertain':
        operations[0]=replace(operations[0],outcome='unknown',model=None,raw_text=None,confidence=None)
        settlement='uncertain'
    attempt=PlateCropAttempt('injected-crop-attempt',7,candidate.candidate_id,
        tuple(o.operation_id for o in operations),tuple(operations))
    capability=scenario if scenario in {'disabled','unsupported'} else 'enabled'
    transcript=PlateTranscript(identity,capability,(candidate,),(attempt.attempt_id,),(attempt,),
        True,admission,settlement)
    if scenario in {'zero_detection','disabled','unsupported'}:
        transcript=replace(transcript,candidates=(),expected_attempt_ids=(),attempts=())
    batch_id=codec.event_id(life,'plate.batch.identity')
    body=dict(life=pack_life(life),batch_id=batch_id,revision=revision,transcript=pack_transcript(transcript))
    plate_batch=codec.make('plate.batch',body)
    closed=codec.make('vehicle.closed.plate',dict(life=pack_life(life),
        delivery_version='natural-db1-bundle-v1',colour_batch_id=old.body['colour_batch_id'],
        plate_batch_id=batch_id,classes=old.body['classes'],plate=capability,manufacturer='unsupported'))
    evaluation=PlateMetadata().evaluate(life,transcript)
    expected=None
    if evaluation.result is not None:
        measurements=tuple(unpack_attempt(v,life) for v in codec.v0.decode(colour_batch).body['measurements'])
        colour=ColourMetadata().finalize(life,measurements)
        expected=Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join_with_plate(
            VehicleBoxObservation(life,(),'unknown',0.),unpack_class_samples(old.body['classes'],life),(
                Branch('colour','enabled',colour),Branch('plate',capability,evaluation.result if capability=='enabled' else None),
                Branch('manufacturer','unsupported'))).delivery
    return closed,colour_batch,plate_batch,expected


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);arguments(parser)
    parser.add_argument('--scenario',choices=SCENARIOS,default='whole')
    parser.add_argument('--revision',type=int,choices=[0,1],default=0)
    parser.add_argument('--track',type=int,default=1)
    parser.add_argument('--stream-id',required=True)
    parser.add_argument('--plate-first',action='store_true')
    args=parser.parse_args(argv);client=None
    try:
        codec=load_plate_codec(args.org,args.identity_namespace)
        closed,colour,batch,_=fixture(codec,track=args.track,scenario=args.scenario,revision=args.revision)
        j=Journal(None,namespace=args.namespace,trusted_org=args.org,role='plate',consumer=args.consumer)
        pairs=[('engine.lifecycle.plate',closed),('engine.evidence.colour',colour),('engine.evidence.plate',batch)]
        if args.plate_first:pairs.reverse()
        if args.revision==1:pairs=[('engine.evidence.plate',batch)]
        client=connect(args.redis)
        for stream,raw in pairs:client.eval(PRODUCE,1,j.key(stream),args.stream_id,raw,j.max_stream)
        print(json.dumps(dict(status='published',scenario=args.scenario,revision=args.revision)))
        return 0
    except Exception:
        print(json.dumps(dict(status='PUBLICATION_FAILED_RETRY_EXACT_BATCH')))
        return 2
    finally:
        if client is not None:client.close()


if __name__=='__main__':raise SystemExit(main())
