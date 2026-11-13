"""Isolated scalar policy port of Mohamed's active Django LPR, phase 1.

Reference HEAD: f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16.
Source paths below are relative to vehicle/license_plate_service/run_video_pipeline.
No execution, transport, catalogue resolution or live T1 integration occurs here.
Inputs describe one already-associated crop; the caller supplies their provenance.
"""
from dataclasses import dataclass
import math
import re
import unicodedata


SOURCE_HEAD = "f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16"
STATES = frozenset({"succeeded", "missing", "not_started", "pending", "failed", "unknown"})
CATEGORIES = frozenset({"tunis", "idara", "nt", "shorta"})
VISUAL_CATEGORIES = CATEGORIES - {"nt"}
TUNIS_MODES = frozenset({"legacy_whole", "marker_first"})


def _require(condition, message):
    if not condition:
        raise ValueError(message)  # Never include OCR contents in validation errors.


def _name(value):
    _require(type(value) is str and bool(value.strip()) and len(value.encode()) <= 256,
             "bounded nonempty evidence identity required")


def _score(value, low=0.0):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= 1,
             "finite evidence score in range required")


def _execution(evidence_id, state, model_ref):
    _name(evidence_id)
    _require(type(state) is str and state in STATES, "explicit execution state required")
    if model_ref is not None:
        _name(model_ref)
    if state == "succeeded":
        _require(model_ref is not None, "successful execution needs a model reference")


@dataclass(frozen=True)
class OCR:
    evidence_id: str
    state: str
    raw_text: str | None = None
    confidence: float | None = None  # Original, unrounded native score.
    model_ref: str | None = None

    def __post_init__(self):
        _execution(self.evidence_id, self.state, self.model_ref)
        if self.state == "succeeded":
            _require(type(self.raw_text) is str and len(self.raw_text.encode()) <= 4096,
                     "bounded OCR text required")
            _score(self.confidence)
        else:
            _require(self.raw_text is None and self.confidence is None,
                     "unfinished or failed OCR cannot supply a successful reading")


@dataclass(frozen=True)
class ReferenceScore:
    reference_id: str
    category: str
    similarity: float

    def __post_init__(self):
        _name(self.reference_id)
        _require(type(self.category) is str and self.category in VISUAL_CATEGORIES,
                 "visual reference category must be tunis, idara or shorta")
        _score(self.similarity, -1.0)  # Cosine similarity, not a probability.


@dataclass(frozen=True)
class DinoScores:
    evidence_id: str
    state: str
    scores: tuple[ReferenceScore, ...] = ()
    model_ref: str | None = None
    index_ref: str | None = None

    def __post_init__(self):
        _execution(self.evidence_id, self.state, self.model_ref)
        _require(type(self.scores) is tuple and len(self.scores) <= 64
                 and all(type(row) is ReferenceScore for row in self.scores), "bounded immutable reference scores required")
        _require(len({row.reference_id for row in self.scores}) == len(self.scores), "duplicate reference identity")
        if self.index_ref is not None:
            _name(self.index_ref)
        if self.state == "succeeded":
            _require(bool(self.scores), "successful visual scoring needs reference scores")
            _name(self.index_ref)
        else:
            _require(not self.scores, "unsuccessful visual execution cannot supply scores")


@dataclass(frozen=True)
class Marker:
    evidence_id: str
    state: str
    detected: bool | None = None
    confidence: float | None = None
    bbox: tuple[int, int, int, int] | None = None  # Crop-local xyxy.
    model_ref: str | None = None

    def __post_init__(self):
        _execution(self.evidence_id, self.state, self.model_ref)
        if self.state == "succeeded":
            _require(type(self.detected) is bool, "explicit marker detection result required")
            _score(self.confidence)
            if self.bbox is not None:
                _require(type(self.bbox) is tuple and len(self.bbox) == 4
                         and all(type(v) is int and v >= 0 for v in self.bbox)
                         and self.bbox[0] < self.bbox[2] and self.bbox[1] < self.bbox[3], "valid crop-local marker box required")
            _require(self.detected or self.bbox is None, "absent marker cannot have a box")
        else:
            _require(self.detected is None and self.confidence is None and self.bbox is None,
                     "unsuccessful marker execution cannot supply a detection")


