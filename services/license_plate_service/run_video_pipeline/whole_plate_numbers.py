"""User-specified number formats from one complete-crop OCR observation.

No category label or reference annotation supplies registration digits.
"""
import math
import re

from run_video_pipeline import config
from run_video_pipeline.ocr import normalize_digits
from run_video_pipeline.schemas import NumberRecognition


LABELS = {
    'nt': r'NT|N\s+T|ن\s*ت',
    'shorta': r'SHORTA|POLICE|شرطة',
    'idara': r'IDARA|إدارة|ادارة',
    'tunis': r'TUNIS|T|تونس',
}
MARKERS = {
    category: re.compile(r'(?<![A-Za-z\u0600-\u06ff])(?:' + pattern + r')(?![A-Za-z\u0600-\u06ff])', re.IGNORECASE)
    for category, pattern in LABELS.items()
}
DASHES = str.maketrans({char: '-' for char in '‐‑‒–—−﹘﹣－'})


def recognize_whole_number(category, reading, classification):
    """Extract strings first, then validate; retain rejected evidence in checks."""
    threshold = config.WHOLE_PLATE_MIN_OCR_CONF
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('WHOLE_PLATE_MIN_OCR_CONF must be between 0 and 1')
    normalized = re.sub(r'\s+', ' ', normalize_digits(reading.raw_text).translate(DASHES)).strip()
    markers = [name for name, pattern in MARKERS.items() if pattern.search(normalized)]
    contradictory = sorted((set(markers) | set(classification.observed_markers)) - {category})
    body = MARKERS[category].sub(' ', normalized).strip()
    body = re.sub(r'\s+', ' ', body)
    fields = {}
    display = None
    extraction_reason = None
    if not normalized:
        extraction_reason = 'empty_ocr'
    elif category == 'idara':
        match = re.fullmatch(r'([0-9]+)\s*-\s*([0-9]+)', body)
        if match:
            fields = {'first_group': match[1], 'second_group': match[2]}
            display = f'{match[1]} - {match[2]}'
        elif re.fullmatch(r'[0-9 ]+', body):
            extraction_reason = 'missing_idara_separator'
        elif re.fullmatch(r'[0-9 -]+', body):
            extraction_reason = 'ambiguous_digit_groups'
        else:
            extraction_reason = 'unexpected_ocr_text'
    elif re.fullmatch(r'[0-9]+', body):
        fields = {'registration_number': body}
        display = f'{body} nt' if category == 'nt' else f'shorta {body}'
    elif re.fullmatch(r'[0-9 -]+', body) and re.search(r'[0-9]', body):
        extraction_reason = 'ambiguous_digit_groups'
    elif not body:
        extraction_reason = 'missing_registration_digits'
    else:
        extraction_reason = 'unexpected_ocr_text'
    sufficient = math.isfinite(reading.confidence) and reading.confidence >= threshold
    reason = ('contradictory_category_marker' if contradictory else extraction_reason
              or ('insufficient_ocr_confidence' if not sufficient else None))
    return NumberRecognition(
        policy=f'{category}_whole_plate', outcome='unresolved' if reason else 'recognized', reason=reason,
        fields=fields if reason is None else {}, normalized_number=display if reason is None else None,
        checks={'format_authority': 'user_supplied_application_requirements',
                'normalized_ocr': normalized, 'numeric_body': body, 'observed_markers': markers,
                'contradictory_markers': contradictory, 'no_category_contradiction': not contradictory,
                'unambiguous_fields': bool(fields), 'extraction_reason': extraction_reason,
                'extracted_fields': fields, 'candidate_display': display,
                'label_source': 'classification' if category in ('nt', 'shorta') else 'not_appended',
                'ocr_confidence': reading.confidence, 'ocr_threshold': threshold, 'sufficient_ocr': sufficient},
        regions=[{'role': 'whole', 'input': 'selected_complete_plate_crop'}],
    )
