"""Replay detection/tracking only through the Idara scenes, without changing runtime."""
from run_tests import ROOT, save
import cv2
from run_video_pipeline import config
from run_video_pipeline.plate_detector import PlateDetector, deduplicate_detections
from run_video_pipeline.run_video_prototype import TrackerDetections, _build_tracker, DEFAULT_TRACKER, DEFAULT_ASSOCIATION_PADDING

path = ROOT / 'vehicle/media/license_plate/2610c8747a524dcc94108e0f4eba1ca2/source.mp4'
capture = cv2.VideoCapture(str(path))
tracker = _build_tracker(DEFAULT_TRACKER, capture.get(cv2.CAP_PROP_FPS))
detector = PlateDetector(config.PLATE_DETECTOR_PATH)
rows=[]
for index in range(321):
    ok, frame = capture.read()
    if not ok:
        break
    detections = deduplicate_detections(detector.predict(frame), iou_threshold=config.PLATE_DEDUP_IOU)
    tracked = tracker.update(TrackerDetections.from_tuples(detections, association_padding=DEFAULT_ASSOCIATION_PADDING), frame)
    rows.append({'frame': index, 'detections': detections, 'track_ids': [int(row[4]) for row in tracked]})
    if index in (0, 60, 119, 120, 121, 180, 239, 240, 241, 300, 320):
        print(index, rows[-1]['track_ids'], flush=True)
capture.release()
save('tracking_results.json', rows)
