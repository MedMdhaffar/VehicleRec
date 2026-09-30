"""Deterministic parser for private Tunisian registration plates only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from run_video_pipeline.ocr import OCRResult, normalize_plate_text

ParserStatus = Literal["valid", "ambiguous", "invalid"]

_EXPLICIT_MARKER = re.compile(
    r"^\s*([0-9]{1,3})\s*(?:T|تونس)\s*([0-9]{1,4})\s*$",
    re.IGNORECASE,
)
_KNOWN_MARKER = re.compile(r"T|تونس", re.IGNORECASE)


@dataclass(frozen=True)
class PrivatePlateCandidate:
    series: str
    registration_number: str

    @property
    def normalized_plate(self) -> str:
        return f"{self.series} تونس {self.registration_number}"


@dataclass(frozen=True)
class PrivatePlateParseResult:
    status: ParserStatus
    series: str | None = None
    registration_number: str | None = None
    normalized_plate: str | None = None
    reason: str | None = None
    candidates: tuple[PrivatePlateCandidate, ...] = ()


def _valid(candidate: PrivatePlateCandidate) -> PrivatePlateParseResult:
    return PrivatePlateParseResult(
        status="valid",
        series=candidate.series,
        registration_number=candidate.registration_number,
        normalized_plate=candidate.normalized_plate,
        candidates=(candidate,),
    )


def _digit_candidates(digits: str) -> tuple[PrivatePlateCandidate, ...]:
    candidates = []
    for split_at in range(1, len(digits)):
        series = digits[:split_at]
        registration_number = digits[split_at:]
        if 1 <= len(series) <= 3 and 1 <= len(registration_number) <= 4:
            candidates.append(PrivatePlateCandidate(series, registration_number))
    return tuple(candidates)


def parse_private_plate(
    raw_text: str,
    normalized_ocr_text: str | None = None,
) -> PrivatePlateParseResult:
    """Parse one OCR observation as a private Tunisian plate.

    An explicit ``T`` or ``تونس`` fixes the boundary. If the marker is absent,
    every legal 1–3 digit / 1–4 digit split is enumerated and a result is valid
    only when that split is unique.
    """
    raw_text = raw_text or ""
    explicit = _EXPLICIT_MARKER.fullmatch(raw_text)
    if explicit is None and normalized_ocr_text is not None:
        explicit = _EXPLICIT_MARKER.fullmatch(normalized_ocr_text)
    if explicit:
        return _valid(PrivatePlateCandidate(explicit.group(1), explicit.group(2)))

    # A marker-like observation with an invalid structure must not silently fall
    # back to digits-only parsing and discard evidence about its boundary.
    if _KNOWN_MARKER.search(raw_text) or (
        normalized_ocr_text is not None and _KNOWN_MARKER.search(normalized_ocr_text)
    ):
        return PrivatePlateParseResult(
            status="invalid",
            reason="explicit private-plate marker has invalid surrounding fields",
        )

    comparison_text = (
        normalize_plate_text(raw_text)
        if normalized_ocr_text is None
        else normalize_plate_text(normalized_ocr_text)
    )
    if not comparison_text:
        return PrivatePlateParseResult(status="invalid", reason="no OCR text")
    if not comparison_text.isascii() or not comparison_text.isdigit():
        return PrivatePlateParseResult(
            status="invalid",
            reason="markerless private-plate text must contain digits only",
        )

    candidates = _digit_candidates(comparison_text)
    if not candidates:
        return PrivatePlateParseResult(
            status="invalid",
            reason="digit count cannot satisfy 1–3 digit series and 1–4 digit registration",
        )
    if len(candidates) > 1:
        return PrivatePlateParseResult(
            status="ambiguous",
            reason="multiple valid private-plate splits",
            candidates=candidates,
        )
    return _valid(candidates[0])


def parse_private_ocr(result: OCRResult) -> PrivatePlateParseResult:
    """Parse an OCR result without merging raw, normalized, and parsed fields."""
    return parse_private_plate(result.raw_text, result.normalized_text)
