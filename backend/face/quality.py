"""Per-frame quality: did the camera move (a bump), and is the face sharp (no motion blur)?

Frames that fail are skipped instead of being counted against the student, so a bumped
kiosk does not reject or restart anyone. Both checks together take about 1 ms per frame.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

MOTION_WIDTH = 160   # frames are downscaled to this width to measure camera motion
FACE_SIZE = 112      # face crops are resized to this before measuring sharpness
HISTORY = 60         # recent face-sharpness values used to calibrate "sharp"


@dataclass(frozen=True)
class FrameQuality:
    steady: bool
    sharp: bool
    shift: tuple[float, float] = (0.0, 0.0)  # whole-image movement since the last frame, px
    sharpness: float = 0.0

    @property
    def usable(self) -> bool:
        return self.steady and self.sharp


GOOD = FrameQuality(True, True)


class QualityMeter:
    def __init__(self, cfg):
        self._cfg = cfg
        self._prev: np.ndarray | None = None
        self._window: np.ndarray | None = None
        self._recent: deque[float] = deque(maxlen=HISTORY)

    def assess(self, frame: np.ndarray, face: np.ndarray | None) -> FrameQuality:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        shift = self._camera_shift(grey)
        steady = np.hypot(*shift) <= self._cfg.max_shift_frac * grey.shape[1]
        if face is None:
            return FrameQuality(steady, True, shift)
        sharpness = self._sharpness(grey, face)
        sharp = sharpness >= self._sharp_limit()
        if steady:  # only calibrate on frames taken while the camera was still
            self._recent.append(sharpness)
        return FrameQuality(steady, sharp, shift, sharpness)

    def _camera_shift(self, grey: np.ndarray) -> tuple[float, float]:
        scale = MOTION_WIDTH / grey.shape[1]
        small = cv2.resize(grey, (MOTION_WIDTH, max(1, round(grey.shape[0] * scale))),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        prev, self._prev = self._prev, small
        if prev is None or prev.shape != small.shape:
            return 0.0, 0.0
        if self._window is None or self._window.shape != small.shape:
            self._window = cv2.createHanningWindow(small.shape[::-1], cv2.CV_32F)
        (dx, dy), _ = cv2.phaseCorrelate(prev, small, self._window)
        return dx / scale, dy / scale

    @staticmethod
    def _sharpness(grey: np.ndarray, face: np.ndarray) -> float:
        h, w = grey.shape
        x, y, fw, fh = (int(v) for v in face[:4])
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + fw, w), min(y + fh, h)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return 0.0
        crop = cv2.resize(grey[y0:y1, x0:x1], (FACE_SIZE, FACE_SIZE), interpolation=cv2.INTER_AREA)
        return float(cv2.Laplacian(crop, cv2.CV_64F).var())

    def _sharp_limit(self) -> float:
        floor = self._cfg.min_sharpness
        if len(self._recent) < 5:
            return floor
        return max(floor, self._cfg.sharpness_ratio * float(np.percentile(self._recent, 90)))
