"""Blink and head-pose signals from MediaPipe's face landmarker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

NOSE_TIP = 1
FOREHEAD = 10
CHIN = 152
RIGHT_CHEEK_EDGE = 234  # subject's right side
LEFT_CHEEK_EDGE = 454   # subject's left side
# Stable points spread over the face's depth (eye corners, nose, mouth, chin, cheeks,
# forehead). A real head moves them non-uniformly; a printed photo moves them as one plane.
SHAPE_POINTS = (33, 133, 362, 263, 1, 168, 61, 291, 152, 234, 454, 10)


@dataclass
class FaceSignals:
    blink: float  # 0 = eyes open, 1 = eyes closed
    yaw: float    # about -1..1; positive = head turned to the subject's own left
    pitch: float  # about -1..1; positive = head tilted down
    points: np.ndarray | None = None  # SHAPE_POINTS in pixels (N x 2), for the flatness check


def _relative(value: float, start: float, end: float) -> float:
    """Where `value` sits between `start` and `end`, mapped to -1..1 (0 = middle)."""
    span = end - start
    if abs(span) < 1e-6:
        return 0.0
    return ((value - start) / span - 0.5) * 2


def yaw_from_landmarks(nose_x: float, right_edge_x: float, left_edge_x: float) -> float:
    return _relative(nose_x, right_edge_x, left_edge_x)


def pitch_from_landmarks(nose_y: float, forehead_y: float, chin_y: float) -> float:
    return _relative(nose_y, forehead_y, chin_y)


class FaceSignalsExtractor:
    def __init__(self, models_dir: Path):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        from backend.face.engine import LANDMARKER_FILE

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(models_dir / LANDMARKER_FILE)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = 0

    def signals(self, frame_bgr: np.ndarray, now: float) -> FaceSignals | None:
        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts = max(int(now * 1000), self._last_ts + 1)  # must strictly increase
        self._last_ts = ts
        result = self._landmarker.detect_for_video(image, ts)
        if not result.face_landmarks:
            return None

        lm = result.face_landmarks[0]
        blink = 0.0
        if result.face_blendshapes:
            scores = {c.category_name: c.score for c in result.face_blendshapes[0]}
            blink = (scores.get("eyeBlinkLeft", 0.0) + scores.get("eyeBlinkRight", 0.0)) / 2
        h, w = frame_bgr.shape[:2]
        return FaceSignals(
            blink=blink,
            yaw=yaw_from_landmarks(lm[NOSE_TIP].x, lm[RIGHT_CHEEK_EDGE].x, lm[LEFT_CHEEK_EDGE].x),
            pitch=pitch_from_landmarks(lm[NOSE_TIP].y, lm[FOREHEAD].y, lm[CHIN].y),
            points=np.array([(lm[i].x * w, lm[i].y * h) for i in SHAPE_POINTS], dtype=np.float32),
        )

    def close(self) -> None:
        self._landmarker.close()
