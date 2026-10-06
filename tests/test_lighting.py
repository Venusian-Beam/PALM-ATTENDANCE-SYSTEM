"""Backlight: measuring it and brightening dark faces, on synthetic pictures."""
import cv2
import numpy as np

from backend.config import LightingConfig
from backend.face.lighting import Lighting

FACE = np.array([220, 120, 200, 240] + [0] * 10 + [0.99], dtype=np.float32)


def picture(background, face_level, seed=0):
    """A flat background with a textured 'face' rectangle in the middle."""
    rng = np.random.default_rng(seed)
    img = np.full((480, 640), background, np.uint8)
    texture = rng.normal(face_level, 12, (240, 200)).clip(0, 255).astype(np.uint8)
    img[120:360, 220:420] = texture
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def face_mean(frame):
    return float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[168:312, 260:380].mean())


def test_window_behind_a_dark_face_is_backlit():
    light = Lighting(LightingConfig())
    info = light.assess(picture(245, 35), FACE)
    assert info.dark and info.backlit
    assert info.face_mean < 50 and info.background_mean > 200


def test_dark_room_is_dark_but_not_backlit():
    info = Lighting(LightingConfig()).assess(picture(40, 35), FACE)
    assert info.dark and not info.backlit


def test_enhance_brings_a_dark_face_to_the_target():
    light = Lighting(LightingConfig())
    frame = picture(245, 35)
    out = light.enhance(frame, light.assess(frame, FACE))
    assert abs(face_mean(out) - 130) < 30


def test_normal_light_is_left_untouched():
    light = Lighting(LightingConfig())
    frame = picture(120, 140)
    assert light.enhance(frame, light.assess(frame, FACE)) is frame
    assert light.enhance(frame, None) is frame


def test_backlit_scene_check_and_rescue():
    light = Lighting(LightingConfig())
    backlit, even = picture(250, 30), picture(120, 130)
    assert light.maybe_backlit(backlit)
    assert not light.maybe_backlit(even)
    before = cv2.cvtColor(backlit, cv2.COLOR_BGR2GRAY)[168:312, 260:380]
    after = cv2.cvtColor(light.rescue(backlit), cv2.COLOR_BGR2GRAY)[168:312, 260:380]
    assert after.mean() > before.mean() + 40 and after.std() > before.std()
