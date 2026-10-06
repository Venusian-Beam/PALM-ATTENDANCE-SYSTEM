"""Is this a live face, or a photo / screen? (MiniFASNetV2, Silent-Face-Anti-Spoofing.)

A small pre-trained network (Apache-2.0) that looks at the face and its surroundings:
paper texture, screen moiré, glare and borders. It returns the probability that the face
is real. Trained on tens of thousands of real faces, printed photos and screen replays.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from backend.face.engine import ANTISPOOF_FILE

CROP_SCALE = 2.7   # the model looks at the face box widened 2.7x (as it was trained)
INPUT_SIZE = 80
REAL_CLASS = 1     # outputs: 0 = print attack, 1 = real, 2 = replay attack


def crop_face(frame: np.ndarray, face: np.ndarray, scale: float = CROP_SCALE,
              size: int = INPUT_SIZE) -> np.ndarray:
    """The face box widened `scale` times. Near the edge of the picture the box is shifted
    back inside rather than cut off, so the face is never squashed (as in training)."""
    h, w = frame.shape[:2]
    x, y, bw, bh = (float(v) for v in face[:4])
    bw, bh = max(bw, 1.0), max(bh, 1.0)
    scale = min(h / bh, w / bw, scale)
    nw, nh = bw * scale, bh * scale
    cx, cy = x + bw / 2, y + bh / 2
    x1 = int(round(min(max(cx - nw / 2, 0.0), w - nw)))
    y1 = int(round(min(max(cy - nh / 2, 0.0), h - nh)))
    x2, y2 = min(w, x1 + int(round(nw))), min(h, y1 + int(round(nh)))
    return cv2.resize(frame[y1:y2, x1:x2], (size, size))


class AntiSpoofNet:
    def __init__(self, models_dir: Path):
        self._net = cv2.dnn.readNetFromONNX(str(models_dir / ANTISPOOF_FILE))

    def real_probability(self, frame: np.ndarray, face: np.ndarray) -> float:
        crop = crop_face(frame, face).astype(np.float32)  # BGR, 0-255, as trained
        self._net.setInput(crop.transpose(2, 0, 1)[None])
        logits = self._net.forward().ravel()
        probs = np.exp(logits - logits.max())
        return float(probs[REAL_CLASS] / probs.sum())