@dataclass(frozen=True)
class Evidence:
    category_ocr: OCR | None = None
    visual: DinoScores | None = None
    registration: OCR | None = None
    marker: Marker | None = None
    left: OCR | None = None
    right: OCR | None = None
    crop_size: tuple[int, int] | None = None  # Original admitted width, height.

    def __post_init__(self):
        values = []
        for name, expected in (("category_ocr", OCR), ("visual", DinoScores), ("registration", OCR),
                               ("marker", Marker), ("left", OCR), ("right", OCR)):
            value = getattr(self, name)
            _require(value is None or type(value) is expected, "typed scalar evidence required")
            if value is not None:
                values.append(value.evidence_id)
        _require(len(values) == len(set(values)), "execution identities must be distinct across evidence roles")
        if self.crop_size is not None:
            _require(type(self.crop_size) is tuple and len(self.crop_size) == 2
                     and all(type(v) is int and 0 < v <= 100000 for v in self.crop_size), "positive bounded crop dimensions required")


@dataclass(frozen=True)
class Thresholds:
    """Application thresholds from config.py, not independently calibrated rules."""
    whole_ocr: float = 0.90
    private_ocr: float = 0.90
    marker_reliable: float = 0.50
    private_marker: float = 0.50
    regional_ocr: float = 0.50

    def __post_init__(self):
        for value in (self.whole_ocr, self.private_ocr, self.marker_reliable,
                      self.private_marker, self.regional_ocr):
            _score(value)


@dataclass(frozen=True)
class Decision:
    category: str | None
    category_source: str | None
    status: str  # accepted | unresolved; inspect reason_kind for failure vs format.
    reason: str | None
    reason_kind: str | None
    observed_text: tuple[tuple[str, str], ...]  # Exact text by registration role; never a fabricated whole read.
    normalized_registration: str | None
    registration_fields: tuple[tuple[str, str], ...]
    display_label: str | None
    display_text: str | None
    evidence_used: tuple[tuple[str, OCR | DinoScores | Marker], ...]
    tunis_mode: str
    thresholds: Thresholds
    crop_size: tuple[int, int] | None
    category_conflicts: tuple[str, ...] = ()
    nearest_reference_id: str | None = None
    nearest_similarity: float | None = None
    category_margin: float | None = None
    visual_tie: bool = False
    warnings: tuple[str, ...] = ()
    checks: tuple[tuple[str, bool], ...] = ()
    policy_version: str = "mohamed-lpr-plate-policy-phase1-v1"


def normalize_digits(text):
    """Source ocr.normalize_digits: decimal conversion without integer parsing."""
    return "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)


def normalize_ocr(text):
    """Source ocr.normalize_plate_text; preserve category words and separators."""
    return re.sub(r"\s+", " ", normalize_digits(unicodedata.normalize("NFKC", text))).strip().upper()


def _digits(text):
    return "".join(char for char in normalize_digits(text) if char in "0123456789")


def category_markers(text):
    """Source license_plate_plugin.parser.parse_nt_evidence (including its limited conflict set)."""
    boundary = "".join(char for char in unicodedata.normalize("NFKC", text)
                       if char != "\u0640" and unicodedata.category(char) != "Mn")
    letter = r"[\u0621-\u063A\u0641-\u064A\u066E-\u066F\u0671-\u06D3\u06FA-\u06FC]"
    patterns = {"nt": rf"(?<!{letter})ن\s*ت(?!{letter})",
                "shorta": rf"(?<!{letter})شرطة(?!{letter})", "tunis": rf"(?<!{letter})تونس(?!{letter})"}
    matches = {name: bool(re.search(pattern, boundary)) for name, pattern in patterns.items()}
    return matches["nt"], tuple(name for name in ("shorta", "tunis") if matches[name])


