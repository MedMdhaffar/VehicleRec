"""Pure bounded reducers around Colour/Aggregator. State is explicit scalar JSON."""
from contracts.db1.reference import canonical_json_bytes
from ..aggregator import Aggregator, Branch
from ..colour_metadata import ColourMetadata
from ..db1_rows.core import require
from ..vehicle_frame import VehicleBoxObservation
from .codec import (unpack_life, pack_life, unpack_attempt, unpack_colour,
                    pack_colour, unpack_class_samples)


def reduce_message(codec, role, message, previous):
    if role in {'plate','aggregator_plate'}:
        from .plate_reducer import reduce_plate
        return reduce_plate(codec,role,message,previous)
    state = dict(previous or {})
    life, body = message.life, message.body
    if role == 'colour':
        require(message.kind == 'colour.batch', 'WRONG_STREAM', 'Colour accepts complete batches only')
        require(not state or (state.get('batch_id') == message.event_id
                and canonical_json_bytes(state['batch']) == canonical_json_bytes(body)),
                'PAYLOAD_CONFLICT', 'accepted batch changed for vehicle')
        if state:
            return state, []
        result = ColourMetadata().finalize(life,tuple(unpack_attempt(a,life) for a in body['measurements']))
        output = codec.encode('colour.terminal',codec.event_id(life,'colour.terminal'),
            dict(life=pack_life(life),batch_id=message.event_id,result=pack_colour(result)))
        return dict(batch_id=message.event_id,batch=body), [('results.colour',output)]
    require(role == 'aggregator' and message.kind in {'vehicle.closed','colour.terminal'},
            'WRONG_STREAM','Aggregator accepts lifecycle and Colour terminals only')
    if message.kind == 'vehicle.closed':
        unpack_class_samples(body['classes'],life)
    slot = 'closed' if message.kind == 'vehicle.closed' else 'colour'
    require(slot not in state or canonical_json_bytes(state[slot]) == canonical_json_bytes(body),
            'PAYLOAD_CONFLICT','accepted component input changed for vehicle')
    state[slot] = body
    if 'closed' not in state or 'colour' not in state:
        state['status'] = 'waiting'
        return state, []
    closed, terminal = state['closed'], state['colour']
    require(canonical_json_bytes(closed['life']) == canonical_json_bytes(terminal['life'])
            and closed['colour_batch_id'] == terminal['batch_id'],
            'PARENT_MISMATCH','joined lifetime or complete batch identity differs')
    life = unpack_life(closed['life'])
    colour = unpack_colour(terminal['result'],life)
    owner = VehicleBoxObservation(life,(), 'unknown',0.0)
    classes = unpack_class_samples(closed['classes'],life)
    result = Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join(owner,classes,(
        Branch('colour','enabled',colour),Branch('plate',closed['plate']),Branch('manufacturer','unsupported')))
    state['status'] = result.state
    if result.delivery is None:
        require('delivery' not in state, 'PAYLOAD_CONFLICT','accepted output cannot regress to waiting')
        return state, []
    payload = result.delivery.payload.value()
    require('delivery' not in state or canonical_json_bytes(state['delivery']) == canonical_json_bytes(payload),
            'PAYLOAD_CONFLICT','accepted natural output changed')
    if 'delivery' in state:
        return state, []
    state['delivery'] = payload
    raw = codec.encode('db1.delivery',codec.event_id(life,'db1.delivery'),
        dict(life=pack_life(life),bundle=payload,bundle_sha256=result.delivery.digest))
    return state,[('db1.delivery',raw)]
