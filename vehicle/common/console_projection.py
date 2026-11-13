"""Public per-track view of original T1 terminals; never a source of predictions."""
import json
from .aggregator import Aggregator, Branch
from .colour_metadata import ColourMetadata
from .manufacturer.policy import evaluate
from .console_crops import diagnostic_for_result


def project_track(closed, codec, *, plate=None, incomplete='pending'):
    life = closed.owner.lifetime
    maker = evaluate(life, json.loads(closed.manufacturer_json))
    colour = ColourMetadata().finalize(life, closed.measurements)
    aggregator = Aggregator(identity_policy=codec.identity_policy,validation=codec.validation)
    branches = (Branch('colour','enabled',colour),
                Branch('plate','enabled',plate) if plate is not None else Branch('plate','disabled'),
                Branch('manufacturer','enabled',maker))
    joined = (aggregator.join_with_plate if plate is not None else aggregator.join)(
        closed.owner,closed.classes,branches)
    facts = {f['row_kind']:f['delivery'] for f in joined.delivery.payload.value()['facts']}
    evidence = json.loads(maker.evidence_json)
    receipt = evidence['binding']['receipt']
    row = dict(run_id=life.context.run_id, track_id=life.service_track_id,
        raw_track_id=life.raw_track_id, tracker_epoch=life.tracker_epoch,
        first_frame=life.first_receipt.frame_index,last_frame=life.last_receipt.frame_index,
        frames_observed=life.frames_observed, state='settled' if plate is not None else 'settling',
        vehicle_type=facts['fact_vehicle']['class']['name'],
        colour=dict(status=colour.evidence.state,label=colour.evidence.label),
        plate=dict(status=plate.outcome if plate is not None else incomplete,
                   category=plate.selected.category if plate is not None and plate.selected is not None else None,
                   diagnostic=diagnostic_for_result(plate) if plate is not None else dict(reason='unknown',frame=None)),
        manufacturer=dict(status=maker.status,make=maker.make,review_only=True,
                          frame=receipt['frame_index'] if receipt else None,
                          request_id=evidence['request_id'],crop_sha256=evidence['binding']['crop_sha256']),
        delivery=dict(postgres_commit='not_configured',redis_ack='not_attempted',
                      review_ack='pending' if maker.status!='disabled' else 'disabled'))
    # Raw OCR strings, model verbal output, probabilities and RTSP source data
    # are deliberately absent from the public row. The canonical bundle stays private.
    return row, joined.delivery
