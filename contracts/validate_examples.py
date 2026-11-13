"""Review-only contract/fixture validation; no producer, consumer or DB writes.

Run from any cwd with a Python environment containing jsonschema:
    /home/intern/.venv/bin/python contracts/validate_examples.py
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import math
from pathlib import Path
import re
import unicodedata

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


def strict_json(raw: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate property: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    data = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite number")
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)

    finite(data)
    return data


def load(path):
    return strict_json((ROOT / path).read_text())


def review_normalize(raw, profile):
    """Restricted spec-vector oracle, not a deployed producer/evaluator."""
    for char in raw:
        if unicodedata.category(char) in {"Cc", "Cf"} and char not in "\t\r\n":
            return None
    if "\ufffd" in raw:
        return None
    text = unicodedata.normalize("NFC", raw)
    text = "".join(str(unicodedata.decimal(c)) if unicodedata.category(c) == "Nd" else c for c in text)
    text = " ".join(text.split())
    text = "".join(c.upper() if "a" <= c <= "z" else c for c in text)
    if not text:
        return None
    if profile == "opaque_complete_v1":
        return text
    match = re.fullmatch(r"([0-9]{1,3})\s*(?:تونس|TUNIS(?:IA|IE)?|T)\s*([0-9]{1,4})", text)
    if match:
        return f"{match[1]} تونس {match[2]}"
    if "T" in text or "تونس" in text:
        return None
    if not re.fullmatch("[0-9]+", text):
        return None
    splits = [(text[:i], text[i:]) for i in range(1, len(text)) if 1 <= i <= 3 and 1 <= len(text) - i <= 4]
    return f"{splits[0][0]} تونس {splits[0][1]}" if len(splits) == 1 else None


def semantic_errors(data, labels, metrics):
    """Executable subset of application invariants against synthetic fixtures.

    Cannot prove actual capabilities, completeness, score evidence, clock
    history, identity stability, association or database consistency.
    """
    errors = []
    e, p, c, m = [data[k] for k in ("event", "plate", "colour", "manufacturer")]
    if e["last_frame"] < e["first_frame"]:
        errors.append("reversed frame interval")
    if e["frames_observed"] > e["last_frame"] - e["first_frame"] + 1:
        errors.append("contributing count exceeds span")
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    if e["first_frame"] == e["last_frame"]:
        if e["track_duration_seconds"] != 0:
            errors.append("single-frame duration must be zero")
        if e["frames_observed"] != 1:
            errors.append("single-frame observed count must be one")
        if parse(e["first_seen"]) != parse(e["last_seen"]):
            errors.append("single-frame receipt instants must match")
    if not parse(data["run_context"]["started_at"]) <= parse(e["first_seen"]) <= parse(e["last_seen"]) <= parse(e["finalized_at"]):
        errors.append("host UTC ordering")
    if data["event_at"] != e["finalized_at"]:
        errors.append("event_at must equal finalized_at")
    if p["selected_crop_frame"] is not None and not e["first_frame"] <= p["selected_crop_frame"] <= e["last_frame"]:
        errors.append("crop outside interval")
    if p["local_latency_ms"] is not None and p["total_recognition_latency_ms"] is not None and p["local_latency_ms"] > p["total_recognition_latency_ms"]:
        errors.append("local latency exceeds total resolution interval")
    calls, retries = p["agent_api_calls"], p["agent_retries"]
    if calls is not None and retries is not None and (retries > calls or (calls > 0 and retries >= calls) or (calls == 0 and retries != 0)):
        errors.append("invalid agent retry count")
    attempts = p["recognition_attempts"]
    if attempts is not None and calls is not None and retries is not None:
        # Retries are counted per crop: each initial call belongs to one
        # submitted crop, and some submitted crops never call the agent.
        initial_calls = calls - retries
        if not 0 <= initial_calls <= attempts or (calls > 0 and initial_calls == 0):
            errors.append("initial agent calls must fit submitted crop attempts")
    if p["recognition_attempts"] is not None and p["recognition_attempts"] == 0 and (calls not in (None, 0) or retries not in (None, 0)):
        errors.append("agent calls without submitted crop")
    if c["colour_state"] == "known" and c["selected_result_source"] == "local_temporal" and c["colour_samples"] == 0:
        errors.append("known local colour without retained samples")
    if m["manufacturer"] not in labels["manufacturers"]["values"]:
        errors.append("unregistered manufacturer")
    if m["model"] is not None and {"manufacturer": m["manufacturer"], "model": m["model"]} not in labels["vehicle_models"]["entries"]:
        errors.append("unregistered manufacturer/model pair")
    for block in (p, c, m):
        for field, value in block.items():
            if field.endswith("_metric") and value is not None and value not in metrics["metrics"]:
                errors.append("unregistered score metric")
        for field in ("error_code", "plate_category_error_code", "manufacturer_error_code", "model_error_code"):
            if block.get(field) is not None and block[field] not in labels["error_codes"]["proposed_values"]:
                errors.append("unregistered error code")
    if p["plate_text"] is not None and review_normalize(p["plate_text"], p["normalization_profile"]) != p["plate_text"]:
        errors.append("noncanonical plate text")
    return errors


def main():
    schema = load("vehicle_event_v1.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    labels, metrics = load("vocabularies/labels.v1.json"), load("vocabularies/metrics.v1.json")
    plan = load("examples/expectations.json")
    totals = {"valid": 0, "schema_reject": 0, "application_reject": 0}
    for path in plan["valid"]:
        data = load(path)
        validator.validate(data)
        assert not semantic_errors(data, labels, metrics), path
        totals["valid"] += 1
    for case in plan["invalid"]:
        data = load(case["path"])
        errors = list(validator.iter_errors(data))
        if case["expected"] == "schema_reject":
            assert errors, case["path"]
        else:
            assert not errors, (case["path"], [e.message for e in errors])
            semantic = semantic_errors(data, labels, metrics)
            assert semantic, case["path"]
            if "semantic_error" in case:
                assert case["semantic_error"] in semantic, (case["path"], semantic)
        totals[case["expected"]] += 1
    vectors = load("normalization/vectors.v1.json")["vectors"]
    for vector in vectors:
        output = review_normalize(vector["input"], vector["profile"])
        assert output == vector["output"], vector
        if output is not None:
            assert review_normalize(output, vector["profile"]) == output
    base = load(plan["valid"][0])
    assert strict_json(json.dumps(base, sort_keys=True)) == base  # reordered keys: semantic retry equality
    changed = copy.deepcopy(base)
    changed["plate"]["confidence"] = 0.95
    assert changed != base  # conflict, not a replacement
    changed = copy.deepcopy(base)
    changed["message_id"] = "00000000-0000-4000-8000-000000000999"
    assert changed != base  # same observation/new transport ID conflicts in v1
    for raw in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}']:
        try:
            strict_json(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(raw)
    # Manifest/schema drift: each known fixed vocabulary must have the same enums.
    defs = schema["$defs"]
    assert defs["event"]["properties"]["vehicle_type"]["enum"] == labels["vehicle_types"]["values"]
    assert defs["plate"]["properties"]["plate_category"]["enum"] == labels["plate_categories"]["values"]
    assert defs["colour"]["properties"]["colour"]["enum"] == labels["colours"]["values"]
    print("Draft 2020-12 schema valid (jsonschema with FormatChecker)")
    print(json.dumps(totals, sort_keys=True))
    print(f"Normalization vectors: {len(vectors)} passed; canonical outputs idempotent")
    print("Retry equality fixtures: 3 passed; strict JSON rejection cases: 4 passed")
    print("Fixed vocabulary/schema drift checks: 3 passed")
    print("No producer, network, transport or database execution performed")


if __name__ == "__main__":
    main()
