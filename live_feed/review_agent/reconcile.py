"""reconcile.py — compare LLM review against model predictions → routing decision."""
from __future__ import annotations

import time

from live_feed.review_agent import config as cfg
from live_feed.review_agent.schemas import (
    PlateReview,
    ReviewInput,
    ReviewedRecord,
    VehicleReview,
)


def _str_match(a: str | None, b: str | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.strip().lower() == b.strip().lower()


def reconcile(
    inp: ReviewInput,
    veh_rev: VehicleReview,
    plate_rev: PlateReview | None,
) -> ReviewedRecord:
    agreement: dict[str, bool] = {
        "class":      _str_match(inp.vehicle_class_predicted, veh_rev.vehicle_class),
        "colour":     _str_match(inp.colour_predicted,        veh_rev.colour),
        "ocr":        _str_match(inp.ocr_predicted,           plate_rev.ocr if plate_rev else None),
        "plate_type": _str_match(inp.plate_type_predicted,    plate_rev.plate_type if plate_rev else None),
    }

    all_agree = all(agreement.values())
    high_conf = veh_rev.confidence >= cfg.AUTO_ACCEPT_CONF
    plate_conf_ok = (plate_rev is None) or (plate_rev.confidence >= cfg.AUTO_ACCEPT_CONF)

    routed_to = "trusted" if (all_agree and high_conf and plate_conf_ok) else "review_queue"

    return ReviewedRecord(
        record_id=inp.record_id,
        channel=inp.channel,
        track_id=inp.track_id,
        vehicle=veh_rev,
        plate=plate_rev,
        predicted=inp,
        agreement=agreement,
        routed_to=routed_to,
        reviewed_at=time.time(),
    )
