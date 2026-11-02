"""Probe actual Idara scenes separately from category recognition."""
from run_tests import ROOT, OUTPUT, save
import cv2
import torch
from run_video_pipeline import config
from run_video_pipeline.plate_detector import PlateDetector
from run_video_pipeline.license_plate_plugin import PlateCategoryClassifier, load_default_config
from run_video_pipeline.ocr import PlateReader, OCRResult
from run_video_pipeline.whole_plate_numbers import recognize_whole_number
from types import SimpleNamespace

torch.set_num_threads(2)
detector = PlateDetector(config.PLATE_DETECTOR_PATH)
capture = cv2.VideoCapture(str(ROOT / 'vehicle/media/license_plate/2610c8747a524dcc94108e0f4eba1ca2/source.mp4'))
results = {'detections': [], 'manual_crops': []}
for frame_index in (120, 140, 160, 180, 200, 220, 240, 260, 280, 300):
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    if not ok:
        continue
    default = detector.predict(frame)
    low = detector.predict(frame, conf=0.01)
    results['detections'].append({'frame': frame_index, 'default': default, 'low_threshold': low})
    print(frame_index, default, low, flush=True)
capture.release()
classifier = PlateCategoryClassifier(load_default_config())
reader = PlateReader()
# Approximate plate rectangles reviewed visually in saved frames; diagnostic only.
for frame_index, box in [(120, (528, 523, 997, 640)), (240, (551, 489, 1009, 619))]:
    frame = cv2.imread(str(OUTPUT / f'frame_{frame_index}.jpg'))
    x1,y1,x2,y2 = box
    crop = frame[y1:y2,x1:x2]
    cv2.imwrite(str(OUTPUT / f'manual_plate_{frame_index}.jpg'), crop)
    classification = classifier.classify_bgr(crop)
    reading = reader.read_result(crop)
    score = reading.native_confidence if reading.native_confidence is not None else reading.confidence
    number = recognize_whole_number('idara', OCRResult(reading.raw_text, reading.normalized_text, score), SimpleNamespace(observed_markers=classification['ocr']['conflicting_categories']))
    results['manual_crops'].append({'frame': frame_index, 'manual_bbox': box, 'classification': classification, 'ocr_text': reading.raw_text, 'ocr_confidence': score, 'idara_number': number.model_dump(mode='json')})
    print('manual', frame_index, classification['decision']['final_category'], reading.raw_text, number.outcome, flush=True)
save('video_detection_results.json', results)
