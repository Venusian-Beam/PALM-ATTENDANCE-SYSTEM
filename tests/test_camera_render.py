"""The kiosk screen shows the camera's own picture."""
import cv2
import numpy as np

from backend.camera import render_jpeg
from backend.controller import ViewState


def test_screen_shows_the_camera_picture_as_it_is():
    frame = np.full((48, 64, 3), 40, np.uint8)          # a dark picture
    jpeg = render_jpeg(frame, ViewState("Hi"), quality=100)
    shown = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert abs(float(shown.mean()) - 40) < 2             # not brightened


def test_blocked_screen_is_blurred():
    frame = np.zeros((64, 64, 3), np.uint8)
    frame[:, ::2] = 255                                  # sharp stripes
    jpeg = render_jpeg(frame, ViewState("Foreign object detected", blur=True), quality=100)
    shown = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert shown.std() < 20                              # stripes gone
