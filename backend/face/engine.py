"""Face detection (YuNet) and recognition (SFace) using OpenCV."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
LANDMARKER_FILE = "face_landmarker.task"
ANTISPOOF_FILE = "MiniFASNetV2.onnx"
OBJECTS_FILE = "object_detection_nanodet_2022nov.onnx"
MODEL_FILES = (YUNET_FILE, SFACE_FILE, LANDMARKER_FILE, ANTISPOOF_FILE, OBJECTS_FILE)

Box = tuple[int, int, int, int]


class ModelsMissingError(Exception):
    pass


def check_models(models_dir: Path) -> None:
    missing = [name for name in MODEL_FILES if not (models_dir / name).exists()]
    if missing:
        raise ModelsMissingError(
            f"Missing model files in {models_dir}: {', '.join(missing)}. "
            "Run `python scripts/download_models.py`.")


def normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def best_match(embedding: np.ndarray, ids: np.ndarray, matrix: np.ndarray,
               exclude: int | None = None) -> tuple[int | None, float]:
    """Return (student_id, cosine similarity) of the closest registered face."""
    if len(ids) == 0:
        return None, 0.0
    scores = matrix @ normalize(embedding)
    if exclude is not None:
        scores = np.where(ids == exclude, -1.0, scores)
    idx = int(np.argmax(scores))
    if scores[idx] < -0.5:
        return None, 0.0
    return int(ids[idx]), float(scores[idx])


def face_width(face: np.ndarray) -> float:
    return float(face[2])


def face_box(face: np.ndarray) -> Box:
    x, y, w, h = (int(v) for v in face[:4])
    return x, y, w, h


class FaceEngine:
    def __init__(self, models_dir: Path, score_threshold: float = 0.85):
        check_models(models_dir)
        self._detector = cv2.FaceDetectorYN.create(
            str(models_dir / YUNET_FILE), "", (320, 320), score_threshold, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(models_dir / SFACE_FILE), "")
        self._size: tuple[int, int] | None = None

    def detect(self, frame: np.ndarray) -> list[np.ndarray]:
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._detector.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._detector.detect(frame)
        return [] if faces is None else list(faces)

    def embed(self, frame: np.ndarray, face: np.ndarray) -> np.ndarray:
        aligned = self._recognizer.alignCrop(frame, face)
        return normalize(self._recognizer.feature(aligned))
