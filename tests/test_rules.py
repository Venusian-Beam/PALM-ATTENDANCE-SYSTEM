"""The shared rules: building sessions from events, and what a scan should do."""
from datetime import date, datetime, time

import pytest

from backend.face.blink import BlinkCounter
from backend.face.gestures import GestureDetector
from backend.face.signals import pitch_from_landmarks, yaw_from_landmarks
from backend.rules import (FLAG_NO_MORNING_SIGNIN, FLAG_NO_SIGNOUT, IN, OUT, STATUS_APPLIED,
                           STATUS_DUPLICATE_IN, STATUS_NOT_IN, Action, Event, Session,
                           build_sessions, decide)

CUTOFF = time(17, 30)
DAY = date(2026, 10, 2)


def at(h, m, day=DAY):
    return datetime(day.year, day.month, day.day, h, m)


def ev(id, kind, h, m, student=1, device="Gate A", cutoff=None, day=DAY):
    return Event(id, student, kind, at(h, m, day), cutoff, device)


# ---- build_sessions ----------------------------------------------------------------

def test_in_then_out_makes_one_session():
    sessions, status = build_sessions([ev("a", IN, 7, 40), ev("b", OUT, 14, 0, device="Gate B")],
                                      CUTOFF, DAY)
    [s] = sessions
    assert (s.clock_in, s.clock_out, s.closed) == ("07:40:00", "14:00:00", True)
    assert (s.in_device, s.out_device) == ("Gate A", "Gate B")
    assert status == {"a": STATUS_APPLIED, "b": STATUS_APPLIED}


def test_order_of_arrival_does_not_matter():
    events = [ev("c", IN, 13, 0), ev("b", OUT, 12, 0), ev("a", IN, 7, 40)]
    sessions, _ = build_sessions(events, CUTOFF, DAY)
    assert [(s.clock_in, s.clock_out) for s in sessions] == [("07:40:00", "12:00:00"),
                                                            ("13:00:00", None)]


def test_duplicate_in_from_two_gates_is_ignored():
    sessions, status = build_sessions([ev("a", IN, 7, 40), ev("b", IN, 7, 41, device="Gate B")],
                                      CUTOFF, DAY)
    assert len(sessions) == 1 and status["b"] == STATUS_DUPLICATE_IN


def test_out_without_in_is_ignored():
    sessions, status = build_sessions([ev("a", OUT, 9, 0)], CUTOFF, DAY)
    assert sessions == [] and status["a"] == STATUS_NOT_IN


def test_first_in_after_cutoff_is_flagged_only_once():
    sessions, _ = build_sessions([ev("a", IN, 17, 40), ev("b", OUT, 18, 0),
                                  ev("c", IN, 18, 30)], CUTOFF, DAY)
    assert [s.in_flag for s in sessions] == [FLAG_NO_MORNING_SIGNIN, None]


def test_event_cutoff_overrides_default():
    sessions, _ = build_sessions([ev("a", IN, 16, 0, cutoff="15:00")], CUTOFF, DAY)
    assert sessions[0].in_flag == FLAG_NO_MORNING_SIGNIN


def test_open_session_on_past_day_gets_no_signout():
    sessions, _ = build_sessions([ev("a", IN, 7, 40)], CUTOFF, date(2026, 10, 3))
    [s] = sessions
    assert s.closed and s.clock_out is None and s.out_flag == FLAG_NO_SIGNOUT
    today, _ = build_sessions([ev("a", IN, 7, 40)], CUTOFF, DAY)
    assert not today[0].closed


def test_students_and_days_are_separate():
    sessions, _ = build_sessions([ev("a", IN, 7, 40, student=1), ev("b", IN, 7, 45, student=2),
                                  ev("c", IN, 7, 50, student=1, day=date(2026, 10, 1))],
                                 CUTOFF, DAY)
    assert len(sessions) == 3


# ---- decide -------------------------------------------------------------------------

def session(clock_in, closed=False, clock_out=None):
    return Session(1, DAY.isoformat(), clock_in, clock_out, None, None, closed, "x")


def rule(sessions, now):
    return decide(sessions, now, CUTOFF, min_session_minutes=2, signout_blinks=3)


def test_first_scan_before_cutoff_clocks_in():
    d = rule([], at(7, 40))
    assert d.action is Action.CLOCK_IN and d.blinks == 1 and d.event_kind == IN


def test_first_scan_after_cutoff_flagged():
    assert rule([], at(17, 40)).flag == FLAG_NO_MORNING_SIGNIN


def test_clocked_in_before_cutoff_three_blink_signout():
    d = rule([session("07:40:00")], at(14, 0))
    assert d.action is Action.SIGNOUT_OFFER and d.blinks == 3 and d.event_kind == OUT


def test_clocked_in_after_cutoff_clocks_out():
    d = rule([session("07:40:00")], at(17, 31))
    assert d.action is Action.CLOCK_OUT and d.event_kind == OUT


def test_just_clocked_in_after_cutoff_stays():
    assert rule([session("17:40:00")], at(17, 41)).action is Action.STAY
    assert rule([session("17:40:00")], at(17, 43)).action is Action.CLOCK_OUT


def test_after_clock_out_asks_if_returning():
    d = rule([session("07:40:00", True, "12:00:00")], at(13, 0))
    assert d.action is Action.ASK_RETURNING and d.event_kind == IN


# ---- blinks + gestures ----------------------------------------------------------------

def test_blink_counter():
    c = BlinkCounter(0.5, 0.25)
    for score in (0.1, 0.6, 0.4, 0.1, 0.9, 0.2, 0.05):
        c.update(score)
    assert c.count == 2


def feed(det, readings, step=0.1):
    t, result = 0.0, None
    for yaw, pitch in readings:
        t += step
        result = det.update(yaw, pitch, t) or result
    return result


STILL = [(0.0, 0.0)] * 3


def test_nod_is_yes_and_shake_is_no():
    assert feed(GestureDetector(), STILL + [(0.01, 0.05), (0.0, 0.14), (0.02, 0.15),
                                            (0.0, 0.03)]) == "yes"
    assert feed(GestureDetector(), STILL + [(0.3, 0.02), (0.1, 0.0), (-0.3, 0.01)]) == "no"


def test_still_or_one_turn_is_nothing():
    assert feed(GestureDetector(), STILL * 10) is None
    assert feed(GestureDetector(), STILL + [(0.3, 0.0)] * 5) is None


def test_pose_helpers():
    assert yaw_from_landmarks(0.5, 0.3, 0.7) == pytest.approx(0)
    assert yaw_from_landmarks(0.6, 0.3, 0.7) > 0
    assert pitch_from_landmarks(0.6, 0.3, 0.7) > 0
