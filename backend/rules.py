"""The attendance rules, shared by the server and every kiosk.

Kiosks record events ("IN" / "OUT"). Sessions are always *derived* from events, so
late, duplicated or out-of-order uploads from offline kiosks still give one correct
answer, and the server and kiosks always agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum, auto
from typing import Iterable

IN = "IN"
OUT = "OUT"

FLAG_NO_MORNING_SIGNIN = "No morning sign-in"
FLAG_NO_SIGNOUT = "No sign-out"

STATUS_APPLIED = "applied"
STATUS_DUPLICATE_IN = "ignored: already clocked in"
STATUS_NOT_IN = "ignored: not clocked in"


@dataclass(frozen=True)
class Event:
    id: str
    student_id: int
    kind: str          # IN or OUT
    ts: datetime
    cutoff: str | None = None   # clock-out time in force when the event was recorded
    device: str | None = None


@dataclass
class Session:
    student_id: int
    date: str
    clock_in: str            # HH:MM:SS
    clock_out: str | None
    in_flag: str | None
    out_flag: str | None
    closed: bool
    in_event: str
    out_event: str | None = None
    in_device: str | None = None
    out_device: str | None = None

    def clock_in_at(self) -> datetime:
        return datetime.fromisoformat(f"{self.date}T{self.clock_in}")


def parse_hhmm(value: str) -> time:
    hours, minutes = str(value).strip().split(":")
    return time(int(hours), int(minutes))


def build_sessions(events: Iterable[Event], default_cutoff: time,
                   today: date) -> tuple[list[Session], dict[str, str]]:
    """Turn events into sessions. Returns (sessions, status of each event id)."""
    groups: dict[tuple[int, str], list[Event]] = {}
    for ev in events:
        groups.setdefault((ev.student_id, ev.ts.date().isoformat()), []).append(ev)

    sessions: list[Session] = []
    statuses: dict[str, str] = {}
    for (student_id, day), evs in sorted(groups.items()):
        evs.sort(key=lambda e: (e.ts, e.id))
        open_session: Session | None = None
        day_sessions: list[Session] = []
        for ev in evs:
            clock = ev.ts.strftime("%H:%M:%S")
            if ev.kind == IN:
                if open_session is not None:
                    statuses[ev.id] = STATUS_DUPLICATE_IN
                    continue
                cutoff = default_cutoff
                if ev.cutoff:
                    try:
                        cutoff = parse_hhmm(ev.cutoff)
                    except (ValueError, TypeError):
                        pass
                flag = (FLAG_NO_MORNING_SIGNIN
                        if not day_sessions and ev.ts.time() >= cutoff else None)
                open_session = Session(student_id, day, clock, None, flag, None, False,
                                       ev.id, in_device=ev.device)
                day_sessions.append(open_session)
                statuses[ev.id] = STATUS_APPLIED
            elif ev.kind == OUT:
                if open_session is None:
                    statuses[ev.id] = STATUS_NOT_IN
                    continue
                open_session.clock_out = clock
                open_session.closed = True
                open_session.out_event = ev.id
                open_session.out_device = ev.device
                open_session = None
                statuses[ev.id] = STATUS_APPLIED
        if open_session is not None and date.fromisoformat(day) < today:
            open_session.closed = True
            open_session.out_flag = FLAG_NO_SIGNOUT
        sessions.extend(day_sessions)
    return sessions, statuses


# ---- what a scan should do -------------------------------------------------------

class Action(Enum):
    CLOCK_IN = auto()        # no session yet today
    SIGNOUT_OFFER = auto()   # clocked in, before the cut-off: blink 3 times to sign out
    CLOCK_OUT = auto()       # clocked in, after the cut-off
    STAY = auto()            # clocked in moments ago, after the cut-off: do nothing
    ASK_RETURNING = auto()   # already clocked out today


@dataclass
class Decision:
    action: Action
    blinks: int = 1
    open_session: Session | None = None
    flag: str | None = None

    @property
    def event_kind(self) -> str:
        return OUT if self.action in (Action.CLOCK_OUT, Action.SIGNOUT_OFFER) else IN


def decide(sessions: list[Session], now: datetime, cutoff: time,
           min_session_minutes: float, signout_blinks: int) -> Decision:
    """What a recognised student's scan should do, given today's sessions."""
    open_session = next((s for s in sessions if not s.closed), None)
    after_cutoff = now.time() >= cutoff
    if open_session:
        if not after_cutoff:
            return Decision(Action.SIGNOUT_OFFER, signout_blinks, open_session)
        minutes_open = (now - open_session.clock_in_at()).total_seconds() / 60
        if minutes_open >= min_session_minutes:
            return Decision(Action.CLOCK_OUT, 1, open_session)
        return Decision(Action.STAY, 0, open_session)
    if not sessions:
        return Decision(Action.CLOCK_IN, 1,
                        flag=FLAG_NO_MORNING_SIGNIN if after_cutoff else None)
    return Decision(Action.ASK_RETURNING, 1)
