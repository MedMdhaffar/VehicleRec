"""Explicit scalar producer; no camera, image, model, provider or database."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

from ..db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
from ..frame_receipt import FrameReceipt
from ..vehicle_observation import VehicleLifetime, VehicleClosure
from ..observation_evidence import VehicleIdentity, RecognitionIdentity
from ..colour_metadata import ColourMeasurement
from .codec import pack_life,pack_receipt,pack_attempt
from .journal import Journal,PRODUCE
from .worker import arguments,connect,load_codec

ORG=UUID('12345678-1234-4234-8234-123456789012')
IDENTITY_NAMESPACE=UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb')


def fixture(codec, *, track=1,plate='disabled'):
    start=datetime(2026,10,8,22,59,59,tzinfo=timezone.utc)
    camera=CameraRegistration(codec.org,'scalar-camera',None,'Africa/Tunis',start,None)
    context=RunContext(codec.org,camera,'scalar-run','file',start,'test-slice',
        ResolutionInfo(None,None,'vehicle',ModelRegistration(None,'detector-v1',1,'detect'),
            None,'unresolved scalar fixture',False),'development')
    receipts=tuple(FrameReceipt(i,start+timedelta(seconds=i),100+i,i*.04) for i in range(5))
    life=VehicleLifetime(context,track,0,19+track-1,1,receipts[0],receipts[-1],5,VehicleClosure.END_OF_INPUT)
    identity=VehicleIdentity.from_lifetime(life)
    measurements=tuple(ColourMeasurement(identity,f'original-{i}',r,'red',.8,.9,
        RecognitionIdentity('hsv-config-v1',1,'classify'),'quality-enhanced-v1') for i,r in enumerate(receipts))
    batch_id=codec.event_id(life,'colour.batch')
    batch=codec.encode('colour.batch',batch_id,dict(life=pack_life(life),complete=True,
        expected_attempt_ids=[m.attempt_id for m in measurements],measurements=[pack_attempt(m) for m in measurements]))
    closed=codec.encode('vehicle.closed',codec.event_id(life,'vehicle.closed'),dict(life=pack_life(life),
        colour_batch_id=batch_id,classes=[dict(receipt=pack_receipt(r),label='car',confidence=.8) for r in receipts],
        plate=plate,manufacturer='unsupported'))
    return closed,batch


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--track',type=int,default=1)
    parser.add_argument('--plate',choices=['disabled','enabled'],default='disabled')
    parser.add_argument('--order',choices=['closed-first','colour-first'],default='closed-first')
    parser.add_argument('--stream-id',required=True,help='Stable explicit Redis ID e.g. 1-0; retain on retry')
    args=parser.parse_args(argv)
    client=None
    try:
        codec=load_codec(args.org,args.identity_namespace)
        closed,batch=fixture(codec,track=args.track,plate=args.plate)
        j=Journal(None,namespace=args.namespace,trusted_org=args.org,role='colour',consumer=args.consumer)
        client=connect(args.redis)
        pairs=[('engine.lifecycle',closed),('engine.evidence.colour',batch)]
        if args.order=='colour-first':
            pairs.reverse()
        for stream,raw in pairs:
            client.eval(PRODUCE,1,j.key(stream),args.stream_id,raw,j.max_stream)
        print(json.dumps(dict(status='published',track=args.track,order=args.order)))
        return 0
    except Exception as error:
        code=str(error).split()[0] if str(error) else ''
        print(json.dumps(dict(status=code if code in {'CAPACITY_EXCEEDED','PAYLOAD_CONFLICT'} else 'PRODUCER_FAILED')))
        return 2
    finally:
        if client is not None:
            client.close()


if __name__=='__main__':
    raise SystemExit(main())
