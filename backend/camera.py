"""Camera capture thread: reads frames, runs them through the kiosk, encodes JPEGs."""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

import cv2
import numpy as np

from backend.controller import ViewState

log = logging.getLogger(__name__)

BOX_COLOUR = (80, 200, 120)


def open_webcam(index: int) -> cv2.VideoCapture:
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def render_jpeg(frame: np.ndarray, state: ViewState | None, quality: int = 75) -> bytes:
    """Draw face boxes, mirror the image (like a mirror) and encode it as JPEG.
    The camera's own picture is shown as it is."""
    if state is not None and state.blur:
        # A photo / phone is held up: send nothing readable to the screen.
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (max(1, w // 16), max(1, h // 16)), interpolation=cv2.INTER_AREA)
        out = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), (0, 0), 8)
    else:
        out = frame.copy()
    for x, y, w, h in (state.boxes if state else []):
        cv2.rectangle(out, (x, y), (x + w, y + h), BOX_COLOUR, 2)
    out = cv2.flip(out, 1)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


class CameraService:
    def __init__(self, camera_index: int,
                 process: Callable[[np.ndarray], ViewState | None],
                 capture_factory: Callable[[int], cv2.VideoCapture] = open_webcam):
        self._index = camera_index
        self._process = process
        self._factory = capture_factory
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._frame_id = 0
        self._jpeg: bytes | None = None
        self._state: ViewState | None = None
        self.camera_ok = False
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def latest(self) -> tuple[int, bytes | None, ViewState | None]:
        with self._cond:
            return self._frame_id, self._jpeg, self._state

    def wait_for_frame(self, after_id: int, timeout: float = 1.0) -> tuple[int, bytes | None]:
        """Block until a frame newer than `after_id` is available (or timeout)."""
        with self._cond:
            self._cond.wait_for(lambda: self._frame_id != after_id or self._stop.is_set(),
                                timeout=timeout)
            return self._frame_id, self._jpeg

    def _publish(self, jpeg: bytes | None, state: ViewState | None) -> None:
        with self._cond:
            self._frame_id += 1
            self._jpeg = jpeg
            self._state = state
            self._cond.notify_all()

    def _run(self) -> None:
        cap = None
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                cap = self._factory(self._index)
                if not cap.isOpened():
                    self.camera_ok = False
                    self._publish(None, ViewState("Camera not available",
                                                  "Check that the webcam is connected.", "error"))
                    self._stop.wait(2)
                    continue
            ok, frame = cap.read()
            if not ok or frame is None:
                self._close(cap)
                cap = None
                continue
            self.camera_ok = True
            try:
                state = self._process(frame)
            except Exception:
                log.exception("Error while processing a frame")
                state = ViewState("Something went wrong", "Please try again.", "error")
            self._publish(render_jpeg(frame, state), state)
        if cap is not None:
            self._close(cap)

    def _close(self, cap) -> None:
        cap.release()
