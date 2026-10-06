"""Presentation-attack detection: is a photo, phone or screen being shown instead of a face?

Checks, combined by SpoofGuard. Each needs strong, repeated evidence, because blocking a
real student is worse than a slower catch:
  * the anti-spoof network (backend/face/antispoof.py) scores most of the last frames as
    not live;
  * the object detector (backend/face/objects.py) sees a phone, laptop or screen at the
    face in at least two of its last three runs (runs in a background thread);
  * blinking: a photo or screen cannot blink. A blink only counts when the eyes close and
    reopen while the rest of the face keeps its shape (tilting a photo changes the whole
    face at once). A real blink clears a borderline score; a face asked to blink that never
    really blinks, and does not score clearly live, is a photo;
  * supporting evidence when the score is also low: the straight edges of a photo or phone
    around the face, or a face that moves like a flat sheet (one homography).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

OBJECT_VALID_S = 1.5      # detector runs older than this are ignored
OBJECT_RUNS = 3           # an object must be seen in 2 of the last 3 runs
OBJECT_MAX_AREA = 6.0     # objects bigger than this many face areas are background
PHONE_TRAINING_FLOOR = 0.1  # phone training measures even weak sightings
PLANAR_MIN_POSE = 0.12    # head must have turned/tilted this much before judging flatness
PLANAR_MAX_ERROR = 0.015  # landmark fit error (share of face width) below which it is flat
# Edges and flatness are supporting evidence only: they block when the network also leans
# towards "not live" (a door frame or poster behind a real student must never block them).
SUPPORT_NEEDS_SCORE = 0.45
# After a genuine blink, only a very low score still blocks (a screen replaying a video).
AFTER_BLINK_SCORE = 0.15
BLINK_MAX_S = 0.8         # eyes closed longer than this is not a blink
BLINK_MAX_POSE = 0.08     # head turn/tilt allowed during a blink


@dataclass(frozen=True)
class SpoofVerdict:
    kind: str | None = None   # None (clean) | "object" | "photo"
    label: str = ""           # what was held up: "phone", "photo", "screen", ...
    reason: str = ""          # which check fired (for the IT officer's log)

    @property
    def spoof(self) -> bool:
        return self.kind is not None


CLEAN = SpoofVerdict()


def _overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _grow(box, factor: float):
    x, y, w, h = box
    return x - w * (factor - 1) / 2, y - h * (factor - 1) / 2, w * factor, h * factor


def _at_face(box, face_box) -> bool:
    """A hand-held object at the face: overlaps it, and isn't a big background screen."""
    _, _, bw, bh = box
    face_area = max(face_box[2] * face_box[3], 1)
    return bw * bh <= OBJECT_MAX_AREA * face_area and _overlap(box, _grow(face_box, 1.6))


def photo_frame_around(frame: np.ndarray, face_box) -> bool:
    """True if a four-sided outline (a photo's or phone's edge) surrounds the face."""
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(grey, (5, 5), 0), 50, 150), None)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    fx, fy, fw, fh = face_box
    face_area = max(fw * fh, 1)
    frame_area = grey.shape[0] * grey.shape[1]
    for c in contours:
        area = cv2.contourArea(c)
        if not (1.3 * face_area <= area <= 8 * face_area) or area > 0.6 * frame_area:
            continue
        quad = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        x, y, w, h = cv2.boundingRect(quad)
        touches_edge = x <= 2 or y <= 2 or x + w >= grey.shape[1] - 2 or y + h >= grey.shape[0] - 2
        if not touches_edge and x <= fx and y <= fy and x + w >= fx + fw and y + h >= fy + fh:
            return True
    return False


def is_flat(first: np.ndarray, now: np.ndarray, face_width: float) -> bool:
    """True if `now` is `first` seen through one homography, i.e. a flat picture moved."""
    if len(first) < 6 or len(first) != len(now):
        return False
    H, _ = cv2.findHomography(first.astype(np.float32), now.astype(np.float32), 0)
    if H is None:
        return False
    projected = cv2.perspectiveTransform(first.reshape(-1, 1, 2).astype(np.float64), H)
    error = np.linalg.norm(projected.reshape(-1, 2) - now, axis=1).mean()
    return error / max(face_width, 1.0) < PLANAR_MAX_ERROR


