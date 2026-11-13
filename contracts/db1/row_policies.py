"""Reusable pure P1 row admission/equality policies; no fixture or runtime imports."""
from typing import Any
from decimal import Decimal

try:
    from .reference import equivalent_instant, derive_date_key, parse_timestamp, normalize_plate_text
except ImportError:
    from reference import equivalent_instant, derive_date_key, parse_timestamp, normalize_plate_text

def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def same_number(left: Any, right: Any) -> bool:
    try:
        if left is None or right is None:
            return left is right
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def validate_model(delivery_model: dict[str, Any], sql_key: int, lookup: dict[str, Any], allowed_tasks: set[str]) -> None:
    model = lookup.get("model")
    require(isinstance(model, dict), "model resolution is missing")
    for field in ("model_name", "version", "task"):
        require(delivery_model[field] == model[field], f"model resolution differs for {field}")
    require(sql_key == model["model_key"], "model resolution key differs from SQL row")
    require(delivery_model["task"] in allowed_tasks, "model task is incompatible with selected route")


def validate_media(
    media: dict[str, Any] | None,
    sql_key: int | None,
    lookup: dict[str, Any],
    delivery: dict[str, Any],
    expected_kind: str,
    *,
    require_same_ts: bool,
) -> None:
    resolved = lookup.get("media")
    if media is None:
        require(sql_key is None and resolved is None, "null media does not match media resolution")
        return
    require(isinstance(resolved, dict), "media resolution is missing")
    require(sql_key == resolved["media_key"], "media key differs from resolution")
    require(
        media["org_id"] == delivery["org_id"]
        and media["run_id"] == delivery["run_id"]
        and media["camera_id"] == delivery["camera_id"],
        "media context does not match row organization/run/camera",
    )
    require(media["kind"] == expected_kind, f"media kind must be {expected_kind}")
    if require_same_ts:
        require(equivalent_instant(media["ts"], delivery["ts"]), "selected media timestamp differs from row timestamp")
    for field in ("org_id", "run_id", "camera_id", "kind", "bucket", "object_key", "sha256"):
        require(media[field] == resolved[field], f"media resolution differs for {field}")
    require("://" not in media["bucket"] and "://" not in media["object_key"], "media object identity must not be a URL")
    require(not media["object_key"].startswith("/") and ".." not in media["object_key"].split("/"), "media object identity must not be a local/relative path")


