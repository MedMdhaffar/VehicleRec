"""Pure accepted delivery DTO/serializer shared with the historical outbox.

The byte format remains db1-local-outbox-v1 for adapter receipt compatibility;
using this DTO neither imports nor opens a SQLite target.
"""
from dataclasses import dataclass
from contracts.db1.reference import canonical_json_bytes
from .db1_rows import FrozenJSON, Construction


@dataclass(frozen=True, slots=True)
class AcceptedEntry:
    sequence: int
    logical_identity: FrozenJSON
    construction: Construction
    stored_bytes: int
    acceptance: str = 'accepted_pending_transport'



def _pack(identity, construction):
    facts = []
    for fact in construction.facts:
        channels = {}
        for name in ('delivery', 'sql', 'diagnostic', 'metadata'):
            value = getattr(fact, name)
            channels[name] = None if value is None else dict(
                canonical=value.canonical.decode('utf-8'), sha256=value.digest)
        facts.append(dict(row_kind=fact.row_kind, **channels,
                          issues=list(fact.issues), retry=fact.retry,
                          decision='row' if fact.ready else 'terminal_omission'))
    return canonical_json_bytes(dict(format='db1-local-outbox-v1',
                                    acceptance='accepted_pending_transport',
                                    identity=identity.value(), facts=facts))