# Index pairs into SHAPE_POINTS (backend/face/signals.py): proportions of the face that a
# blink leaves alone, measured against the distance between the outer eye corners.
_SHAPE_PAIRS = ((4, 8),    # nose tip - chin
                (11, 8),   # forehead - chin
                (6, 7),    # mouth width
                (9, 10),   # cheek to cheek
                (5, 4),    # nose bridge - nose tip
                (0, 1),    # right eye width
                (2, 3))    # left eye width


def face_shape(points: np.ndarray) -> np.ndarray | None:
    """Face proportions that stay the same while a real person blinks."""
    if points is None or len(points) < 12:
        return None
    eyes = float(np.linalg.norm(points[0] - points[3]))
    if eyes < 1.0:
        return None
    return np.array([np.linalg.norm(points[a] - points[b]) / eyes for a, b in _SHAPE_PAIRS])


class BlinkJudge:
    """Tells a genuine blink (eyes only) from a fake one (a tilted or bent photo)."""

    def __init__(self, closed: float = 0.5, open_: float = 0.25, max_change: float = 0.05):
        self._closed = closed
        self._open = open_
        self._max_change = max_change
        self._before: tuple[np.ndarray, float, float, float] | None = None  # shape, yaw, pitch, t
        self._last_open: tuple[np.ndarray, float, float] | None = None

    def update(self, blink: float, points, yaw: float, pitch: float, t: float) -> str | None:
        """Feed one frame. Returns "genuine" or "fake" when a blink has just finished."""
        shape = face_shape(points)
        if self._before is None:
            if blink >= self._closed and self._last_open is not None:
                self._before = (*self._last_open, t)   # the face just before the eyes closed
            elif blink <= self._open and shape is not None:
                self._last_open = (shape, yaw, pitch)
            return None
        before_shape, before_yaw, before_pitch, closed_at = self._before
        if t - closed_at > BLINK_MAX_S:
            self._before = None                        # eyes shut too long: not a blink
            return "fake"
        if blink > self._open:
            return None
        self._before = None
        if shape is None:
            return None
        change = float(np.max(np.abs(shape / before_shape - 1.0)))
        moved = max(abs(yaw - before_yaw), abs(pitch - before_pitch))
        self._last_open = (shape, yaw, pitch)
        if change <= self._max_change and moved <= BLINK_MAX_POSE:
            return "genuine"
        return "fake"


class ObjectWatcher:
    """Runs the (slow, ~60 ms) object detector on the newest frame in its own thread."""

    def __init__(self, detector, threaded: bool = True, clock: Callable[[], float] = time.monotonic):
        self._detector = detector
        self._clock = clock
        self._threaded = threaded
        self._lock = threading.Lock()
        self._detect_lock = threading.Lock()   # the network is not thread-safe
        self._pending: np.ndarray | None = None
        self._runs: deque[tuple[float, list]] = deque(maxlen=OBJECT_RUNS)
        self._wake = threading.Event()
        self._stop = threading.Event()
        if threaded:
            threading.Thread(target=self._run, name="objects", daemon=True).start()

    def submit(self, frame) -> None:
        if not self._threaded:
            self._record(self._detect(frame))
            return
        with self._lock:
            self._pending = frame
        self._wake.set()

    def _record(self, found: list) -> None:
        with self._lock:
            self._runs.append((self._clock(), found))

    def runs(self) -> list[list]:
        """Detections of the last few runs (oldest first), ignoring stale ones."""
        now = self._clock()
        with self._lock:
            return [found for at, found in self._runs if now - at <= OBJECT_VALID_S]

    def detect_now(self, frame, min_score: float) -> list:
        """Runs the detector on this frame in the calling thread (phone training)."""
        return self._detect(frame, min_score)

    def _detect(self, frame, min_score: float | None = None) -> list:
        with self._detect_lock:
            if min_score is None:
                return self._detector.detect(frame)
            return self._detector.detect(frame, min_score)

    def recent(self) -> list:
        runs = self.runs()
        return runs[-1] if runs else []

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
            if frame is None:
                continue
            try:
                self._record(self._detect(frame))
            except Exception:
                log.exception("Object detection failed")


