"""Engine owns models/pixels/tracking/quality. It accepts frames, never a source."""
from dataclasses import dataclass
from ..aggregator import ClassSample
from ..colour_metadata import ColourMeasurement
from ..observation_evidence import VehicleIdentity
from ..db1_rows.core import require
from ..vehicle_frame import VehicleBoxObservation
from ..vehicle_observation import RunEnd
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.pipeline import VehiclePipeline
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.classification import normalize_streetdet_class_id


@dataclass(frozen=True)
class ClosedScalars:
    owner: VehicleBoxObservation
    classes: tuple[ClassSample,...]
    measurements: tuple[ColourMeasurement,...]
    blocker: str | None = None
    manufacturer_json: str | None = None


class RecordingDetector:
    def __init__(self,target):
        self.target=target
        self.rows=()
        self.calls=0

    def process(self,pixels):
        self.calls+=1
        self.rows=self.target.process(pixels)
        return self.rows

    def close(self):
        if self.target is not None:
            close=getattr(self.target,'close',None)
            if close:close()
            self.target=None
        self.rows=()


class Engine:
    agent_enabled=False  # No provider client/operation/route exists in this interface.

    def __init__(self,context,clock,detector,*,algorithm,quality_algorithm,estimate,quality,max_vehicles=64,
                 manufacturer=None,streaming=False):
        require(1<=max_vehicles<=128,'CAPACITY_EXCEEDED','bounded vehicle ledger required')
        self.detector=RecordingDetector(detector)
        self.pipeline=VehiclePipeline(self.detector,observation_clock=clock,class_consensus_enabled=False)
        self.pipeline.claim_closure_delivery(self)
        self.algorithm,self.quality_algorithm=algorithm,quality_algorithm
        self.estimate,self.quality=estimate,quality
        self.working={}
        self.pending={}
        self.closed_ids=set()
        self.max_vehicles=max_vehicles
        self.quality_calls,self.hsv_calls=0,0
        self.closed=False
        self.failure=None
        self.manufacturer=manufacturer
        self.streaming=streaming
        self.evidence_not_admitted=0

    def process(self,packet):
        require(self.failure is None,'ENGINE_FAILED','Engine failed; no further frames may execute')
        try:self._process(packet)
        except BaseException as error:
            self.failure=getattr(error,'code','ENGINE_PROCESSING_FAILURE')
            raise

    def _process(self,packet):
        self.pipeline.process_frame(packet.pixels,packet.receipt.frame_index,receipt=packet.receipt)
        observed=self.pipeline.observation_frame.vehicles
        rows={}
        for row in self.detector.rows:
            raw=int(row[4])
            require(raw not in rows,'DUPLICATE_TRACK_ROW','duplicate raw tracker row in frame')
            rows[raw]=row
        h,w=packet.pixels.shape[:2]
        for vehicle in observed:
            sid=vehicle.service_track_id
            require(sid in self.working or len(self.working)+len(self.closed_ids)<self.max_vehicles,
                    'CAPACITY_EXCEEDED','vehicle lifetime ledger full')
            classes,attempts=self.working.setdefault(sid,([],[]))
            admitted = not self.streaming or len(classes)<20
            require(self.streaming or len(classes)<20 and len(attempts)<20,'CAPACITY_EXCEEDED',
                    'smoke ledger permits at most 20 original observations per lifetime; no truncation')
            row=rows[vehicle.raw_track_id]
            label=normalize_streetdet_class_id(int(row[6]),float(row[5]))
            require(label is not None,'CLASS_SAMPLE_MISMATCH','owner admitted excluded class')
            identity=VehicleIdentity.from_lifetime(vehicle.lifetime)
            if admitted:classes.append(ClassSample(identity,packet.receipt,label,float(row[5])))
            x1,y1,x2,y2=vehicle.bbox
            box=(max(0,min(w,x1)),max(0,min(h,y1)),max(0,min(w,x2)),max(0,min(h,y2)))
            x1,y1,x2,y2=box
            require(x2>x1 and y2>y1,'REJECTED_CROP','no faithful Colour batch for empty crop')
            crop=packet.pixels[y1:y2,x1:x2].copy()
            if self.manufacturer is not None:
                from hashlib import sha256
                maker_calls = getattr(self.manufacturer,'calls',0)
                self.manufacturer.observe(vehicle.lifetime,packet.receipt,box,crop,
                    sha256(packet.pixels.tobytes()).hexdigest())
                if not admitted and getattr(self.manufacturer,'calls',0)>maker_calls:
                    # Preserve the exact class receipt for a first eligible
                    # manufacturer crop that arrives after the evidence quota.
                    classes[-1]=ClassSample(identity,packet.receipt,label,float(row[5]))
            self.quality_calls+=1
            quality=self.quality(crop,packet.pixels.shape,box)
            self.hsv_calls+=1
            estimate=self.estimate(crop)
            if not admitted:
                self.evidence_not_admitted+=1
                continue
            attempts.append(ColourMeasurement(identity,
                f'{identity.context.run_id}:vehicle:{sid}:frame:{packet.receipt.frame_index}:hsv',
                packet.receipt,estimate.label,float(estimate.confidence),float(quality.combined),
                self.algorithm,self.quality_algorithm))
        self._retain_closures()

    def _retain_closures(self):
        for owner in self.pipeline.pending_finalized_observations(owner=self):
            sid=owner.service_track_id
            if sid in self.pending:continue
            classes,attempts=self.working.pop(sid,([],[]))
            # Freeze before any publication attempt; pixels never enter this record.
            manufacturer_json=None
            if self.manufacturer is not None:
                from contracts.db1.reference import canonical_json_bytes
                manufacturer_json=canonical_json_bytes(self.manufacturer.terminal(owner.lifetime)).decode()
            self.pending[sid]=ClosedScalars(VehicleBoxObservation(owner.lifetime,(),'unknown',0.0),
                                           tuple(classes),tuple(attempts),self.failure,manufacturer_json)
            self.closed_ids.add(sid)

    def finish(self,cause=RunEnd.SERVICE_STOP,*,trigger=None):
        self.pipeline.finish(cause,trigger=trigger)
        self._retain_closures()

    def acknowledge(self,sid):
        require(sid in self.pending,'UNKNOWN_CLOSURE','no retained closed evidence')
        self.pipeline.acknowledge_finalized_observations((sid,),owner=self)
        del self.pending[sid]

    def close(self):
        if not self.closed:
            self.detector.close()
            if self.manufacturer is not None:self.manufacturer.close()
            self.closed=True
