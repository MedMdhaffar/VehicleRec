"""Input owns opening, decoding and the single paired receipt for each frame."""
from dataclasses import dataclass
from pathlib import Path
from ..frame_receipt import FrameReceipt


@dataclass(frozen=True)
class DecodedFrame:
    receipt: FrameReceipt
    pixels: object  # Local Input -> Engine only. Never a broker message.


class MP4Input:
    def __init__(self,path,clock,*,capture_factory=None):
        self.path=Path(path).resolve()
        if self.path.suffix.lower()!='.mp4' or not self.path.is_file():
            raise ValueError('existing local MP4 required; no network input')
        self.clock,self.factory=clock,capture_factory
        self.capture=None
        self.decoded=0
        self.opens=0
        self.metadata={}

    def __enter__(self):
        import cv2
        if self.capture is not None:return self
        if self.opens:
            raise RuntimeError('Input cannot reopen or replay the source')
        self.opens+=1
        self.capture=(self.factory or cv2.VideoCapture)(str(self.path))
        if not self.capture.isOpened():
            self.close()
            raise RuntimeError('MP4 open failed')
        self.metadata=dict(fps=self.capture.get(cv2.CAP_PROP_FPS),
            declared_frames=self.capture.get(cv2.CAP_PROP_FRAME_COUNT),
            width=self.capture.get(cv2.CAP_PROP_FRAME_WIDTH),height=self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return self

    def read(self):
        import cv2
        if self.capture is None:
            raise RuntimeError('Input is not open')
        ok,pixels=self.capture.read()
        if not ok:
            if self.metadata.get('declared_frames',0)>self.decoded+.5:
                raise RuntimeError('mp4_decode_failed_before_declared_eof')
            return None
        receipt=self.clock.capture(self.decoded,
            media_pts_reader=lambda:self.capture.get(cv2.CAP_PROP_POS_MSEC)/1000.0)
        self.decoded+=1
        return DecodedFrame(receipt,pixels)

    def close(self):
        if self.capture is not None:
            self.capture.release()
            self.capture=None

    def __exit__(self,*_):self.close()
