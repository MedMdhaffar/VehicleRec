# Opt-in Plate request protocol v2

This bounded, single live Engine session protocol leaves reviewed v0/v1 plans
unchanged. Pixels, executor bindings and native resources remain Engine-private.
Plate requests operation kinds; it cannot choose executors or supply code/paths.
Every envelope binds the trusted org, complete original lifetime and Engine session.

| Input / state | Transition | Output / prerequisite |
|---|---|---|
| `crop` / absent | Retain closed detection metadata and bounded capability/attempt offer | First immutable `request`, or exact empty `seal` |
| `admission` / requested | Retain explicit acceptance or rejection | Acceptance waits for evidence; rejection blocks terminal publication |
| `evidence` / requested | Retain original generation and operation scalar | Wait for acceptance if reordered; otherwise apply canonical Plate policy |
| settled whole OCR | Request marker needed by canonical eligibility | Exact whole evidence generation dependency |
| settled whole + marker | `needs_region_ocr` decides regional work | Exact whole/marker IDs and evidence hashes; either regional order supported |
| all policy work settled | Close request generation | `seal` includes exact requested/admitted identities and revision |
| `sealed` / generation closed | Verify exact admitted set, scalar transcript and retirement | Canonical v1 Plate terminal, then unchanged Aggregator v1 join |
| terminal handoff confirmed | Explicit host completion handshake | Engine may release crops after seal/evidence publication and native retirement |
| duplicate | Exact bytes / identity required | Reuse decision/evidence; never invoke again |
| missing dependency | Persist waiting state | Consume other inputs; no global wait |
| conflict / late changed evidence | Block and retain original terminal | No replacement terminal or fabricated no-detection |

Detailed execution, limits, ACK boundaries and validation evidence are recorded in
[current restart results](vehicle_restart_results.md); the original execution
record is `vehicle_plate_requests_results.md`. Detection closure, generation closure,
admission, execution settlement, Plate terminal and final delivery are distinct.
Timeouts establish none of cancellation, retirement or semantic outcome.

## Binding and seals

`contracts/vehicle_plate_requests_v2.schema.json` closes every envelope and nested
request field set. It copies the reviewed scalar definitions unchanged; v0/v1
schemas are unchanged. Protocol is `vehicle-plate-requests-v2`, policy is
`plate-local-v1`. Full original closed lifetime, org/run/camera/raw tracker/epoch/
sequence, candidate, attempt ID/ordinal, Engine session UUID and batch UUID bind
requests. Request sequence is lifetime-wide and independent of crop ordinal.
UUID5 request IDs include the complete parent, session, attempt, crop and sequence;
operation/dependency changes under that ID conflict. Dependencies name exact
operation IDs and SHA256 generations of original scalar evidence. Marker requests
bind whole OCR; regional requests bind the exact successful whole and marker.
No string field selects a model, callable, import, URL, credential or crop path.

Engine accepts only explicitly configured operation→executor bindings. Acceptance
is published before invocation is enabled. Rejections distinguish unsupported
capability, capacity, binding, unavailable session/crop, dependency and closed
generation. Rejection blocks this bounded lifetime's terminal, rather than
fabricating an execution or omission. Unknown native results remain waiting.

The Plate seal includes sorted exact request IDs and revision; Engine requires
equality with its admitted set, no rejection, all original evidence and confirmed
native retirement. Plate separately compares its independently reconstructed
transcript with Engine's sealed transcript before publishing an ordinary v1
terminal. V1 expected-operation sets therefore describe only actual admitted
operations. No unrequested region appears as completed or not-started work.
Aggregator's unchanged v1 join then authenticates Plate and Colour and constructs
one canonical natural bundle, with Manufacturer unsupported.

## Streams and acknowledgments

All suffixes use `<namespace>:{vehicle-v1.<trusted-org-uuid>}:`. A fresh namespace
is required per bounded host ownership scope; org is identity, not authorization.

| Suffix | Producer | Group | ACK boundary |
|---|---|---|---|
| `engine.crops.v2` | Engine | `plate-requests-v2` | Fenced state + immutable request/seal intent/output |
| `plate.requests.v2` | Plate | `engine-requests-v2` | Confirmed admission publication + retained live ledger |
| `engine.admissions.v2` | Engine | `plate-requests-v2` | Persist admission/wait and any resulting output |
| `engine.executions.v2` | Engine | `plate-requests-v2` | Persist original scalar generation and next decision |
| `plate.seals.v2` | Plate | `engine-requests-v2` | Confirmed exact Engine sealed-response publication |
| `engine.sealed.v2` | Engine | `plate-requests-v2` | Exact set/transcript validation + terminal handoff |
| `engine.lifecycle.plate`, `results.colour`, `results.plate` | Existing named components | `aggregator-plate-v1` | Existing fenced join/state/output journal |
| `engine.evidence.colour` | Engine | `colour-v1` | Existing Colour state/output journal |
| `db1.delivery.v1` | Aggregator | `db1-natural-v1` | Confirmed PostgreSQL commit, including identical-history reconciliation |

