"""Bounded hard-cut detection to prevent tracking across unrelated shots."""
import cv2
import numpy as np

class SceneCutDetector:
    def __init__(self, threshold=0.12):
        if not 0 <= threshold <= 1:
            raise ValueError("VEHICLE_SCENE_CUT_THRESHOLD must be between 0 and 1; 0 disables cuts")
        self.threshold = threshold
        self.previous = None

    def update(self, frame):
        current = cv2.resize(frame, (64, 64)).astype(np.float32)
        cut = False
        if self.previous is not None and self.threshold:
            difference = np.abs(current - self.previous)
            cut = float(difference.mean() / 255) >= self.threshold and float((difference.mean(axis=2) > 25).mean()) >= 0.25
        self.previous = current
        return cut
