"""Frame quality on synthetic images: camera movement and motion blur."""
import cv2
import numpy as np

from backend.config import StabilityConfig
from backend.face.quality import QualityMeter


def scene(seed=0):
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.integers(0, 255, (480, 640), dtype=np.uint8), (0, 0), 2)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


FACE = np.array([220, 140, 200, 200] + [0] * 10 + [0.99], dtype=np.float32)


def test_still_camera_is_steady():
    meter = QualityMeter(StabilityConfig())
    img = scene()
    meter.assess(img, None)
    q = meter.assess(img, None)
    assert q.steady and abs(q.shift[0]) < 1 and abs(q.shift[1]) < 1


def test_bump_is_detected_with_its_direction():
    meter = QualityMeter(StabilityConfig())
    img = scene()
    meter.assess(img, None)
    q = meter.assess(np.roll(img, 40, axis=1), None)
    assert not q.steady
    assert 30 < q.shift[0] < 50 and abs(q.shift[1]) < 5


def test_motion_blur_is_not_sharp_after_sharp_history():
    meter = QualityMeter(StabilityConfig())
    img = scene()
    for _ in range(10):
        assert meter.assess(img, FACE).sharp
    smeared = cv2.filter2D(img, -1, np.full((1, 25), 1 / 25, np.float32))  # horizontal blur
    assert not meter.assess(smeared, FACE).sharp
    assert meter.assess(img, FACE).usable