def _reference_key(row):
    value = row.reference_id
    return (0, int(value)) if value.isdecimal() else (1, value)


def _visual_choice(scores):
    """Source ReferenceIndex.match: cosine maximum and deterministic reference-ID tie break."""
    tolerance = 1e-12
    best = max(row.similarity for row in scores)
    tied = [row for row in scores if abs(row.similarity - best) <= tolerance]
    winner = min(tied, key=_reference_key)
    category_max = {}
    for row in scores:
        old = category_max.get(row.category)
        if old is None or row.similarity > old.similarity + tolerance or (
                abs(row.similarity - old.similarity) <= tolerance and _reference_key(row) < _reference_key(old)):
            category_max[row.category] = row
    others = [row.similarity for category, row in category_max.items() if category != winner.category]
    tie = len(tied) > 1 or sum(abs(row.similarity - best) <= tolerance for row in category_max.values()) > 1
    return winner, best, best - max(others) if others else None, tie


_LABELS = {"nt": r"NT|N\s+T|ن\s*ت", "shorta": r"SHORTA|POLICE|شرطة",
           "idara": r"IDARA|إدارة|ادارة", "tunis": r"TUNIS|T|تونس"}
_MARKERS = {name: re.compile(r"(?<![A-Za-z\u0600-\u06ff])(?:" + value + r")(?![A-Za-z\u0600-\u06ff])", re.I)
            for name, value in _LABELS.items()}
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—−﹘﹣－"})
_PRIVATE_MARKER = re.compile(r"^\s*([0-9]{1,3})\s*(?:T|تونس)\s*([0-9]{1,4})\s*$", re.I)


def _private_parse(raw, normalized):
    """Source private_plate_parser.parse_private_plate: no guessed digit boundary."""
    raw = normalize_digits(raw)
    match = _PRIVATE_MARKER.fullmatch(raw) or _PRIVATE_MARKER.fullmatch(normalized)
    if match:
        return "valid", match[1], match[2]
    if re.search(r"T|تونس", raw, re.I) or re.search(r"T|تونس", normalized, re.I):
        return "invalid", None, None
    body = re.sub(r"[\s-]", "", normalize_ocr(normalized))
    if not body or not body.isascii() or not body.isdigit():
        return "invalid", None, None
    candidates = [(body[:at], body[at:]) for at in range(1, len(body))
                  if 1 <= at <= 3 and 1 <= len(body) - at <= 4]
    if len(candidates) != 1:
        return "ambiguous" if candidates else "invalid", None, None
    return "valid", *candidates[0]


