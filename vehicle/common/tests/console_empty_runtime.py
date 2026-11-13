"""Explicit server-side acceptance fixture. Never selectable in an API body."""
import numpy as np


class EmptyVehicle:
    def __init__(self, tracked=False):
        self.prediction_calls=self.forward_calls=0
        self.closed=False
        self.placement=dict(profile='explicit empty detector fixture')
        self.tracked=tracked
    def process(self,pixels):
        self.prediction_calls+=1;self.forward_calls+=1
        return np.array([[10,10,80,80,7,.9,2,0]],dtype=np.float32) if self.tracked else np.empty((0,8),dtype=np.float32)
    def close(self):self.closed=True


class EmptyPlate:
    def process(self,pixels):
        from vehicle.common.real_video.plate_local import PlateDetections
        return PlateDetections((),())
    def close(self):pass