Metadata workers retain dependency waits before ACK, repair pending intents before
later work, and retain exact output bytes/monotonic IDs under the existing fence.
Changed retries produce diagnostics and a sticky block for unfinished work. Late
conflict after terminal retains that original terminal and a conflict hash; it
cannot replace it or retract already committed facts. Engine publication uses a
single bounded FIFO across all output streams, stable increasing IDs and retained
per-input progress. Physical duplicates reuse the original decision/evidence.

Engine request ACK relies on **live process memory**, not Redis inference
idempotence. Model execution is outside Redis transactions. Process death between
admission, native start, result and publication leaves an uncertainty window;
restart cannot reconstruct crops, resume an old session or prove exactly-once
inference. An active new session rejects old-session requests. Routing an
unavailable lifetime fails with a retained dependency wait; it does not re-decode.

## Completion and resources

Detection closes before the offered metadata is published. Plate remains able to
request followups until its generation seal. Engine continues pumping requests,
seals and native progress while metadata processes consume admissions/evidence.
There is no synchronous wait inside the reducer or one global dependency wait.
Native calls remain serialized through the reviewed process-wide lane. Return
without retirement publishes no settled evidence and pins both lane and crop.

After Engine's sealed response, `completion.observe_completion` performs one
read-only Lua observation in the trusted org partition. It requires no pending
journal intent, the exact seen input digest, original physical sealed input bytes,
the group's last-delivered cursor at or beyond that input, and no PEL entry for
that physical ID. It then reads terminal state and the single matching terminal
in that same observation. Python validates the full original lifetime/session/
sealed body and the terminal provenance digest. PREPARE's seen marker and proposed
state alone never establish completion. Before PREPARE, between PREPARE and append,
or after append before FINISH/XACK the host returns waiting and retains crops.
A completed but conflicting input/output blocks; it is not a missing-terminal
error during ordinary progress. The group cursor also prevents a later unread
physical duplicate from masquerading as an acknowledged input.

FINISH atomically validates the fence, XACKs the input and removes its pending
intent. A lost FINISH reply is reconciled by the same observation; no additional
receipt schema or weaker lease was introduced.
Only this explicit completion handshake confirms the progressive adapter and
allows retryable crop release. Required scalar handoffs and native retirement
are therefore prerequisites. Timeout/stop does not manufacture that handshake;
unresolved handles remain explicitly retained. Existing local runtime cleanup
quarantine/retry behavior is unchanged.

Limits: 8 decoded frames/300 admission seconds maximum (default 4/180); 16
lifetimes and total crop attempts, 2 selected segments per lifetime, 16 candidates
per lifetime, 16 handles/32 MiB total private crops, 8 MiB per crop, 80 actual API
calls maximum. Each v2 offer has at most 8 attempts, 32 operations, 6 MiB wire
bytes. The host retains at most 256 publication entries/8 MiB scalar packets,
128 input progress entries, and 64 admission/rejection decisions per lifetime.
The metadata journal retains at most 256 input events and stream entries by
default, with the reviewed 20 MiB state and 24 MiB + 4096-byte intent profiles and 60-second live lease.
No eviction/trimming is used. Capacity is explicit backpressure or rejection.
Streams/PEL and live operations are polled one entry at a time with fair source
and pending/new alternation; idle workers back off 50 ms and pending host turns
back off 10 ms. Native APIs are not interruptible deadlines.


## Startup and broker restart boundary (2026-11-07)

`V2Publisher` calls canonical `Journal.require_org(codec.org)` before any Redis
call, Engine access or queue allocation. Engine's configured context must also
match the trusted org; prepared lifetimes must equal its original run/camera
context. `OrderedPublisher` is a lower-level byte/FIFO helper: its caller supplies
the trusted partition. It is not an independent tenant authorization layer.

The host atomically claims an empty bounded namespace with its full org/run/
camera/session owner value before group creation. Nonempty state or an existing
owner is refused; there is no reset/rebuild path. Metadata workers may already
have created empty streams and lease/fence keys. Every pump verifies the owner.
`reconnect()` is supported only on the same live publisher/Engine object, using
its original ledger, identities, queued bytes and crops. Missing/changed ownership
fails closed. The new bounded driver retries broker connection errors within its
settlement budget, continues fair request/seal/evidence pumping and does not
invoke completed native work again. The older real-model convenience CLI retains
its fail-closed connection-error behavior.

The historical `PrivateRuntime` remains ephemeral (`appendonly no`, no snapshot).
The separate `PersistentRuntime` uses a private socket/directory, AOF and verified
`appendfsync always`, no eviction and no automatic trimming/reset. Its deterministic
restart tests cover confirmed command boundaries, exact stream/group/journal
replay, live Engine continuation and metadata replacement. SIGKILL testing does
not establish host power-loss or filesystem/storage-controller guarantees.
After Engine death, preserve prior evidence and use an explicit new run/session
and fresh namespace for new capture. Old handles and uncertain native work cannot
be recovered or converted to unreadable/no_detection/success. Already accepted
natural deliveries remain independently replayable to the DB1 consumer.