def decide(evidence: Evidence, *, tunis_mode: str, thresholds: Thresholds = Thresholds()) -> Decision:
    """Interpret supplied scalars. tunis_mode is mandatory; no environment/runtime default.

    Category precedence/fallback: PlateCategoryClassifier._classify_image.
    Number decisions: recognize_whole_number, _analyze_tunis_sides,
    assemble_plate_detection and validate_tunis. Display output is deliberately
    separated from normalized registration fields rather than treated as OCR.
    """
    _require(type(evidence) is Evidence and type(thresholds) is Thresholds, "typed evidence and thresholds required")
    _require(type(tunis_mode) is str and tunis_mode in TUNIS_MODES, "explicit legacy_whole or marker_first Tunis mode required")
    used, observed, warnings = [], [], []
    category = source = None
    conflicts = ()
    nearest = similarity = margin = None
    tie = False

    def finish(reason=None, kind=None, fields=(), registration=None, label=None, display=None, checks=()):
        return Decision(category=category, category_source=source,
                        status="unresolved" if reason else "accepted", reason=reason, reason_kind=kind,
                        observed_text=tuple(observed), normalized_registration=registration,
                        registration_fields=tuple(fields), display_label=label, display_text=display,
                        evidence_used=tuple(used), tunis_mode=tunis_mode, thresholds=thresholds,
                        crop_size=evidence.crop_size, category_conflicts=conflicts,
                        nearest_reference_id=nearest, nearest_similarity=similarity, category_margin=margin,
                        visual_tie=tie, warnings=tuple(warnings), checks=tuple(checks))

    def take(role):
        value = getattr(evidence, role)
        if value is not None:
            used.append((role, value))
            if role in {"registration", "left", "right"} and value.state == "succeeded":
                observed.append((role, value.raw_text))
        state = value.state if value is not None else "missing"
        if state == "succeeded":
            return value, None
        kind = {"failed": "inference_failure", "unknown": "unknown_outcome", "pending": "pending_evidence"}.get(state, "missing_evidence")
        return value, finish(role + "_" + state, kind)

    arabic, failure = take("category_ocr")
    if failure is not None and (arabic is None or arabic.state != "failed"):
        return failure  # Missing/unfinished evidence is not an observed negative NT result.
    if failure is None:
        nt, conflicts = category_markers(arabic.raw_text)
    else:
        nt = False
        warnings.append("category_ocr_failed_visual_fallback")
    if nt:
        category, source = "nt", "ocr_nt_evidence"  # Even conflicting NT evidence precedes DINO.
    else:
        visual, failure = take("visual")
        if failure is not None:
            return failure
        winner, similarity, margin, tie = _visual_choice(visual.scores)
        category, source, nearest = winner.category, "visual_r2", winner.reference_id

    if category != "tunis":
        reading, failure = take("registration")
        if failure is not None:
            return failure
        # Source whole_plate_numbers.recognize_whole_number uses the native score,
        # unlike the retained rounded Tunis reader confidence.
        text = re.sub(r"\s+", " ", normalize_digits(reading.raw_text).translate(_DASHES)).strip()
        seen = {name for name, pattern in _MARKERS.items() if pattern.search(text)}
        contradictory = (seen | set(conflicts)) - {category}
        body = re.sub(r"\s+", " ", _MARKERS[category].sub(" ", text).strip())
        fields, registration, label, display, reason = (), None, None, None, None
        if not text:
            reason = "empty_ocr"
        elif category == "idara":
            match = re.fullmatch(r"([0-9]+)\s*-\s*([0-9]+)", body)
            if match:
                fields = (("first_group", match[1]), ("second_group", match[2]))
                registration = display = f"{match[1]} - {match[2]}"
            elif re.fullmatch(r"[0-9 ]+", body):
                reason = "missing_idara_separator"
            elif re.fullmatch(r"[0-9 -]+", body):
                reason = "ambiguous_digit_groups"
            else:
                reason = "unexpected_ocr_text"
        elif re.fullmatch(r"[0-9]+", body):
            fields, registration, label = (("registration_number", body),), body, category
            display = f"{body} nt" if category == "nt" else f"shorta {body}"
        elif re.fullmatch(r"[0-9 -]+", body) and re.search(r"[0-9]", body):
            reason = "ambiguous_digit_groups"
        else:
            reason = "missing_registration_digits" if not body else "unexpected_ocr_text"
        checks = (("no_category_contradiction", not contradictory), ("unambiguous_fields", bool(fields)),
                  ("sufficient_ocr", reading.confidence >= thresholds.whole_ocr))
        if contradictory:
            return finish("contradictory_category_marker", "conflict", checks=checks)
        if reason:
            return finish(reason, "invalid_format", checks=checks)
        if reading.confidence < thresholds.whole_ocr:
            return finish("insufficient_ocr_confidence", "low_confidence", checks=checks)
        return finish(fields=fields, registration=registration, label=label, display=display, checks=checks)

    if tunis_mode == "legacy_whole":
        whole, failure = take("registration")
        if failure is not None:
            return failure
    marker, failure = take("marker")
    if failure is not None:
        return failure
    reliable = marker.detected and marker.confidence >= thresholds.marker_reliable
    valid_regions = False
    if marker.bbox is not None:
        if evidence.crop_size is not None:
            valid_regions = 0 < marker.bbox[0] < marker.bbox[2] < evidence.crop_size[0]
    left = right = None
    if tunis_mode == "marker_first":
        reliable = reliable and marker.bbox is not None and marker.confidence >= thresholds.private_marker
        if not reliable:
            return finish("insufficient_marker_evidence", "insufficient_evidence")
        if evidence.crop_size is None:
            return finish("crop_geometry_missing", "missing_evidence")
        if not valid_regions:
            return finish("invalid_marker_regions", "invalid_format")
        state, series, number = "invalid", None, None
    else:
        normalized = normalize_ocr(whole.raw_text)
        state, series, number = _private_parse(whole.raw_text, normalized)
    needs_regions = tunis_mode == "marker_first" or (state == "ambiguous" and reliable and marker.bbox is not None)
    if needs_regions and evidence.crop_size is None:
        return finish("crop_geometry_missing", "missing_evidence")
    if needs_regions and valid_regions:
        left, failure = take("left")
        if failure is not None:
            return failure
        right, failure = take("right")
        if failure is not None:
            return failure
    left_text = normalize_ocr(left.raw_text) if left else ""
    right_text = normalize_ocr(right.raw_text) if right else ""
    left_conf = round(left.confidence, 4) if left else 0.0
    right_conf = round(right.confidence, 4) if right else 0.0
    sides_valid = (left_text.isascii() and left_text.isdigit() and 1 <= len(left_text) <= 3
                   and right_text.isascii() and right_text.isdigit() and 1 <= len(right_text) <= 4)
    sides_confident = left_conf >= thresholds.regional_ocr and right_conf >= thresholds.regional_ocr
    if tunis_mode == "marker_first":
        if sides_valid:
            state, series, number = _private_parse(f"{left_text}T{right_text}", "")
        raw = f"{left.raw_text} {right.raw_text}"
        normalized = f"{left_text} {right_text}"
        ocr_conf = min(left_conf, right_conf)
        marker_conf = marker.confidence
    else:
        raw, ocr_conf, marker_conf = whole.raw_text, round(whole.confidence, 4), round(marker.confidence, 4)
        if state == "ambiguous" and reliable and sides_valid and sides_confident and left_text + right_text == _digits(normalized):
            state, series, number = _private_parse(f"{left_text}T{right_text}", "")
    # Source policies.validate_tunis, including reason ordering.
    conflicting = (tunis_mode == "legacy_whole" and sides_valid and sides_confident
                   and left_text + right_text != _digits(normalized))
    private_valid = state == "valid" and series is not None and number is not None
    marker_ok = reliable and marker.bbox is not None and marker_conf >= thresholds.private_marker
    checks = (("valid_private_fields", private_valid), ("reliable_tounes_marker", marker_ok),
              ("sufficient_ocr", ocr_conf >= thresholds.private_ocr),
              ("no_category_conflict", all(name == "tunis" for name in conflicts)))
    if conflicting:
        return finish("conflicting_readings", "conflict", checks=checks)
    if state == "ambiguous":
        return finish("ambiguous_fields", "invalid_format", checks=checks)
    if not raw:
        return finish("insufficient_ocr_evidence", "invalid_format", checks=checks)
    if not all(name == "tunis" for name in conflicts):
        return finish("conflicting_category_evidence", "conflict", checks=checks)
    if not private_valid:
        return finish("invalid_format", "invalid_format", checks=checks)
    if not marker_ok:
        return finish("insufficient_marker_evidence", "insufficient_evidence", checks=checks)
    if ocr_conf < thresholds.private_ocr or (tunis_mode == "marker_first" and not sides_confident):
        return finish("insufficient_ocr_confidence", "low_confidence", checks=checks)
    if tunis_mode == "legacy_whole" and _digits(series + number) != _digits(normalized):
        return finish("conflicting_readings", "conflict", checks=checks)
    return finish(fields=(("series", series), ("registration_number", number)),
                  registration=f"{series} {number}", label="تونس", display=f"{series} تونس {number}", checks=checks)
