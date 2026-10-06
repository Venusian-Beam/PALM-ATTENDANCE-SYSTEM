"""Counts blinks from a stream of eye-closure scores."""
from __future__ import annotations


class BlinkCounter:
    """A blink is the eyes closing (score >= closed) and then opening (score <= open)."""

    def __init__(self, closed: float = 0.5, open_: float = 0.25):
        self._closed = closed
        self._open = open_
        self._eyes_closed = False
        self.count = 0

    @property
    def eyes_open(self) -> bool:
        return not self._eyes_closed

    def update(self, score: float) -> bool:
        """Feed one frame's blink score. Returns True when a blink has just completed."""
        if not self._eyes_closed and score >= self._closed:
            self._eyes_closed = True
        elif self._eyes_closed and score <= self._open:
            self._eyes_closed = False
            self.count += 1
            return True
        return False
