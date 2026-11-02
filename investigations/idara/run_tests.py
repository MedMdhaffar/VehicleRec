"""Read-only production diagnostics; writes results only beside this script."""
import json
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR', str(OUTPUT / 'cache'))
os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
sys.path.insert(0, str(ROOT / 'vehicle/license_plate_service'))

import cv2
import numpy as np
import torch
from run_video_pipeline.license_plate_plugin import PlateCategoryClassifier, load_default_config
from run_video_pipeline.license_plate_plugin.representations import extract_r2
from run_video_pipeline.ocr import OCRResult, PlateReader
from run_video_pipeline.whole_plate_numbers import recognize_whole_number


def save(name, data):
    (OUTPUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def main():
    torch.set_num_threads(2)
    config = load_default_config()
    manifest = json.loads(config.reference_manifest.read_text())
    wanted = {item['sha256']: item for item in manifest['images']}
    found = {}
    for path in (ROOT / 'services/license_plate_service').rglob('*.png'):
        if path.stem not in {item['id'] for item in wanted.values()}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in wanted and digest not in found:
            found[digest] = {**wanted[digest], 'path': path, 'image': cv2.imread(str(path))}
    records = list(found.values())
    save('reference_sources.json', {'found': [{ 'id': r['id'], 'path': str(r['path']) } for r in records],
                                   'missing': [r['id'] for h, r in wanted.items() if h not in found]})
    classifier = PlateCategoryClassifier(config)
    reader = PlateReader()
    results = {'reference_tests': [], 'crop_tests': [], 'parser_tests': []}
    # Test all stored vectors even when original reference images are unavailable.
    for position, reference in enumerate(classifier.index.references):
        vector = classifier.index.embeddings[position]
        match = classifier.index.match(vector, exclude_ids=(reference['id'],), expected_category=reference['category'])
        results['reference_tests'].append({'id': reference['id'], 'expected': reference['category'], 'source': 'stored_embedding', 'leave_one_out': match})
    # Exclude each reference from its own comparison to avoid trivial self matches.
    for record in records:
        vector, _ = extract_r2(record['image'], classifier.encoder, config.policy)
        self_match = classifier.index.match(vector)
        loo = classifier.index.match(vector, exclude_ids=(record['id'],), expected_category=record['category'])
        row = {'id': record['id'], 'expected': record['category'], 'self_similarity': self_match['nearest_similarity'], 'leave_one_out': loo}
        results['reference_tests'].append(row)
        print('reference', record['id'], record['category'], '=>', loo['visual_category'], flush=True)
    # Known Idara references provide ground truth; variants probe image degradation.
    samples = []
    for record in records:
        if record['category'] == 'idara':
            image = record['image']
            samples.append((f"reference_{record['id']}_original", image, 'idara'))
            for width in (160, 80):
                height = max(1, round(image.shape[0] * width / image.shape[1]))
                small = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
                samples.append((f"reference_{record['id']}_width_{width}", small, 'idara'))
    # Actual saved crops are unlabelled: do not assume the user's Idara track ID.
    job = ROOT / 'vehicle/media/license_plate/2610c8747a524dcc94108e0f4eba1ca2/selected_crops'
    for track in (2, 3):
        for stage in ('early', 'recognized', 'final_best'):
            path = job / f'track_{track}_{stage}.jpg'
            if path.exists():
                samples.append((path.stem, cv2.imread(str(path)), None))
    for name, image, expected in samples:
        result = classifier.classify_bgr(image)
        reading = reader.read_result(image)
        evidence = SimpleNamespace(observed_markers=result['ocr']['conflicting_categories'])
        confidence = reading.native_confidence if reading.native_confidence is not None else reading.confidence
        reading = OCRResult(reading.raw_text, reading.normalized_text, confidence)
        predicted = result['decision']['final_category']
        # Counterfactual Idara parsing separates OCR/format failures from category errors.
        actual = recognize_whole_number(predicted, reading, evidence) if predicted in ('idara', 'nt', 'shorta') else None
        idara = recognize_whole_number('idara', reading, evidence)
        row = {'name': name, 'expected_category': expected, 'size': [image.shape[1], image.shape[0]],
               'classification': result, 'registration_ocr': {'text': reading.raw_text, 'confidence': confidence},
               'predicted_number_policy': actual.model_dump(mode='json') if actual else None,
               'forced_idara_policy': idara.model_dump(mode='json')}
        results['crop_tests'].append(row)
        save('results.json', results)
        print(name, '=>', predicted, repr(reading.raw_text), 'Idara:', idara.reason, flush=True)
    cases = [('17 - 138957', .99), ('17 138957', .99), ('17138957', .99),
             ('17–138957', .99), ('١٧ - ١٣٨٩٥٧', .99), ('17 - 138957', .89),
             ('17 - 138957 IDARA', .99), ('17 - 138957 NT', .99)]
    for text, confidence in cases:
        result = recognize_whole_number('idara', OCRResult(text, text, confidence), SimpleNamespace(observed_markers=[]))
        results['parser_tests'].append({'text': text, 'confidence': confidence, 'result': result.model_dump(mode='json')})
    save('results.json', results)
    print('Saved', OUTPUT / 'results.json', flush=True)


if __name__ == '__main__':
    main()
