#!/usr/bin/env python3
"""Pure DB1 P1 fixture validator. Never imports runtime or database packages."""
from __future__ import annotations

import copy
import importlib.metadata
import math
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    print("LIMITATION: jsonschema is unavailable; metaschema and format validation were not run.")
    print("Install nothing automatically. Run with an existing interpreter that provides jsonschema.")
    raise SystemExit(2)

from reference import (
    StrictJSONError,
    allocate_track_ids,
    associate_plate,
    canonical_json_bytes,
    classify_plate_omission,
    clock_discontinuity,
    derive_date_key,
    equivalent_instant,
    normalize_plate_text,
    parse_timestamp,
    retry_comparison,
    select_unreadable_attempt,
    strict_load,
)


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        return
    FAIL += 1
    print(f"FAIL {name}: {detail}")


def all_error_text(error) -> str:
    parts = [error.message]
    for child in error.context:
        parts.append(all_error_text(child))
    return " | ".join(parts)


def schema_errors(validator: Draft202012Validator, instance: Any) -> list[str]:
    return [all_error_text(error) for error in validator.iter_errors(instance)]


def path_parts(path: str) -> list[str]:
    return path.split(".")


def mutate(case: dict[str, Any], operation: dict[str, Any]) -> None:
    parts = path_parts(operation["path"])
    target: Any = case
    for part in parts[:-1]:
        target = target[part]
    if operation["operation"] == "set":
        target[parts[-1]] = operation["value"]
    elif operation["operation"] == "remove":
        target.pop(parts[-1])
    else:
        raise ValueError(f"unknown mutation operation: {operation['operation']}")


from row_policies import validate_snapshot_semantics as _validate_snapshot_semantics


def validate_snapshot_semantics(snapshot, lookup):
    if lookup.get("synthetic") is not True:
        raise ValueError("fixture lookup keys must be explicitly synthetic")
    _validate_snapshot_semantics(snapshot, lookup)


def load_valid_rows() -> dict[str, dict[str, Any]]:
    suite = strict_load(FIXTURES / "valid_rows.json")
    return {case["id"]: case for case in suite["cases"]}


