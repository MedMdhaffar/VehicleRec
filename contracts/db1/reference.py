"""Pure P1 reference helpers. No runtime, database, network or model dependencies."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    from .association import associate_plate
except ImportError:  # Direct validator script execution.
    from association import associate_plate


TIMESTAMP_KEYS = {"ts", "first_ts", "last_ts", "timestamp", "registered_at"}
UUID_KEYS = {"uid", "row_uid", "attempt_uid", "org_id"}


class StrictJSONError(ValueError):
    pass


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrictJSONError(f"nonfinite JSON number: {value}")


def strict_loads(text: str | bytes) -> Any:
    """Decode pure JSON evidence with the same exact/duplicate policy as P1 files."""
    try:
        return json.loads(text, object_pairs_hook=_pairs_no_duplicates,
                          parse_float=Decimal, parse_int=int, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise StrictJSONError(str(exc)) from exc


def strict_load(path: Path) -> Any:
    return strict_loads(path.read_text(encoding="utf-8"))


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise ValueError("timestamp requires explicit-offset RFC3339 with at most six fractional digits")
    value = value.upper()
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid RFC3339 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks UTC offset: {value}")
    return parsed


def canonical_timestamp(value: str) -> str:
    parsed = parse_timestamp(value).astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_number(value: int | float | Decimal) -> str:
    if isinstance(value, bool):
        raise TypeError("boolean is not a canonical number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite number")
        value = Decimal(repr(value))
    elif isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("nonfinite or unsupported number")
    # Exact tuple operations never consult the ambient decimal context.
    sign, digits, exponent = value.as_tuple()
    if len(digits) > 4096 or abs(exponent) > 100000:
        raise ValueError("canonical number exceeds 4096 digits or exponent limit 100000")
    if value == 0:
        return "0"
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    body = "".join(str(digit) for digit in digits)
    adjusted = exponent + len(body) - 1
    mantissa = body[0] + (("." + body[1:]) if len(body) > 1 else "")
    return ("-" if sign else "") + mantissa + (f"e{adjusted}" if adjusted else "")


def _canonical_json(value: Any, key: str | None = None) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float, Decimal)):
        return _canonical_number(value)
    if isinstance(value, str):
        if key in TIMESTAMP_KEYS:
            value = canonical_timestamp(value)
        elif key in UUID_KEYS:
            try:
                value = str(uuid.UUID(value))
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"invalid UUID for {key}: {value}") from exc
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(item_key, str) for item_key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return "{" + ",".join(
            json.dumps(item_key, ensure_ascii=False) + ":" + _canonical_json(value[item_key], item_key)
            for item_key in sorted(value)
        ) + "}"
    raise TypeError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_json(value).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def equivalent_instant(left: str, right: str) -> bool:
    return canonical_timestamp(left) == canonical_timestamp(right)


def derive_date_key(ts: str, camera_timezone: str) -> int:
    try:
        zone = ZoneInfo(camera_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown camera timezone: {camera_timezone}") from exc
    local_date = parse_timestamp(ts).astimezone(zone).date()
    return local_date.year * 10000 + local_date.month * 100 + local_date.day


def clock_discontinuity(
    previous_utc: str,
    previous_monotonic: float | Decimal,
    current_utc: str,
    current_monotonic: float | Decimal,
    *,
    tolerance_seconds: float | Decimal = 5,
) -> bool:
    """Reference paired-clock rule v0; five seconds is provisional service config."""
    previous_wall = parse_timestamp(previous_utc)
    current_wall = parse_timestamp(current_utc)
    previous_mono = float(previous_monotonic)
    current_mono = float(current_monotonic)
    tolerance = float(tolerance_seconds)
    if not all(math.isfinite(value) for value in (previous_mono, current_mono, tolerance)) or tolerance < 0:
        raise ValueError("clock inputs and tolerance must be finite; tolerance must be nonnegative")
    wall_delta = (current_wall - previous_wall).total_seconds()
    mono_delta = current_mono - previous_mono
    return wall_delta < 0 or mono_delta < 0 or abs(wall_delta - mono_delta) > tolerance


def normalize_plate_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("plate text must be a string")
    normalized = unicodedata.normalize("NFC", value)
    output: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if char == "\ufffd" or (category in {"Cc", "Cf"} and not char.isspace()):
            raise ValueError("plate text contains a prohibited control or replacement character")
        if category == "Nd":
            output.append(str(unicodedata.decimal(char)))
        elif "a" <= char <= "z":
            output.append(char.upper())
        else:
            output.append(char)
    collapsed = " ".join("".join(output).split())
    if not collapsed or any(token in collapsed for token in ("?", "�", "[UNREADABLE]")):
        raise ValueError("plate text is empty or contains an unreadable placeholder")
    return collapsed


def allocate_track_ids(lifetimes: list[dict[str, Any]], *, start: int = 1) -> list[int]:
    """Allocate one non-reused positive BIGINT per distinct run-local lifetime."""
    if start < 1 or start > 9223372036854775807:
        raise ValueError("track allocator start is outside positive BIGINT range")
    seen: set[tuple[int, int, int]] = set()
    allocated: list[int] = []
    next_id = start
    for lifetime in lifetimes:
        identity = (int(lifetime["epoch"]), int(lifetime["raw_track_id"]), int(lifetime["lifetime_sequence"]))
        if identity in seen:
            raise ValueError(f"duplicate raw lifetime identity: {identity}")
        if next_id > 9223372036854775807:
            raise ValueError("track allocator exhausted positive BIGINT range")
        seen.add(identity)
        allocated.append(next_id)
        next_id += 1
    return allocated


def plate_attempt_rank(item, *, coerce_quality=True):
    """Canonical quality/null/ordinal/native-ID ordering for crop selection."""
    quality = item['crop_score']
    quality = float(quality) if quality is not None and coerce_quality else quality
    return (quality is None, -quality if quality is not None else 0,
            int(item['submission_ordinal']), str(item['attempt_id']))


def select_unreadable_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Select one internally consistent attempted crop; never borrow route/model evidence."""
    eligible: list[dict[str, Any]] = []
    for attempt in attempts:
        model = attempt.get("executed_model")
        score = attempt.get("crop_score")
        try:
            numeric_score = None if score is None else float(score)
            timestamp = attempt.get("ts")
            parse_timestamp(timestamp)
        except (TypeError, ValueError):
            continue
        if (
            attempt.get("submitted") is True
            and attempt.get("associated") is True
            and attempt.get("crop_accepted") is True
            and isinstance(model, dict)
            and model.get("model_name")
            and model.get("version")
            and model.get("task")
            and (numeric_score is None or (math.isfinite(numeric_score) and numeric_score >= 0))
        ):
            eligible.append(attempt)
    if not eligible:
        return {"selected_attempt_id": None, "omission_reason": "failure_before_model"}
    selected = min(eligible, key=plate_attempt_rank)
    return {"selected_attempt_id": selected["attempt_id"], "omission_reason": None}


def classify_plate_omission(state: dict[str, Any]) -> str | None:
    capability = state.get("capability")
    if capability == "unsupported":
        return "unsupported_capability"
    if capability == "disabled":
        return "disabled_capability"
    association = state.get("association")
    if association == "ambiguous":
        return "ambiguous_association"
    if association == "unassociated":
        return "unassociated_crop"
    detections = int(state.get("detections", 0))
    admitted_crops = int(state.get("admitted_crops", 0))
    models_executed = int(state.get("models_executed", 0))
    if detections == 0:
        return "no_detection"
    if admitted_crops == 0:
        return "rejected_crop"
    if models_executed == 0:
        return "failure_before_model"
    return None


def retry_comparison(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    natural_equal = payload_sha256(first["delivery_payload"]) == payload_sha256(second["delivery_payload"])
    sql_equal = payload_sha256(first["resolved_sql_row"]) == payload_sha256(second["resolved_sql_row"])
    return {
        "natural_equal": natural_equal,
        "sql_equal": sql_equal,
        "classification": "identical" if natural_equal and sql_equal else "conflict",
    }
