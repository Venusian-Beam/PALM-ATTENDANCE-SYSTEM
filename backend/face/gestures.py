"""Detects a nod (yes) or head shake (no) from a stream of head-pose readings."""
from __future__ import annotations

from collections import deque

BASELINE_FRAMES = 3


class GestureDetector:
    """Movements are measured relative to the pose in the first few frames.

    Nod:   the head tilts down (or up) past the threshold and comes back,
           without much side-to-side movement.
    Shake: the head turns past the threshold to one side and then the other,
           without much up-and-down movement.
    """

    def __init__(self, nod_threshold: float = 0.10, shake_threshold: float = 0.25,
                 window_s: float = 2.0):
        self._nod = nod_threshold
        self._shake = shake_threshold
        self._window = window_s
        self._baseline: list[tuple[float, float]] = []
        self._samples: deque[tuple[float, float, float]] = deque()

    def reset(self) -> None:
        self._baseline.clear()
        self._samples.clear()

    def update(self, yaw: float, pitch: float, now: float) -> str | None:
        """Returns 'yes' for a nod, 'no' for a shake, or None."""
        if len(self._baseline) < BASELINE_FRAMES:
            self._baseline.append((yaw, pitch))
            return None
        base_yaw = sum(b[0] for b in self._baseline) / len(self._baseline)
        base_pitch = sum(b[1] for b in self._baseline) / len(self._baseline)
        self._samples.append((now, yaw - base_yaw, pitch - base_pitch))
        while self._samples and now - self._samples[0][0] > self._window:
            self._samples.popleft()

        if self._is_shake():
            return "no"
        if self._is_nod():
            return "yes"
        return None

    def _is_nod(self) -> bool:
        if max(abs(s[1]) for s in self._samples) >= self._shake * 0.6:
            return False
        moved = False
        for _, _, dp in self._samples:
            if abs(dp) >= self._nod:
                moved = True
            elif moved and abs(dp) <= self._nod * 0.4:
                return True  # went past the threshold and came back
        return False

    def _is_shake(self) -> bool:
        if max(abs(s[2]) for s in self._samples) >= self._nod * 1.5:
            return False
        sides: list[int] = []
        for _, dy, _ in self._samples:
            if abs(dy) >= self._shake:
                side = 1 if dy > 0 else -1
                if not sides or sides[-1] != side:
                    sides.append(side)
        return len(sides) >= 2