def run() -> int:
    try:
        version = importlib.metadata.version("jsonschema")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    print(f"jsonschema {version}; Draft 2020-12 FormatChecker enabled")

    row_schema = strict_load(ROOT / "rows.v0.schema.json")
    diagnostic_schema = strict_load(ROOT / "diagnostics.v1.schema.json")
    for name, schema in (("rows", row_schema), ("diagnostics", diagnostic_schema)):
        try:
            Draft202012Validator.check_schema(schema)
            check(f"{name} metaschema", True)
        except Exception as exc:
            check(f"{name} metaschema", False, str(exc))
    formats = FormatChecker()
    @formats.checks("date-time", raises=ValueError)
    def supported_timestamp(value):
        if not isinstance(value, str):
            return True
        parse_timestamp(value)
        return True
    row_validator = Draft202012Validator(row_schema, format_checker=formats)
    diagnostic_validator = Draft202012Validator(diagnostic_schema, format_checker=formats)

    for manifest_name in ("catalogue_requirements.v0.json", "model_registrations.v0.json"):
        manifest = strict_load(ROOT / manifest_name)
        check(f"manifest {manifest_name}", manifest.get("fixture_keys_are_synthetic") is True and manifest.get("status", "").startswith("review_candidate"), "manifest must identify synthetic keys and review status")

    valid = load_valid_rows()
    for case_id, case in valid.items():
        errors = schema_errors(row_validator, case["snapshot"])
        if errors:
            check(f"valid row {case_id}", False, " | ".join(errors))
            continue
        try:
            validate_snapshot_semantics(case["snapshot"], case["synthetic_lookup"])
            canonical_json_bytes(case["snapshot"]["delivery_payload"])
            canonical_json_bytes(case["snapshot"]["resolved_sql_row"])
            check(f"valid row {case_id}", True)
        except Exception as exc:
            check(f"valid row {case_id}", False, str(exc))

    invalid_suite = strict_load(FIXTURES / "invalid_rows.json")
    for spec in invalid_suite["cases"]:
        case = copy.deepcopy(valid[spec["base_case"]])
        operations = spec.get("operations") or [spec]
        for operation in operations:
            mutate(case, operation)
        errors = schema_errors(row_validator, case["snapshot"])
        if spec["layer"] == "schema":
            combined = " | ".join(errors)
            check(f"invalid row {spec['id']}", bool(errors) and spec["contains"].lower() in combined.lower(), combined)
            continue
        if errors:
            check(f"invalid row {spec['id']}", False, "unexpected schema rejection: " + " | ".join(errors))
            continue
        try:
            validate_snapshot_semantics(case["snapshot"], case["synthetic_lookup"])
            check(f"invalid row {spec['id']}", False, "semantic validator accepted case")
        except Exception as exc:
            check(f"invalid row {spec['id']}", spec["contains"].lower() in str(exc).lower(), str(exc))

    valid_diag_suite = strict_load(FIXTURES / "valid_diagnostics.json")
    valid_diagnostics = {case["id"]: case for case in valid_diag_suite["cases"]}
    for case_id, case in valid_diagnostics.items():
        errors = schema_errors(diagnostic_validator, case["diagnostic"])
        check(f"valid diagnostic {case_id}", not errors, " | ".join(errors))
    invalid_diag_suite = strict_load(FIXTURES / "invalid_diagnostics.json")
    for spec in invalid_diag_suite["cases"]:
        case = copy.deepcopy(valid_diagnostics[spec["base_case"]])
        mutate(case, spec)
        errors = schema_errors(diagnostic_validator, case["diagnostic"])
        combined = " | ".join(errors)
        check(f"invalid diagnostic {spec['id']}", bool(errors) and spec["contains"].lower() in combined.lower(), combined)

    construction_cases = strict_load(FIXTURES / "construction_diagnostics.v1.json")
    for case in construction_cases["cases"]:
        errors = schema_errors(diagnostic_validator, case["diagnostic"])
        check(f"construction diagnostic {case['id']}", (not errors) == case["valid"], " | ".join(errors))

    semantics = strict_load(FIXTURES / "semantic_cases.json")
    for case in semantics["canonical_numbers"]:
        try:
            with localcontext() as ctx:
                ctx.prec = case.get("precision", 28)
                result = canonical_json_bytes(Decimal(case["left"]))
                if "right" in case:
                    check(case["id"], (result == canonical_json_bytes(Decimal(case["right"]))) == case["equal"])
                else:
                    check(case["id"], "expected_error" not in case and len(result) <= 4108)
        except ValueError as exc:
            check(case["id"], case.get("expected_error", "MISSING") in str(exc), str(exc))
    for case in semantics["timestamp_precision"]:
        try:
            result = equivalent_instant(case["left"], case.get("right", case["left"]))
            check(case["id"], "expected_error" not in case and result)
        except ValueError as exc:
            check(case["id"], case.get("expected_error", "MISSING") in str(exc), str(exc))
    for case in semantics["identity"]:
        try:
            result = allocate_track_ids(case["lifetimes"], start=int(case.get("start", 1)))
            check(f"identity {case['id']}", "expected_track_ids" in case and result == case["expected_track_ids"], str(result))
        except Exception as exc:
            check(f"identity {case['id']}", case.get("expected_error", "").lower() in str(exc).lower(), str(exc))
    for case in semantics["association"]:
        kwargs = {name: float(case[name]) for name in ("minimum_margin",) if name in case}
        result = associate_plate(case["plate_bbox"], case["vehicles"], **kwargs)
        check(f"association {case['id']}", result["status"] == case["expected_status"] and result["selected_track_id"] == case["expected_track_id"], str(result))
    for case in semantics["plate_selection"]:
        result = select_unreadable_attempt(case["attempts"])
        check(f"plate selection {case['id']}", result["selected_attempt_id"] == case["expected_attempt_id"] and result["omission_reason"] == case["expected_omission"], str(result))
    for case in semantics["time"]:
        if "expected_date_key" in case:
            result = derive_date_key(case["ts"], case["timezone"])
            check(f"time {case['id']}", result == case["expected_date_key"], str(result))
        else:
            result = equivalent_instant(case["left"], case["right"])
            check(f"time {case['id']}", result is case["expected_equivalent"], str(result))
    for case in semantics["clock"]:
        result = clock_discontinuity(
            case["previous_utc"], case["previous_monotonic"],
            case["current_utc"], case["current_monotonic"],
        )
        check(f"clock {case['id']}", result is case["expected_discontinuity"], str(result))
    for case in semantics["retry"]:
        first = copy.deepcopy(valid[case["base_case"]]["snapshot"])
        second = copy.deepcopy(first)
        variant = case["variant"]
        if variant == "reorder_equivalent":
            second = {key: second[key] for key in reversed(list(second))}
            second["delivery_payload"]["attempts"] = Decimal("2.0")
            second["delivery_payload"]["ts"] = "2026-10-09T00:30:00+01:00"
            second["resolved_sql_row"]["date_key"] = Decimal("20261009.0")
        elif variant == "remove_explicit_null":
            second["delivery_payload"]["selected_attempt"].pop("conf")
        elif variant == "change_natural_score":
            second["delivery_payload"]["selected_attempt"]["crop_score"] = Decimal("1250.6")
        elif variant == "change_sql_model_key":
            second["resolved_sql_row"]["model_key"] = 5999
        result = retry_comparison(first, second)
        expected = (case["expected_natural_equal"], case["expected_sql_equal"], case["expected_classification"])
        actual = (result["natural_equal"], result["sql_equal"], result["classification"])
        check(f"retry {case['id']}", actual == expected, str(result))
    for case in semantics["normalization"]:
        try:
            result = normalize_plate_text(case["input"])
            check(f"normalization {case['id']}", "expected" in case and result == case["expected"], result)
        except Exception as exc:
            check(f"normalization {case['id']}", case.get("expected_error", "").lower() in str(exc).lower(), str(exc))
    for case in semantics["omission"]:
        result = classify_plate_omission(case["state"])
        check(f"omission {case['id']}", result == case["expected"], str(result))

    for path in sorted((FIXTURES / "rejected_json").glob("*.json")):
        try:
            strict_load(path)
            check(f"strict JSON {path.name}", False, "decoder accepted malformed JSON")
        except StrictJSONError:
            check(f"strict JSON {path.name}", True)

    print(f"\n{PASS} passed, {FAIL} failed")
    print(
        "EXECUTED: 2 metaschemas; 2 manifests; "
        f"{len(valid)} valid rows; {len(invalid_suite['cases'])} invalid rows; "
        f"{len(valid_diagnostics)} valid diagnostics; {len(invalid_diag_suite['cases'])} invalid diagnostics; "
        f"{len(semantics['canonical_numbers'])} canonical-number; {len(semantics['timestamp_precision'])} timestamp-precision; "
        f"{len(semantics['identity'])} identity; {len(semantics['association'])} association; {len(semantics['plate_selection'])} plate-selection; "
        f"{len(semantics['time'])} time; {len(semantics['clock'])} clock; {len(semantics['retry'])} retry; "
        f"{len(semantics['normalization'])} normalization; {len(semantics['omission'])} omission; 2 strict-JSON rejection cases; 2 construction diagnostics."
    )
    print("LIMITATIONS: fixtures are synthetic; no runtime evidence, database, object store, model, network, persisted/concurrent deduplication, authorization, or arbitrary-secret detection was tested.")
    print("CONTRACT STATUS: review candidate only; fixture success does not senior-validate or freeze T1.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(run())
