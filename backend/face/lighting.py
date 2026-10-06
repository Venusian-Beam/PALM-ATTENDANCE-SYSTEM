"""Bright light behind the student (backlight): measure it and make dark faces readable.

The webcam's auto-exposure averages the whole picture, so a bright window behind a
student turns their face into a dark silhouette. This module tells when that happens
and brightens the face for detection, recognition and blink tracking. Normally lit
frames are returned untouched, so they still match the faces stored at registration.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

CLAHE_CLIP = 2.0
CLAHE_TILES = (8, 8)
RESCUE_GAMMA = 0.5
SMALL_WIDTH = 160  # the cheap "is this backlit?" check works on a downscaled copy


@dataclass(frozen=True)
class LightInfo:
    face_mean: float
    background_mean: float
    dark: bool
    backlit: bool


def _luma(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


def _apply_lut(frame: np.ndarray, gamma: float) -> np.ndarray:
    table = (np.linspace(0, 1, 256) ** gamma * 255).clip(0, 255).astype(np.uint8)
    return cv2.LUT(frame, table)


_CLAHE = cv2.createCLAHE(CLAHE_CLIP, CLAHE_TILES)  # used only from the camera thread


def _clahe(frame: np.ndarray) -> np.ndarray:
    """Local contrast on brightness only (YCrCb is about twice as fast as LAB here)."""
    ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    ycc[:, :, 0] = _CLAHE.apply(ycc[:, :, 0])
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)


class Lighting:
    def __init__(self, cfg):
        self._cfg = cfg

    def assess(self, frame: np.ndarray, face: np.ndarray) -> LightInfo:
        grey = _luma(frame)
        h, w = grey.shape
        x, y, fw, fh = (int(v) for v in face[:4])
        # Inner 60% of the face box: skin, not hair or the bright background.
        ix0, iy0 = max(x + int(fw * 0.2), 0), max(y + int(fh * 0.2), 0)
        ix1, iy1 = min(x + int(fw * 0.8), w), min(y + int(fh * 0.8), h)
        if ix1 - ix0 < 4 or iy1 - iy0 < 4:
            mean = float(grey.mean())
            return LightInfo(mean, mean, False, False)
        face_mean = float(grey[iy0:iy1, ix0:ix1].mean())
        # Background = everything outside the face box, from sums (no full-size mask).
        box = grey[max(y, 0):min(y + fh, h), max(x, 0):min(x + fw, w)]
        rest = grey.size - box.size
        background = (float(grey.sum(dtype=np.int64) - box.sum(dtype=np.int64)) / rest
                      if rest > 0 else face_mean)
        dark = face_mean < self._cfg.dark_face
        backlit = dark and background - face_mean >= self._cfg.backlit_gap
        return LightInfo(face_mean, background, dark, backlit)

    def enhance(self, frame: np.ndarray, info: LightInfo | None) -> np.ndarray:
        """Brighten a dark face to about `target_face`; anything else is returned as is."""
        if info is None or not info.dark:
            return frame
        mean = min(max(info.face_mean, 1.0), 254.0) / 255
        gamma = float(np.clip(np.log(self._cfg.target_face / 255) / np.log(mean), 0.25, 1.0))
        return _clahe(_apply_lut(frame, gamma))

    def maybe_backlit(self, frame: np.ndarray) -> bool:
        """Cheap test: a bright picture with a dark middle (where a student would stand)."""
        grey = _luma(frame)
        small = cv2.resize(grey, (SMALL_WIDTH, max(1, grey.shape[0] * SMALL_WIDTH // grey.shape[1])),
                           interpolation=cv2.INTER_AREA)
        bright = (small >= 250).mean() >= 0.08 or np.percentile(small, 90) >= 235
        h, w = small.shape
        centre = small[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        return bool(bright and np.percentile(centre, 25) < self._cfg.dark_face)

    @staticmethod
    def rescue(frame: np.ndarray) -> np.ndarray:
        """A strong brightening, used only to find a face that the plain frame hid."""
        return _clahe(_apply_lut(frame, RESCUE_GAMMA))