class SpoofGuard:
    def __init__(self, cfg, net, objects: ObjectWatcher | None,
                 threshold: Callable[[], float] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 blink_closed: float = 0.5, blink_open: float = 0.25,
                 object_threshold: Callable[[], float] | None = None):
        self._cfg = cfg
        self._net = net
        self._objects = objects
        self._threshold = threshold or (lambda: cfg.threshold)
        # Phone/screen confidence that blocks (kiosk phone training can change it).
        self._object_threshold = object_threshold or (lambda: cfg.object_score)
        self._clock = clock
        self._blink_levels = (blink_closed, blink_open)
        self._scores: deque[float] = deque(maxlen=max(1, cfg.smooth_frames))
        self._frame_no = 0
        self.live_frames = 0      # consecutive scored frames that looked live
        self.last_score: float | None = None
        self._first_points: np.ndarray | None = None
        self._first_pose: tuple[float, float] | None = None
        self._flat = False
        self._attempt_t: float | None = None
        self._expect_blink = False
        self._judge = self._new_judge()
        self.genuine_blinks = 0
        self.fake_blinks = 0

    @property
    def threshold(self) -> float:
        return self._threshold()

    def _new_judge(self) -> BlinkJudge:
        return BlinkJudge(*self._blink_levels, max_change=self._cfg.blink_shape_change)

    def reset(self) -> None:
        """A new person: forget every score and the attempt."""
        self._scores.clear()
        self.live_frames = 0
        self.end_attempt()

    def new_attempt(self, t: float | None = None, expect_blink: bool = True) -> None:
        """A check is starting (the student is asked to blink, unless expect_blink=False)."""
        self._first_points = None
        self._first_pose = None
        self._flat = False
        self._attempt_t = self._clock() if t is None else t
        self._expect_blink = expect_blink
        self._judge = self._new_judge()
        self.genuine_blinks = 0
        self.fake_blinks = 0

    def end_attempt(self) -> None:
        self._first_points = None
        self._first_pose = None
        self._flat = False
        self._attempt_t = None
        self._expect_blink = False
        self._judge = self._new_judge()
        self.genuine_blinks = 0
        self.fake_blinks = 0

    def landmarks(self, points: np.ndarray | None, yaw: float, pitch: float,
                  face_width: float, blink: float | None = None,
                  t: float | None = None) -> str | None:
        """Feeds face landmarks during a check. Returns "genuine" / "fake" when a blink has
        just finished, else None. Also detects a face that moves like a flat picture."""
        result = None
        if blink is not None:
            result = self._judge.update(blink, points, yaw, pitch,
                                        self._clock() if t is None else t)
            if result == "genuine":
                self.genuine_blinks += 1
            elif result == "fake":
                self.fake_blinks += 1
        if self._cfg.planarity and points is not None:
            if self._first_points is None:
                self._first_points, self._first_pose = points, (yaw, pitch)
            else:
                moved = max(abs(yaw - self._first_pose[0]), abs(pitch - self._first_pose[1]))
                if moved >= PLANAR_MIN_POSE:
                    self._flat = is_flat(self._first_points, points, face_width)
        return result

    def score(self, frame, face) -> float:
        """The live-face score for this frame (also used by kiosk tuning)."""
        return self._net.real_probability(frame, face)

    def phone_score(self, frame, face) -> float:
        """Kiosk phone training: the most confident phone/screen at the face (0 if none).
        Runs the detector directly, with a low floor so weak sightings are measured too."""
        if self._objects is None:
            return 0.0
        face_box = tuple(int(v) for v in face[:4])
        found = self._objects.detect_now(frame, PHONE_TRAINING_FLOOR)
        return max((d.score for d in found if _at_face(d.box, face_box)), default=0.0)

    def check(self, frame, faces, t: float | None = None, steady: bool = True) -> SpoofVerdict:
        t = self._clock() if t is None else t
        self._frame_no += 1
        if (self._objects is not None and len(faces) > 0
                and self._frame_no % max(1, self._cfg.object_every_n) == 0):
            self._objects.submit(frame)
        face_box = tuple(int(v) for v in faces[0][:4]) if len(faces) == 1 else None
        if face_box is None:
            return CLEAN

        verdict = self._object_verdict(face_box)
        if verdict is None:
            verdict = self._face_verdict(frame, faces[0], face_box, t, steady)
        if verdict is not None:
            self.live_frames = 0
            return verdict
        return CLEAN

    def _object_verdict(self, face_box) -> SpoofVerdict | None:
        """A phone, laptop or screen at the face, seen in at least 2 of the last 3 runs."""
        if self._objects is None:
            return None
        runs = self._objects.runs()
        if not runs:
            return None
        min_score = self._object_threshold()
        for det in runs[-1]:
            if det.score < min_score or not _at_face(det.box, face_box):
                continue
            near = _grow(det.box, 1.5)
            seen = 1 + sum(1 for run in runs[:-1]
                           if any(o.label == det.label and o.score >= min_score
                                  and _overlap(o.box, near) for o in run))
            if seen >= 2:
                return SpoofVerdict("object", det.label, f"{det.label} detected ({det.score:.2f})")
        return None

    def _face_verdict(self, frame, face, face_box, t: float, steady: bool) -> SpoofVerdict | None:
        # Small or shaken faces give unreliable scores: don't judge them at all.
        if face_box[2] < self._cfg.min_face_px or not steady:
            return None
        score = self.score(frame, face)
        self.last_score = score
        self._scores.append(score)
        threshold = self.threshold
        self.live_frames = self.live_frames + 1 if score >= threshold else 0

        if len(self._scores) < min(self._cfg.min_scores, self._scores.maxlen):
            return None
        mean = float(np.mean(self._scores))
        low = float(np.mean([s < threshold for s in self._scores]))
        if self.genuine_blinks:
            # A real blink was seen: only a very clear "not live" still blocks.
            if mean < AFTER_BLINK_SCORE and low >= self._cfg.low_share:
                return SpoofVerdict("photo", "screen", f"anti-spoof score {mean:.2f} after a blink")
            return None
        if low >= self._cfg.low_share:
            return SpoofVerdict("photo", "photo", f"anti-spoof score {mean:.2f}")
        if self._attempt_t is not None and self._expect_blink and mean < self._cfg.suspect_score:
            if t - self._attempt_t >= self._cfg.no_blink_s:
                return SpoofVerdict("photo", "photo",
                                    f"no real blink in {self._cfg.no_blink_s:.0f} s "
                                    f"(score {mean:.2f})")
            if self.fake_blinks >= 2:
                return SpoofVerdict("photo", "photo", "blinks moved the whole face (tilted photo)")
        if mean >= SUPPORT_NEEDS_SCORE:
            return None
        if self._cfg.frame_edges and photo_frame_around(frame, face_box):
            return SpoofVerdict("photo", "photo", "photo or phone edges around the face")
        if self._flat:
            return SpoofVerdict("photo", "photo", "face moved like a flat picture")
        return None

    def stop(self) -> None:
        if self._objects is not None:
            self._objects.stop()

    def live_now(self) -> bool:
        """The recent frames looked like a live face (checked before recording)."""
        if not self._scores:
            return self.genuine_blinks > 0
        mean = float(np.mean(self._scores))
        return mean >= self.threshold or (self.genuine_blinks > 0 and mean >= AFTER_BLINK_SCORE)
