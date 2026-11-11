"""Open manufacturer identifiers and normalization helpers.

This is intentionally not a closed taxonomy. Any reasonable manufacturer name can
become a stable identifier; the aliases below only cover common, unambiguous forms.
"""

from __future__ import annotations

import re
import unicodedata

UNKNOWN_MANUFACTURER = "unknown"

_UNKNOWN_VALUES = {
    "",
    "n_a",
    "na",
    "nil",
    "none",
    "not_sure",
    "not_applicable",
    "not_available",
    "not_visible",
    "null",
    "cannot_determine",
    "cannot_tell",
    "indeterminate",
    "uncertain",
    "undetermined",
    "unknown",
    "unknown_manufacturer",
    "unrecognizable",
    "unsure",
}

# Apply aliases after generic slug normalization. Keep this deliberately small and
# extensible: it is not a list of all manufacturers.
_ALIASES = {
    "benz": "mercedes",
    "bimmer": "bmw",
    "bmw_ag": "bmw",
    "mercedes_benz": "mercedes",
    "mercedesbenz": "mercedes",
    "toyota_motor_corporation": "toyota",
    "volkswagen_ag": "volkswagen",
    "vw": "volkswagen",
}

_HEDGE_WORDS = {"maybe", "possibly", "probably", "perhaps", "likely"}

# These are suffixes, not a manufacturer whitelist. Removing them keeps corporate legal
# names aligned with the public brand while arbitrary unseen manufacturer names remain
# valid.
_CORPORATE_SUFFIXES = (
    ("motor", "corporation"),
    ("motor", "company"),
    ("motors", "corporation"),
    ("motors", "company"),
    ("corporation",),
    ("company",),
    ("limited",),
    ("ltd",),
    ("inc",),
    ("corp",),
    ("ag",),
)

# A small guard for obvious "known brand + model" responses. It does not limit valid
# manufacturers; it only makes malformed additions to these common single-token brands
# fail closed.
_COMMON_SINGLE_TOKEN_BRANDS = {
    "bmw",
    "citroen",
    "dacia",
    "fiat",
    "ford",
    "hyundai",
    "kia",
    "mercedes",
    "nissan",
    "peugeot",
    "renault",
    "toyota",
    "volkswagen",
}


def normalize_manufacturer(value: object | None) -> str:
    """Return the project's canonical manufacturer identifier, or ``unknown``.

    Unseen names remain supported through generic slug generation, for example
    ``"Alfa Romeo"`` becomes ``"alfa_romeo"``.
    """

    if not isinstance(value, str):
        return UNKNOWN_MANUFACTURER

    ascii_value = unicodedata.normalize("NFKD", value.strip()).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)

    tokens = slug.split("_") if slug else []
    if slug in _UNKNOWN_VALUES or _HEDGE_WORDS.intersection(tokens):
        return UNKNOWN_MANUFACTURER

    for suffix in _CORPORATE_SUFFIXES:
        if len(tokens) > len(suffix) and tuple(tokens[-len(suffix) :]) == suffix:
            tokens = tokens[: -len(suffix)]
            break
    slug = "_".join(tokens)
    normalized = _ALIASES.get(slug, slug)

    normalized_tokens = normalized.split("_")
    if (
        len(normalized_tokens) > 1
        and normalized_tokens[0] in _COMMON_SINGLE_TOKEN_BRANDS
    ):
        return UNKNOWN_MANUFACTURER
    return normalized or UNKNOWN_MANUFACTURER
