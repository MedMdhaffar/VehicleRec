"""Explicit test stand-ins: private allocations, work plans and injected calls.

No recognition policy or model is implemented. The host supplies the plan; the
Engine adapter produces all operation outcomes/PlateTranscript records itself.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from .engine_plate import (PlateExecutionAdapter,CropBinding,CropPlan,OperationPlan,
    InjectedExecutor,decode_ocr,decode_marker)
from .plate_metadata import PlateCandidate,PlateModel
from .observation_evidence import VehicleIdentity
from .redis_scalar.synthetic import fixture as colour_fixture
from .redis_scalar.codec import pack_life


class InjectedCrop:
    def __init__(self,binding,pixels):
        self._binding=binding;self._pixels=bytes(pixels)
        self.read_calls=0;self.release_calls=0;self.released=False
        self.fail_release=0

    @property
    def binding(self):return self._binding

    def read(self):
        if self.released:raise RuntimeError('private allocation already released')
        self.read_calls+=1
        return self._pixels

    def release(self):
        self.release_calls+=1
        if self.fail_release:
            self.fail_release-=1;raise RuntimeError('injected release failure')
        self._pixels=None;self.released=True


@dataclass
class InjectedCall:
    model: PlateModel
    output: dict
    calls: int = 0
    dependencies: tuple = ()

    def __call__(self,request,context):
        self.calls+=1
        try:
            context.started(self.model)
            self.dependencies=request.dependencies
            # Inspect Engine-private input, never retain it or return its bytes.
            if request.pixels is None:raise RuntimeError('no private pixels')
            return json.dumps(self.output,ensure_ascii=False).encode()
        finally:context.retired()


def fixture(codec,*,track=1,scenario='whole',scheduler=None,admit=True,adapter_options=None):
    if scenario not in {'whole','region-left-first','region-right-first'}:
        raise ValueError('explicit injected work-plan scenario required')
    original,colour=colour_fixture(codec.v0,track=track)
    message=codec.v0.decode(original);life=message.life
    identity=VehicleIdentity.from_lifetime(life)
    pixels=bytearray(100*30*3)
    candidate=PlateCandidate(identity,'engine-candidate',1,11,life.first_receipt,
        'associated',True,'opaque-injected-crop',sha256(pixels).hexdigest(),(10,10,110,40),
        (100,30),None,.9,'injected-quality-v1')
    binding=CropBinding(candidate,'engine-private-handle',len(pixels));crop=InjectedCrop(binding,pixels)
    values=[('whole',PlateModel('synthetic-whole-ocr',2,'ocr'),dict(raw_text='123 تونس 4567' if scenario=='whole' else '123456',confidence=.96)),
        ('marker',PlateModel('synthetic-marker',1,'detect'),dict(detected=True,confidence=.98,bbox=[40,0,60,30],center_x=50.))]
    plans=[OperationPlan('whole',1,(),'whole_ocr','whole'),OperationPlan('marker',2,(),'marker','marker')]
    if scenario!='whole':
        values.extend([('left',PlateModel('synthetic-left-ocr',5,'ocr'),dict(raw_text='123',confidence=.97)),
                       ('right',PlateModel('synthetic-right-ocr',9,'ocr'),dict(raw_text='456',confidence=.98))])
        first,last=('left','right') if scenario=='region-left-first' else ('right','left')
        plans.extend([OperationPlan(first,3,('whole','marker'),first+'_ocr',first),
                      OperationPlan(last,4,('whole','marker'),last+'_ocr',last)])
    calls={name:InjectedCall(model,output) for name,model,output in values}
    executors=tuple(InjectedExecutor(name,model,calls[name],decode_marker if name=='marker' else decode_ocr)
                    for name,model,_ in values)
    plan=CropPlan('engine-crop-attempt',7,candidate.candidate_id,tuple(plans))
    adapter=PlateExecutionAdapter(life,executors,expected_attempt_ids=(plan.attempt_id,),scheduler=scheduler,**(adapter_options or {}))
    if admit:adapter.admit(binding,crop,plan)
    batch_id=codec.event_id(life,'engine-plate-adapter.batch')
    closed=codec.make('vehicle.closed.plate',dict(life=pack_life(life),delivery_version='natural-db1-bundle-v1',
        colour_batch_id=message.body['colour_batch_id'],plate_batch_id=batch_id,
        classes=message.body['classes'],plate='enabled',manufacturer='unsupported'))
    return adapter,crop,plan,calls,closed,colour,batch_id
