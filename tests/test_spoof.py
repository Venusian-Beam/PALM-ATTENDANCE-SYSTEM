"""Photo / phone / screen checks: strong evidence blocks, borderline real faces never do."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.config import AntiSpoofConfig
from backend.face.antispoof import crop_face
from backend.face.objects import WATCHED, Detection
from backend.face.spoof import (BlinkJudge, ObjectWatcher, SpoofGuard, is_flat,
                                photo_frame_around)

FRAME = np.full((480, 640, 3), 120, np.uint8)       # plain wall: no edges anywhere
FACE = np.array([270, 150, 100, 100] + [0] * 10 + [0.99], np.float32)
FACE_BOX = (270, 150, 100, 100)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeNet:
    def __init__(self, score):
        self.value = score

    def real_probability(self, _frame, _face):
        return self.value


class FakeDetector:
    def __init__(self):
        self.next: list[Detection] = []

    def detect(self, _frame):
        return list(self.next)


def guard(score, detector=None, **cfg):
    clock = Clock()
    objects = ObjectWatcher(detector, threaded=False, clock=clock) if detector else None
    g = SpoofGuard(AntiSpoofConfig(object_every_n=1, **cfg), FakeNet(score), objects, clock=clock)
    return g, clock


def run(g, clock, frames=1, step=0.1, steady=True):
    verdict = None
    for _ in range(frames):
        clock.t += step
        verdict = g.check(FRAME, [FACE], clock.t, steady)
        if verdict.spoof:
            return verdict
    return verdict


# ---- the anti-spoof score ------------------------------------------------------------------

def test_borderline_real_face_never_blocks():
    g, clock = guard(0.45)                       # real face in poor light
    assert not run(g, clock, 100).spoof
    assert g.live_frames >= 3 and g.live_now()


def test_sustained_low_score_blocks_but_not_on_the_first_frames():
    g, clock = guard(0.05)                       # printed photo
    assert not run(g, clock, 7).spoof
    assert g.live_frames == 0                    # never counted as live, so no name shown
    verdict = run(g, clock, 1)
    assert verdict.spoof and verdict.kind == "photo"


def test_a_few_low_frames_among_good_ones_do_not_block():
    g, clock = guard(0.9)
    run(g, clock, 7)
    g._net.value = 0.1
    assert not run(g, clock, 3).spoof           # 3 of 10 low: below the 70 % share


def test_small_or_shaken_faces_are_not_scored():
    g, clock = guard(0.05)
    assert not run(g, clock, 20, steady=False).spoof
    small = np.array([300, 200, 40, 40] + [0] * 10 + [0.99], np.float32)
    for _ in range(20):
        assert not g.check(FRAME, [small], clock.t).spoof
    assert g.last_score is None


# ---- blinking: a photo can't blink -----------------------------------------------------------

def face_points(tilt=1.0):
    """12 SHAPE_POINTS of a frontal face; tilt < 1 squashes it vertically (tilted photo)."""
    pts = np.array([(250, 200), (285, 200), (315, 200), (350, 200),    # eye corners
                    (300, 250), (300, 205),                            # nose tip, bridge
                    (270, 290), (330, 290),                            # mouth corners
                    (300, 340),                                        # chin
                    (230, 240), (370, 240),                            # cheeks
                    (300, 140)], np.float32)                           # forehead
    centre = pts.mean(axis=0)
    return ((pts - centre) * [1.0, tilt] + centre).astype(np.float32)


def blink(g, clock, closed_points, reopen_points):
    results = []
    for value, pts in ((0.05, face_points()), (0.9, closed_points), (0.9, closed_points),
                       (0.05, reopen_points)):
        clock.t += 0.1
        results.append(g.landmarks(pts, 0.0, 0.0, 100, value, clock.t))
    return results[-1]


def test_real_blink_is_genuine_and_tilted_photo_is_fake():
    judge = BlinkJudge()
    for t, (value, pts) in enumerate(((0.05, face_points()), (0.9, face_points()),
                                      (0.05, face_points()))):
        result = judge.update(value, pts, 0.0, 0.0, t * 0.1)
    assert result == "genuine"
    judge = BlinkJudge()
    for t, (value, pts) in enumerate(((0.05, face_points()), (0.9, face_points(0.85)),
                                      (0.05, face_points(0.85)))):
        result = judge.update(value, pts, 0.0, 0.0, t * 0.1)
    assert result == "fake"


def test_eyes_closed_too_long_is_not_a_blink():
    judge = BlinkJudge()
    judge.update(0.05, face_points(), 0.0, 0.0, 0.0)
    judge.update(0.9, face_points(), 0.0, 0.0, 0.1)
    assert judge.update(0.9, face_points(), 0.0, 0.0, 1.2) == "fake"


def test_genuine_blink_clears_a_low_ish_score():
    g, clock = guard(0.22)                       # below the threshold, but not a clear photo
    g.new_attempt(clock.t)
    assert blink(g, clock, face_points(), face_points()) == "genuine"
    assert not run(g, clock, 30).spoof
    assert g.live_now()


def test_screen_replay_still_blocked_after_a_blink():
    g, clock = guard(0.05)
    g.new_attempt(clock.t)
    blink(g, clock, face_points(), face_points())
    assert run(g, clock, 10).spoof


def test_face_asked_to_blink_that_never_blinks_is_a_photo():
    g, clock = guard(0.5)
    g.new_attempt(clock.t)
    assert not run(g, clock, 45).spoof          # 4.5 s: still waiting
    verdict = run(g, clock, 10)
    assert verdict.spoof and "blink" in verdict.reason


def test_clearly_live_face_is_never_blocked_for_not_blinking():
    g, clock = guard(0.9)
    g.new_attempt(clock.t)
    assert not run(g, clock, 100).spoof


def test_ignored_sign_out_offer_is_not_suspicious():
    g, clock = guard(0.5)
    g.new_attempt(clock.t, expect_blink=False)
    assert not run(g, clock, 100).spoof


def test_two_tilted_photo_blinks_block():
    g, clock = guard(0.5)
    g.new_attempt(clock.t)
    assert blink(g, clock, face_points(0.85), face_points(0.85)) == "fake"
    assert blink(g, clock, face_points(0.85), face_points(0.85)) == "fake"
    verdict = run(g, clock, 10)
    assert verdict.spoof and "tilted" in verdict.reason


# ---- phones, laptops and screens ------------------------------------------------------------

PHONE_AT_FACE = Detection("phone", 0.8, (260, 140, 120, 160))


def test_object_at_the_face_in_two_runs_blocks():
    det = FakeDetector()
    g, clock = guard(0.9, det)
    det.next = [PHONE_AT_FACE]
    assert not run(g, clock, 1).spoof            # one sighting is not enough
    verdict = run(g, clock, 1)
    assert verdict.kind == "object" and verdict.label == "phone"


def test_single_flicker_does_not_block():
    det = FakeDetector()
    g, clock = guard(0.9, det)
    det.next = [PHONE_AT_FACE]
    run(g, clock, 1)
    det.next = []
    assert not run(g, clock, 20).spoof


def test_weak_detections_are_ignored():
    det = FakeDetector()
    g, clock = guard(0.9, det)
    det.next = [Detection("phone", 0.4, PHONE_AT_FACE.box)]
    assert not run(g, clock, 20).spoof


def test_laptop_or_tv_in_the_background_is_ignored():
    det = FakeDetector()
    g, clock = guard(0.9, det)
    det.next = [Detection("screen", 0.9, (20, 20, 600, 440)),    # behind the student, huge
                Detection("laptop", 0.9, (500, 380, 120, 90))]   # on a desk, away from face
    assert not run(g, clock, 20).spoof


def test_noisy_classes_are_not_watched():
    assert set(WATCHED.values()) == {"phone", "laptop", "screen"}


# ---- supporting checks and helpers -------------------------------------------------------------

def test_crop_is_square_at_the_edge_of_the_picture():
    xs = np.tile(np.arange(640, dtype=np.float32), (480, 1))
    ys = np.tile(np.arange(480, dtype=np.float32)[:, None], (1, 640))
    edge_face = np.array([0, 300, 100, 100], np.float32)          # at the left / bottom edge
    cx, cy = crop_face(xs, edge_face), crop_face(ys, edge_face)
    width = cx[:, -1].mean() - cx[:, 0].mean()
    height = cy[-1].mean() - cy[0].mean()
    assert cx.shape == (80, 80) and abs(width - height) < 5     # not squashed


def test_photo_edges_found_around_a_face_but_not_on_a_plain_wall():
    img = FRAME.copy()
    cv2.rectangle(img, (220, 100), (420, 320), (20, 20, 20), 3)
    assert photo_frame_around(img, FACE_BOX)
    assert not photo_frame_around(FRAME, FACE_BOX)


def test_edges_alone_do_not_block_a_live_face():
    img = FRAME.copy()
    cv2.rectangle(img, (220, 100), (420, 320), (20, 20, 20), 3)   # a door or poster frame
    g, clock = guard(0.9)
    for _ in range(20):
        clock.t += 0.1
        assert not g.check(img, [FACE], clock.t).spoof


def test_flat_picture_fits_one_homography_and_a_head_does_not():
    first = face_points()
    H = np.array([[0.95, 0.05, 10], [-0.03, 1.0, 5], [1e-4, 0, 1]])
    flat = cv2.perspectiveTransform(first.reshape(-1, 1, 2).astype(np.float64), H).reshape(-1, 2)
    assert is_flat(first, flat, 100)
    head = first.copy()
    head[4] += (12, 0)                                            # nose moves more (depth)
    head[9] += (-8, 0)
    assert not is_flat(first, head, 100)


MODELS = Path(__file__).resolve().parents[1] / "models"


@pytest.mark.skipif(not (MODELS / "MiniFASNetV2.onnx").exists(), reason="models not downloaded")
def test_anti_spoof_model_loads_and_scores():
    from backend.face.antispoof import AntiSpoofNet
    score = AntiSpoofNet(MODELS).real_probability(FRAME, FACE)
    assert 0.0 <= score <= 1.0


@pytest.mark.skipif(not (MODELS / "object_detection_nanodet_2022nov.onnx").exists(),
                    reason="models not downloaded")
def test_object_model_loads_and_finds_nothing_on_a_wall():
    from backend.face.objects import ObjectDetector
    assert ObjectDetector(MODELS, 0.55).detect(FRAME) == []


# ---- phone training -----------------------------------------------------------------------

class FloorDetector(FakeDetector):
    """Returns only detections at or above the floor it is asked for."""

    def detect(self, _frame, min_score=None):
        floor = 0.3 if min_score is None else min_score   # the kiosk detector floor
        return [d for d in self.next if d.score >= floor]


def test_phone_score_measures_weak_phones_at_the_face_only():
    det = FloorDetector()
    g, _ = guard(0.9, det)
    det.next = [Detection("phone", 0.2, PHONE_AT_FACE.box),
                Detection("phone", 0.9, (520, 380, 80, 80))]      # on a desk, away from the face
    assert g.phone_score(FRAME, FACE) == 0.2
    det.next = []
    assert g.phone_score(FRAME, FACE) == 0.0


def test_kiosk_phone_threshold_decides_what_blocks():
    det = FloorDetector()
    clock = Clock()
    objects = ObjectWatcher(det, threaded=False, clock=clock)
    level = {"value": 0.55}
    g = SpoofGuard(AntiSpoofConfig(object_every_n=1), FakeNet(0.9), objects, clock=clock,
                   object_threshold=lambda: level["value"])
    det.next = [Detection("phone", 0.4, PHONE_AT_FACE.box)]
    assert not run(g, clock, 5).spoof                 # 0.4 is below this kiosk's 0.55
    level["value"] = 0.35                             # trained on a dim webcam
    assert run(g, clock, 2).kind == "object"
