"""Finds phones, laptops and screens (NanoDet-Plus, COCO).

Pre-trained on the 80-class COCO dataset (Apache-2.0, OpenCV model zoo). Decoding is
adapted from the OpenCV zoo demo (models/object_detection_nanodet).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from backend.face.engine import OBJECTS_FILE

SIZE = 416
STRIDES = (8, 16, 32)
REG_MAX = 7
MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32)
STD = np.array([57.375, 57.12, 58.395], dtype=np.float32)

# COCO class index -> label for the IT officer's log. Only devices that can show a face;
# noisy classes (book, remote, scissors) caused false alarms on real students.
WATCHED = {
    62: "screen",    # tv / monitor
    63: "laptop",
    67: "phone",     # cell phone
}


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[int, int, int, int]  # x, y, w, h in frame pixels


def _anchors() -> list[np.ndarray]:
    out = []
    for stride in STRIDES:
        n = SIZE // stride
        xv, yv = np.meshgrid(np.arange(n) * stride, np.arange(n) * stride)
        out.append(np.column_stack((xv.ravel() + 0.5 * (stride - 1),
                                    yv.ravel() + 0.5 * (stride - 1))))
    return out


class ObjectDetector:
    def __init__(self, models_dir: Path, min_score: float = 0.35):
        self._net = cv2.dnn.readNet(str(models_dir / OBJECTS_FILE))
        self._min_score = min_score
        self._anchors = _anchors()
        self._project = np.arange(REG_MAX + 1, dtype=np.float32)
        self._watched = np.array(sorted(WATCHED))

    def detect(self, frame: np.ndarray, min_score: float | None = None) -> list[Detection]:
        """Watched objects scoring at least `min_score` (default: the detector's own)."""
        h, w = frame.shape[:2]
        # Letterbox to 416x416, keeping the aspect ratio.
        scale = SIZE / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        top, left = (SIZE - nh) // 2, (SIZE - nw) // 2
        canvas = np.zeros((SIZE, SIZE, 3), np.uint8)
        canvas[top:top + nh, left:left + nw] = cv2.resize(frame, (nw, nh),
                                                          interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32)
        self._net.setInput(cv2.dnn.blobFromImage((rgb - MEAN) / STD))
        outs = self._net.forward(self._net.getUnconnectedOutLayersNames())
        floor = self._min_score if min_score is None else min_score
        return self._decode(outs, scale, top, left, w, h, floor)

    def _decode(self, outs, scale, top, left, w, h, floor) -> list[Detection]:
        cls_outs = [o for o in outs if o.shape[-1] == 80]
        reg_outs = [o for o in outs if o.shape[-1] == 4 * (REG_MAX + 1)]
        boxes, scores, classes = [], [], []
        for stride, cls, reg, anchors in zip(STRIDES, cls_outs, reg_outs, self._anchors):
            cls, reg = cls.reshape(-1, 80), reg.reshape(-1, 4 * (REG_MAX + 1))
            # Best watched class per anchor. Not the best of all 80 classes: a phone held
            # in front of a face would otherwise lose to the stronger "person" score.
            watched = cls[:, self._watched]
            best = np.argmax(watched, axis=1)
            ids = self._watched[best]
            conf = watched[np.arange(len(best)), best]
            keep = conf >= floor
            if not keep.any():
                continue
            dist = np.exp(reg[keep].reshape(-1, REG_MAX + 1))
            dist = (dist / dist.sum(axis=1, keepdims=True)) @ self._project
            dist = dist.reshape(-1, 4) * stride
            pts = anchors[keep]
            x1, y1 = pts[:, 0] - dist[:, 0], pts[:, 1] - dist[:, 1]
            x2, y2 = pts[:, 0] + dist[:, 2], pts[:, 1] + dist[:, 3]
            boxes.append(np.column_stack([x1, y1, x2 - x1, y2 - y1]))
            scores.append(conf[keep])
            classes.append(ids[keep])
        if not boxes:
            return []
        boxes, scores, classes = np.concatenate(boxes), np.concatenate(scores), np.concatenate(classes)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), floor, 0.6)
        found = []
        for i in np.asarray(idx).ravel():
            bx, by, bw, bh = boxes[i]
            x = max(0, int((bx - left) / scale))
            y = max(0, int((by - top) / scale))
            found.append(Detection(WATCHED[int(classes[i])], float(scores[i]),
                                   (x, y, min(int(bw / scale), w - x), min(int(bh / scale), h - y))))
        return found
