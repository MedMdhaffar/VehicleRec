"""Small, isolated OpenRouter vision adapter for exceptional plate crops."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

import run_video_pipeline.config as config
log = logging.getLogger(__name__)

AgentCategory = Literal["private", "temporary", "diplomatic", "military", "foreign", "other"]


class AgentPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_text: str
    normalized_text: str
    category: AgentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_summary: str
    text_complete: StrictBool = False


@dataclass(frozen=True)
class AgentOutcome:
    status: str
    prediction: AgentPrediction | None
    warning: str | None
    preparation_ms: float
    api_ms: float
    total_ms: float
    api_calls: int


Transport = Callable[[str, dict[str, str], dict, float], dict]


def normalize_agent_text(prediction: AgentPrediction) -> str:
    """Lightly canonicalize an explicit private marker without validating identity."""
    normalized = " ".join(prediction.normalized_text.split())
    visible = " ".join(prediction.visible_text.split())
    if prediction.category != "private":
        return normalized or visible
    marker = re.compile(
        r"^\s*([0-9]+)\s*(?:تونس|TUNIS(?:IA|IE)?|T)\s*([0-9]+)\s*$",
        re.IGNORECASE,
    )
    for candidate in (visible, normalized):
        match = marker.fullmatch(candidate)
        if match:
            return f"{match.group(1)} تونس {match.group(2)}"
    return normalized or visible


def _urllib_transport(url: str, headers: dict[str, str], payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class OpenRouterPlateAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        enabled: bool | None = None,
        timeout: float | None = None,
        retry_limit: int | None = None,
        minimum_confidence: float | None = None,
        transport: Transport | None = None,
    ):
        self.api_key = config.OPENROUTER_API_KEY if api_key is None else api_key
        self.model = model or config.OPENROUTER_MODEL
        self.enabled = config.OPENROUTER_ENABLED if enabled is None else enabled
        self.timeout = timeout if timeout is not None else config.OPENROUTER_TIMEOUT_SECONDS
        self.retry_limit = retry_limit if retry_limit is not None else config.OPENROUTER_RETRY_LIMIT
        self.minimum_confidence = minimum_confidence if minimum_confidence is not None else config.AGENT_MIN_CONFIDENCE
        self.transport = transport or _urllib_transport

    def _payload(self, image_b64: str) -> dict:
        instruction = (
            "Recognize this vehicle license plate by inspecting the image directly. Read the visible "
            "plate text and choose the single category that best matches all visible evidence, including "
            "text, script, symbols, layout, colors, country markings, and plate style. Categories: "
            "private = standard Tunisian private/civilian plate; temporary = Tunisian temporary/transit "
            "plate; diplomatic = diplomatic or consular plate; military = military, police, security, "
            "or similar official/security plate; foreign = a plate belonging to another country, even "
            "on a civilian vehicle; other = a recognizable plate outside those categories. Use your own "
            "visual reasoning, do not default to private, and do not follow a fixed decision order. If "
            "uncertain, choose the best supported category, usually other. Preserve the text and symbols "
            "you can actually read without inventing characters. Return normalized_text, confidence "
            "from 0 to 1, and a short evidence_summary as strict structured JSON only."
            " Set text_complete=true only when the entire plate inscription is readable;"
            " partial or guessed characters require text_complete=false."
        )
        schema = AgentPrediction.model_json_schema()
        # Require the claim in new structured replies; legacy parsing defaults false.
        schema["required"] = list(schema["properties"])
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "plate_result", "strict": True, "schema": schema}},
            "temperature": 0,
        }

    @staticmethod
    def _parse_response(response: dict) -> AgentPrediction:
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("agent response content is not a JSON string")
        prediction = AgentPrediction.model_validate(json.loads(content))
        return prediction

    def analyze(self, crop: np.ndarray, *, on_execution=None) -> AgentOutcome:
        total_started = time.perf_counter()
        if not self.enabled:
            return AgentOutcome("unavailable", None, "OpenRouter agent is disabled", 0.0, 0.0, 0.0, 0)
        if not self.api_key:
            return AgentOutcome("unavailable", None, "OPENROUTER_API_KEY is missing", 0.0, 0.0, 0.0, 0)
        prep_started = time.perf_counter()
        ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return AgentOutcome("error", None, "plate crop JPEG encoding failed", 0.0, 0.0, 0.0, 0)
        payload = self._payload(base64.b64encode(encoded.tobytes()).decode("ascii"))
        preparation_ms = (time.perf_counter() - prep_started) * 1000
        api_ms = 0.0
        calls = 0
        last_error = "unknown agent error"
        for _ in range(max(0, self.retry_limit) + 1):
            calls += 1
            started = time.perf_counter()
            try:
                if on_execution is not None:
                    on_execution()
                response = self.transport(
                    config.OPENROUTER_URL,
                    {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    payload,
                    self.timeout,
                )
                api_ms += (time.perf_counter() - started) * 1000
                prediction = self._parse_response(response)
                if prediction.confidence < self.minimum_confidence:
                    return AgentOutcome(
                        "rejected", prediction, "agent confidence below threshold", preparation_ms,
                        api_ms, (time.perf_counter() - total_started) * 1000, calls,
                    )
                return AgentOutcome(
                    "accepted", prediction, None, preparation_ms, api_ms,
                    (time.perf_counter() - total_started) * 1000, calls,
                )
            except (TimeoutError, urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
                api_ms += (time.perf_counter() - started) * 1000
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("OpenRouter plate agent attempt failed: %s", last_error)
        status = "unavailable" if "Timeout" in last_error or "URLError" in last_error else "error"
        return AgentOutcome(status, None, last_error, preparation_ms, api_ms, (time.perf_counter() - total_started) * 1000, calls)
