from __future__ import annotations

import base64
import logging
import os

import cv2
from langchain_openai import ChatOpenAI

from live_feed.review_agent import config as cfg
from live_feed.review_agent.schemas import (
    PlateReview,
    ReviewInput,
    VehicleReview,
)

log = logging.getLogger(__name__)

_VEHICLE_PROMPT = """\
You are a vehicle analysis assistant reviewing a CCTV crop.

The model predicted:
  class  : {cls}
  colour : {colour}

Look at the image and:
1. Confirm the class if correct, correct it if wrong.
   Valid classes: car, truck, bus, van, motorcycle, bicycle, person, unknown.
2. Confirm the colour if correct, correct it if wrong.
   Use a common colour name (white, black, silver, grey, red, blue, green, yellow, orange, brown, beige).
3. Identify the vehicle MAKER/brand (e.g. Kia, BMW, Renault, Toyota).
   Return 'unknown' if you cannot determine it with reasonable confidence.
   If the subject is a person or bicycle, no maker applies — return 'none'.
4. Return your overall confidence (0.0 – 1.0).
"""

_PLATE_PROMPT = """\
You are a licence-plate analysis assistant reviewing a CCTV crop of a vehicle plate.

The model predicted:
  plate digits : {ocr}
  plate type   : {plate_type}

Look at the image and:
1. Confirm the OCR text if correct, correct it if wrong. Return '' if the plate is unreadable.
2. Set plate_type to EXACTLY 'tounes' if the plate contains the Arabic word تونس, otherwise 'other'.
   Do NOT use any other value.
3. Set observed_category to the REAL nature of the plate for our database.
   Choose from: private / diplomatic / military / RS / taxi / transport / foreign / unknown.
   This is metadata only — it does NOT change plate_type.
4. Return your overall confidence (0.0 – 1.0).
"""


def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg.OPENROUTER_MODEL,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        default_headers={
            "HTTP-Referer": os.environ.get("YOUR_SITE_URL", "http://localhost"),
            "X-Title":      os.environ.get("YOUR_SITE_NAME", "vehicle_services"),
        },
        temperature=0,
        timeout=60,
        max_retries=2,
    )


def _data_url(path: str) -> str:
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {path}")
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        raise RuntimeError(f"Failed to encode image: {path}")
    b64 = base64.b64encode(buf).decode()
    return f"data:image/jpeg;base64,{b64}"


def review_vehicle(inp: ReviewInput) -> VehicleReview:
    llm = build_llm()
    chain = llm.with_structured_output(VehicleReview)
    msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": _VEHICLE_PROMPT.format(
                    cls=inp.vehicle_class_predicted,
                    colour=inp.colour_predicted,
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": _data_url(inp.vehicle_crop_path)},
            },
        ],
    }
    result = chain.invoke([msg])
    log.debug("vehicle review record_id=%s: %s", inp.record_id, result)
    return result


def review_plate(inp: ReviewInput) -> PlateReview | None:
    if not inp.plate_crop_path:
        return None
    llm = build_llm()
    chain = llm.with_structured_output(PlateReview)
    msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": _PLATE_PROMPT.format(
                    ocr=inp.ocr_predicted or "",
                    plate_type=inp.plate_type_predicted or "unknown",
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": _data_url(inp.plate_crop_path)},
            },
        ],
    }
    result = chain.invoke([msg])
    log.debug("plate review record_id=%s: %s", inp.record_id, result)
    return result