def validate_common(snapshot: dict[str, Any], lookup: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    delivery, sql = snapshot["delivery_payload"], snapshot["resolved_sql_row"]
    run = lookup["run"]
    require(delivery["run_id"] == run["run_id"], "run natural id differs from supplied resolution")
    require(delivery["org_id"] == run["org_id"], "organization differs from run resolution")
    require(delivery["camera_id"] == run["camera_id"], "camera natural id differs from run resolution")
    require(delivery["camera_timezone"] == run["timezone"], "camera timezone differs from run resolution")
    require(sql["run_key"] == run["run_key"] and sql["camera_key"] == run["camera_key"], "SQL run/camera keys differ from resolution")
    require(sql["org_id"] == delivery["org_id"], "SQL organization differs from delivery payload")
    require(sql["uid"].lower() == delivery["uid"].lower(), "SQL UID differs from delivery payload")
    require(sql["track_id"] == delivery["track_id"], "SQL track_id differs from delivery payload")
    require(equivalent_instant(sql["ts"], delivery["ts"]), "SQL timestamp differs from delivery payload")
    expected_date = derive_date_key(delivery["ts"], delivery["camera_timezone"])
    require(sql["date_key"] == expected_date, f"date_key {sql['date_key']} is not row-local date {expected_date}")
    require(sql["date_key"] in run["date_keys"], "date_key is not available in supplied date lookup")
    return delivery, sql


def validate_parent(sql: dict[str, Any], lookup: dict[str, Any]) -> None:
    parent = lookup.get("parent")
    require(isinstance(parent, dict), "recognition parent context is missing")
    require(
        sql["run_key"] == parent["run_key"]
        and sql["org_id"] == parent["org_id"]
        and sql["camera_key"] == parent["camera_key"]
        and sql["track_id"] == parent["track_id"],
        "recognition parent context does not match vehicle",
    )


def validate_snapshot_semantics(snapshot: dict[str, Any], lookup: dict[str, Any]) -> None:
    delivery, sql = validate_common(snapshot, lookup)
    kind = snapshot["row_kind"]
    if kind == "fact_vehicle":
        require(equivalent_instant(delivery["ts"], delivery["first_ts"]), "vehicle delivery ts must equal first_ts")
        require(equivalent_instant(sql["ts"], sql["first_ts"]), "vehicle ts must equal first_ts")
        require(equivalent_instant(sql["first_ts"], delivery["first_ts"]) and equivalent_instant(sql["last_ts"], delivery["last_ts"]), "vehicle sighting timestamps differ")
        require(parse_timestamp(delivery["last_ts"]) >= parse_timestamp(delivery["first_ts"]), "vehicle last_ts precedes first_ts")
        require(derive_date_key(delivery["ts"], delivery["camera_timezone"]) == sql["date_key"], "vehicle date_key mismatch")
        require(lookup["class"] == {**delivery["class"], "class_key": sql["class_key"]}, "class resolution differs from payload/SQL")
        require(delivery["class"]["class_group"] == "vehicle", "class_group must be vehicle")
        require(delivery["class_conf"] == sql["class_conf"] and delivery["frames_observed"] == sql["frames_observed"], "vehicle evidence differs from SQL row")
        validate_media(delivery["best_media"], sql["best_media_key"], lookup, delivery, "vehicle_crop", require_same_ts=False)
        return
    validate_parent(sql, lookup)
    if kind == "fact_plate_read":
        require(delivery["outcome"] != "no_plate", "no_plate is omitted under unchanged DB1 v0")
        require(delivery["outcome"] == sql["outcome"], "plate outcome differs from SQL row")
        attempt = delivery["selected_attempt"]
        require(isinstance(attempt, dict), "emitted plate row requires one selected attempt")
        require(equivalent_instant(delivery["ts"], attempt["ts"]) and equivalent_instant(sql["ts"], attempt["ts"]), "selected attempt timestamp is inconsistent")
        allowed = {"vlm"} if attempt["route"] == "agent" else {"ocr"}
        validate_model(attempt["model"], sql["model_key"], lookup, allowed)
        require(delivery["attempts"] == sql["attempts"] and delivery["attempts"] >= attempt["submission_ordinal"], "attempt count/ordinal is inconsistent")
        require(attempt["plate_text"] == sql["plate_text"], "selected attempt text differs from SQL row")
        require(attempt["conf"] == sql["conf"], "selected attempt confidence differs from SQL row")
        require(same_number(attempt["crop_score"], sql["crop_score"]), "selected attempt crop_score differs from SQL row")
        if delivery["outcome"] == "read":
            require(attempt["text_complete"] is True, "read requires text_complete=true; this remains only a necessary assertion")
            require(attempt["plate_text"] is not None and normalize_plate_text(attempt["plate_text"]) == attempt["plate_text"], "read plate text is not canonical and complete")
            if attempt["route"] == "local_private":
                require(attempt["structural_valid"] is True, "local_private read requires structural_valid=true")
        else:
            require(attempt["plate_text"] is None and attempt["plate_category"] is None and attempt["conf"] is None, "unreadable result fields must be null")
        category = attempt["plate_category"]
        if category is None:
            require(sql["plate_category_key"] is None and lookup.get("catalogue") is None, "null category must not resolve a key")
        else:
            catalogue = lookup.get("catalogue")
            require(isinstance(catalogue, dict) and catalogue.get("approved") is True, "plate category mapping is not approved")
            require(category == catalogue["name"] and sql["plate_category_key"] == catalogue["key"], "plate category resolution differs")
        validate_media(attempt["crop_media"], sql["crop_media_key"], lookup, delivery, "plate_crop", require_same_ts=True)
        return
    if kind == "fact_vehicle_colour":
        support = delivery["support"]
        require(equivalent_instant(delivery["ts"], support["ts"]), "colour support timestamp differs from row timestamp")
        allowed = {"vlm"} if support["source"] == "agent_override" else {"classify"}
        validate_model(delivery["model"], sql["model_key"], lookup, allowed)
        require(delivery["state"] == sql["state"] and delivery["samples"] == sql["samples"], "colour state/sample count differs")
        if delivery["state"] == "decided":
            require(delivery["colour"] is not None and delivery["conf"] is not None, "decided colour requires value and confidence")
            catalogue = lookup.get("catalogue")
            require(isinstance(catalogue, dict) and catalogue.get("approved") is True, "colour catalogue resolution is not approved")
            require(delivery["colour"] == catalogue["name"] and sql["colour_key"] == catalogue["key"], "catalogue resolution differs from grouped colour evidence")
            require(delivery["conf"] == sql["conf"], "colour confidence differs")
        else:
            require(delivery["colour"] is None and delivery["conf"] is None and sql["colour_key"] is None and sql["conf"] is None, "undecided/error colour fields must be null")
            require(lookup.get("catalogue") is None, "undecided/error colour must not resolve a catalogue member")
        return
    if kind == "fact_vehicle_make":
        validate_model(delivery["model"], sql["model_key"], lookup, {"classify", "vlm"})
        manufacturer = delivery["manufacturer"]
        if manufacturer is None:
            require(sql["manufacturer_key"] is None and delivery["manufacturer_conf"] is None, "null manufacturer fields are inconsistent")
            require(delivery["vehicle_model"] is None and sql["vehicle_model_key"] is None, "vehicle model cannot exist without manufacturer")
        else:
            resolved_maker = lookup.get("manufacturer")
            require(isinstance(resolved_maker, dict) and resolved_maker["name"] == manufacturer and resolved_maker["manufacturer_key"] == sql["manufacturer_key"], "manufacturer resolution differs")
        if delivery["vehicle_model"] is None:
            require(sql["vehicle_model_key"] is None and delivery["vehicle_model_conf"] is None, "null vehicle model fields are inconsistent")
        else:
            resolved_model = lookup.get("vehicle_model")
            require(isinstance(resolved_model, dict) and resolved_model["name"] == delivery["vehicle_model"] and resolved_model["vehicle_model_key"] == sql["vehicle_model_key"], "vehicle model resolution differs")
            require(resolved_model["manufacturer_key"] == sql["manufacturer_key"], "vehicle model manufacturer differs from direct manufacturer FK")
        require(delivery["manufacturer_conf"] == sql["manufacturer_conf"] and delivery["vehicle_model_conf"] == sql["vehicle_model_conf"] and delivery["observations"] == sql["observations"], "make evidence differs from SQL row")
        return
    raise ValueError(f"unknown row kind: {kind}")




def admit_native_plate_text(result: dict[str, Any]) -> str | None:
    """Shared producer's existing analytical completeness admission, pure.

    Model execution, original-parent ownership and crop eligibility are checked
    separately by the caller. An agent assertion remains a necessary claim.
    """
    if not isinstance(result, dict) or result.get('status') != 'accepted':
        return None
    evidence = result.get('evidence', {})
    if not isinstance(evidence, dict):
        return None
    if result.get('source') == 'agent':
        if result.get('route') != 'agent' or evidence.get('text_complete') is not True:
            return None
    elif result.get('source') == 'local_ocr':
        local = evidence.get('local', {})
        if result.get('route') != 'private_local' or not isinstance(local, dict) or local.get('parser_status') != 'valid':
            return None
    else:
        return None
    try:
        return normalize_plate_text(result.get('plate_text_normalized') or '')
    except ValueError:
        return None
