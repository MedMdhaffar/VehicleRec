# Draft contract validation — 5 October 2026

> **DB1 reconciliation required (2026-10-08).** DB1 development contract v0 (2026-10-08) is the target database interface. This earlier draft is retained for producer evidence and proposal history; its tables, columns, vocabularies, cardinality and retry rules are not a DB1 specification. See [DB1 alignment](db1_alignment.md). No acceptance of unresolved semantics is claimed.

Executed from the current checkout with existing `jsonschema 4.26.0` in `/home/intern/.venv`. No package installation or application environment/dependency changes.

```text
/home/intern/.venv/bin/python contracts/validate_examples.py

Draft 2020-12 schema valid (jsonschema with FormatChecker)
{"application_reject": 16, "schema_reject": 24, "valid": 18}
Normalization vectors: 8 passed; canonical outputs idempotent
Retry equality fixtures: 3 passed; strict JSON rejection cases: 4 passed
Fixed vocabulary/schema drift checks: 3 passed
No producer, network, transport or database execution performed
```

The harness checks the Draft 2020-12 metaschema and enables format validation for timestamps/UUIDs (format is not automatically enforced by every validator). Strict JSON decoding rejects duplicate keys, NaN/Infinity and numeric overflow. Fixed vocabulary enums match labels manifest. Examples are synthetic, with synthetic artifact hashes and distinct run/observation identities; they are not output of the present unintegrated producer. Root expectations.json defines positive/negative cases and which layer should reject each negative fixture.

## Executed structural checks

18 positive envelopes pass schema and the harness's reference semantic subset: known private text/category/colour with unsupported manufacturer/model; vehicle without usable plate; unavailable capabilities; failed inference; unresolved text with known category; disabled category; agent colour override retaining local sample count; known text with disabled category; known values with null confidence and unmeasured counts. Nine added positives cover multiple crops with retries, crops that skip the agent, each individually unmeasured plate counter, known agent results with null confidence and measured/unmeasured calls, a single frame with equivalent UTC fractional spellings, and failed inference without a selected crop. The existing colour agent override passes with plate.agent_api_calls=0.

24 negative envelopes are rejected by JSON Schema: known null text; unresolved colour with score; failure without required error; missing manufacturer block; missing category state; unpaired score/metric; unsupported manufacturer with non-unknown value; disabled category reported known; type metric inconsistent with authority; deferred source_capture; invalid calendar date; mutable run total in envelope; score outside [0,1]; unresolved model with non-null value; oversized error code; attempted known recognition with measured zero attempts. Eight added negatives reject known agent text/category with measured zero plate-agent calls (even with null confidence), known text/category without selected crop, and whitespace-only input_source, service_name, model artifact_ref and component.

Schema also expresses shape, unknown fields, required nullable fields, integer/nonnegative bounds, state/value constraints for all five attributes, known model requires known manufacturer, source/metric compatibility, known text/category requires selected crop, known agent text/category permits only positive or null plate-agent call count, non-whitespace source/artifact references and component names, no crop/no quality or local latency, fixed initial time basis and private canonical shape. A known result may have both score/metric null. These are contract structural guarantees, not empirical producer correctness.

## Executed review-only semantic subset

16 fixtures deliberately pass JSON Schema but fail the harness's application-rule subset: crop outside sighting interval; reversed frame interval; frames_observed exceeds span; event_at differs from finalized_at; reversed host UTC; unregistered manufacturer; inconsistent agent retry counts; noncanonical whitespace in opaque plate text; local latency exceeds total interval. Seven added negatives cover calls-minus-retries exceeding submitted crops, no initial call despite positive calls, retries exceeding calls, single-frame unequal UTC instants/nonzero duration/multiple observed frames, and event_at differing in spelling from finalized_at despite representing the same instant. Each new semantic fixture asserts its specific rejection reason.

The subset additionally checks single-frame first_seen/last_seen equality as parsed UTC instants, zero monotonic duration and exactly one observed frame, UTC run/sighting/finalization ordering, canonical output idempotence, registry membership for manufacturer/model/metrics/errors, no agent calls with zero crop attempts, `0 <= agent_api_calls - agent_retries <= recognition_attempts` when all three are measured (positive difference for positive calls), pairwise retry constraints when only calls/retries are measured, and known local colour with zero retained samples. Eight normalization vectors exercise zeros, Unicode decimal digits, proposed private aliases, unique vs ambiguous markerless splitting, invalid explicit boundary, punctuation preservation and prohibited control characters. This is a spec-vector oracle, not a deployed normalizer/evaluator or OCR acceptance implementation.

Three in-memory equality fixtures exercise ignored JSON key order, changed score conflict and changed message ID conflict. They do **not** test persisted replay, concurrent first delivery, generation allocation, deduplication, transaction rollback or transport acknowledgements.

The baseline passed 9 positives, 16 schema negatives and 9 semantic negatives. The corrected full harness passes 18 positives, 24 schema negatives and 16 semantic negatives (58 envelope fixtures). All existing harness checks executed successfully with `/home/intern/.venv/bin/python`; none was skipped or unable to execute. No package or environment changes were needed. The five pending policies remain proposals: type authority, category admission/taxonomy, normalization, the 1.0-second clock tolerance/recovery policy, and provenance/count columns/manifests. No normalization or taxonomy approval, clock-tolerance acceptance or T1 freeze follows from these checks.

## Required future checks, not claimed executed

| Check | Responsibility / why schema alone is insufficient |
|---|---|
| Vehicle rather than plate observation; association correctness | Shared producer/tracker evidence, future integration. |
| Numeric tracker reuse/generation and UUID allocation across reset/retry/crash | Stateful producer and durable ingestion history. |
| Capability flags match effective executable configuration/readiness | Producer configuration/provenance artifact; code elsewhere is insufficient. |
| Timestamp captured at receipt before inference; duration from same monotonic epoch | Instrumented producer clock/frame history. |
| Clock discontinuity detection/quarantine | Paired receipt history and operational clock-step detection; schema checks only UTC shape. |
| Crop attempts/counts/agent retries truly measured; selected crop supports results | Producer attempt ledger, including failures/queue/rechecks. |
| Score supports exact type/colour/text/category/model and observation | Actual selected evidence and formula, not just metric enum. |
| Complete text/accepted category and proposed taxonomy | Validated admission evidence and reviewed format rules. |
| Stable immutable run_context/capabilities across observations in run | Stateful writer compare by run_id; fixture runs are independent. |
| Replay/source-tuple/message conflicts and atomic event+three-row persistence | Guarded writer, DB constraints and real transaction tests in later phases. |
| Model/manufacturer FKs, unknown member resolution and date dimension correctness | Future database implementation; no SQL/migrations executed. |
| Credential-free artifact/source references and correct loaded hashes/fingerprint | Deployment/source registry and producer provenance verification. |

The full proposed admission contract is the conjunction of schema, application rules, trusted evidence and future database constraints. Passing these fixtures does not mean T1 is frozen or a current producer can emit the envelope.
