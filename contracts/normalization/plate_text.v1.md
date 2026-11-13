# Plate-text normalization v1-draft.1

Working producer/evaluator specification; not implemented, frozen or accuracy validated. Canonical output is TEXT. Preserve raw evidence separately. Use this specification identically in producer and evaluator; the existing comparison helper is not a complete-plate normalizer.

## Existing behavior as evidence

`run_video_pipeline/ocr.py:normalize_plate_text` removes everything except ASCII alphanumerics and uppercases, losing Arabic markers/punctuation. `private_plate_parser.py:parse_private_plate` accepts explicit T/تونس with 1–3 ASCII series digits and 1–4 registration digits, or enumerates markerless splits and accepts only a unique split. Invalid marker structure cannot fall back to markerless parsing. `pipeline.py:assemble_plate_detection` can resolve ambiguous digits using reliable marker and left/right OCR only when sides are valid and concatenation agrees with whole OCR. `agent.py:normalize_agent_text` collapses whitespace, accepts T/TUNIS/TUNISIA/TUNISIE/تونس for private without length validation, and otherwise trusts model-normalized or visible text. These formats must not silently merge.

## Proposed common preprocessing

1. Input is an observed string, never integer. Reject controls (Unicode category Cc/Cf) except tab/CR/LF treated as whitespace; reject replacement characters and missing/unreadable-character placeholders when completeness cannot be established. Do not silently remove bidirectional/zero-width characters.
2. Apply Unicode NFC, not compatibility normalization. Convert Unicode decimal digits (category Nd with decimal value 0–9) to ASCII digits without numeric parsing; retain every leading zero. Superscripts, circled numerals and other numeric symbols are not decimal digits and must not be converted silently.
3. Collapse Unicode whitespace runs to one ASCII space, strip ends. Preserve punctuation/separators and meaningful symbols. Do not delete hyphens, dots, Arabic script or country/category markers globally.
4. ASCII a–z uppercase only; retain other scripts' case/forms. Mixed scripts, unusual punctuation and confusable letters require evidence; never convert O↔0 or I↔1.
5. Select an explicit profile from evidence independently of accepted category state. No universal digits_only fallback, no raw-text fallback to stored plate_text. Normalization is deterministic and idempotent; admission/completeness is a separate rule.

## private_tn_v1

Apply only to evidence that supports the Tunisian private plate format. Canonical output is `<1–3 ASCII series digits> تونس <1–4 ASCII registration digits>`, exactly one space around تونس. Fields remain strings.

Explicit T or تونس boundary matches current parser; propose adding TUNIS/TUNISIA/TUNISIE as full boundary tokens to reconcile agent behavior, after common preprocessing. Unlike current agent helper, enforce parser field lengths. Presence of an explicit marker with invalid fields is invalid, never stripped for markerless retry. An explicit marker establishes text boundary, not automatically a known private category.

Markerless digits: enumerate every split with series length 1–3 and registration length 1–4; accept only a unique split, matching parser behavior. Thus `1234567` -> `123 تونس 4567`, but `123456` is ambiguous. A reliable visual marker/split OCR may supply the boundary with side confidence and whole-digit compatibility checks in the existing pipeline; normalizing alone cannot invent it. Any canonical marker insertion must retain that parser/fusion provenance in diagnostics. Unknown detailed category does not preclude complete normalized text with a supported format.

Examples: `001T0007` -> `001 تونس 0007`; `٠٠١ تونس ٠٠٠٧` -> `001 تونس 0007`; ` 001 tunis 0007 ` -> same proposed canonical text. `1234T5678` is invalid, not a truncated plate. Do not join series/registration as an integer or remove تونس from analytical text.

## opaque_complete_v1

For complete visible text outside a resolved private format, common preprocessing is the only transformation. Preserve script, meaningful marker tokens and punctuation (e.g. `ab-٠٠١` -> `AB-001`). This is an opaque transcription, not proof of jurisdiction/category structure. Known text requires affirmative complete transcription evidence (every visible identity character resolved, no truncation, no invented characters) from the selected producer; current agent accepted status alone does not establish it. Category may be unknown or independently known. A known private category plus known text must satisfy private_tn_v1; incompatible evidence is not silently coerced. Retain conflicts diagnostically and resolve accepted values before publication.

Temporary, diplomatic, military/police/security, foreign and other formats lack an implemented dedicated parser in this checkout. Their exact prefixes, separator significance, length/field limits and jurisdiction distinctions are **unresolved**. Until supplied, never add inferred prefixes or apply private parser to those categories. An opaque complete transcription is allowed only with explicit completeness evidence; do not claim structural validation. Incomplete/ambiguous text becomes inferred_unknown with null plate_text. Missing usable input becomes not_attempted; attempted runtime failure becomes inference_error, not a normalization guess.

## Review vectors

See vectors.v1.json for deterministic transformations and rejected/ambiguous input. The contract validator checks canonical outputs and a restricted private parser boundary; it is not an OCR/evaluator implementation. Proposed Unicode/marker alias choices and opaque admission remain subject to review.
