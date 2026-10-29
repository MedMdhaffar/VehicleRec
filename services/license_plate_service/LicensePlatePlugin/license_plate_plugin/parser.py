"""Exact Unicode normalization and conservative NT evidence parser."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_arabic_marker(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    output: list[str] = []
    for character in normalized:
        if character == "\u0640" or character.isspace():
            continue
        if unicodedata.category(character) == "Mn":
            continue
        output.append(character)
    return "".join(output)


def parse_nt_evidence(text: str) -> dict[str, Any]:
    boundary_text = unicodedata.normalize("NFKC", text)
    boundary_text = "".join(
        character
        for character in boundary_text
        if character != "\u0640" and unicodedata.category(character) != "Mn"
    )
    arabic_letter = r"[\u0621-\u063A\u0641-\u064A\u066E-\u066F\u0671-\u06D3\u06FA-\u06FC]"
    patterns = {
        "nt": rf"(?<!{arabic_letter})ن\s*ت(?!{arabic_letter})",
        "shorta": rf"(?<!{arabic_letter})شرطة(?!{arabic_letter})",
        "tunis": rf"(?<!{arabic_letter})تونس(?!{arabic_letter})",
    }
    matches = {name: bool(re.search(pattern, boundary_text)) for name, pattern in patterns.items()}
    conflicts = [name for name in ("shorta", "tunis") if matches[name]]
    if conflicts:
        decision, reason = "conflicting", "explicit_non_nt_category_evidence"
    elif matches["nt"]:
        decision, reason = "present", "explicit_nt_with_valid_arabic_letter_boundaries"
    else:
        decision, reason = "absent", "no_unambiguous_nt_match"
    return {
        "decision": decision,
        "reason": reason,
        "boundary_text": boundary_text,
        "normalized_text": normalize_arabic_marker(text),
        "matches": matches,
        "conflicting_categories": conflicts,
    }

