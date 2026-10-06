"""Turns camera frames into clock-in / clock-out events. Contains no web code.

Attendance:   IDLE -> BLINK -> CONFIRM -> (QUESTION) -> RESULT -> IDLE
Registration: REG_WAIT -> REG_BLINK -> REG_CAPTURE -> RESULT

Decisions come from backend/rules.py using the kiosk's local store, so they work
offline. Results are recorded as IN / OUT events that the sync client uploads.

Bumps and shaking: each frame gets a quality score (backend/face/quality.py). Blurry or
shaken frames are skipped rather than counted against the student, the check's timers
pause while that lasts (up to `stability.max_grace_s`), and if the picture jumps the
student's face is re-confirmed on the next sharp frame instead of cancelling the check.

Bright light behind the student: dark faces are brightened (backend/face/lighting.py)
before detection, recognition and blink tracking; if no face is found in a backlit
picture, detection is retried on a brightened copy. The brightening happens in memory
only: the webcam keeps its own settings and the screen shows the camera's picture.

Photos, phones and other objects (backend/face/spoof.py): any frame where one is seen
puts the kiosk in BLOCKED: the attempt in progress is abandoned (nothing is recorded),
the picture is blurred, the student is asked to remove it, and the IT officer is told.
A name is only shown after several frames have passed the live-face check.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from enum import Enum, auto
from typing import Any, Callable

import numpy as np

from backend.face.blink import BlinkCounter
from backend.face.engine import Box, best_match, face_box, face_width, normalize
from backend.face.gestures import GestureDetector
from backend.face.lighting import LightInfo
from backend.face.quality import GOOD, FrameQuality
from backend.face.spoof import CLEAN, SpoofVerdict
from backend.rules import FLAG_NO_MORNING_SIGNIN, Action, Decision, decide
from backend.security.guard import GuardResult

# submit_registration(job_id, samples or None, error or None) -> (ok, message)
SubmitRegistration = Callable[[int, list[np.ndarray] | None, str | None], tuple[bool, str]]


@dataclass
class ViewState:
    headline: str
    detail: str = ""
    tone: str = "idle"  # idle | info | success | warn | error
    boxes: list[Box] = field(default_factory=list)
    progress: float | None = None
    blinks: dict[str, int] | None = None
    question: str | None = None
    blur: bool = False  # a photo / phone / object is held up: blur the camera picture

    def to_dict(self) -> dict[str, Any]:
        return {"headline": self.headline, "detail": self.detail, "tone": self.tone,
                "progress": self.progress, "blinks": self.blinks, "question": self.question,
                "blur": self.blur}


@dataclass
class RegisterRequest:
    job_id: int
    student_no: str
    full_name: str
    student_id: int | None = None  # set when re-capturing an existing student's face


class RegistrationBusyError(Exception):
    pass


class Mode(Enum):
    IDLE = auto()
    BLINK = auto()
    CONFIRM = auto()
    QUESTION = auto()
    RESULT = auto()
    REG_WAIT = auto()
    REG_BLINK = auto()
    REG_CAPTURE = auto()
    BLOCKED = auto()   # a photo, phone or other object is being shown
    TUNING = auto()    # the IT officer is tuning this kiosk's photo detection


def _hhmm(t: str) -> str:
    return t[:5]


class Controller:
    RESULT_SECONDS = 3.5
    NOTICE_COOLDOWN_S = 5.0
    MISS_FRAMES_BEFORE_REJECT = 6
    CONFIRM_SECONDS = 3.0
    FRONTAL_YAW = 0.2
    REG_BLINK_TIMEOUT_S = 10.0
    REG_TIMEOUT_S = 90.0
    REG_MAX_ATTEMPTS = 3
    CAPTURE_INTERVAL_S = 0.35
    LIVE_FRAMES_NEEDED = 3   # live-face frames before a name is shown

    def __init__(self, cfg, store, engine, extractor,
                 guard_check: Callable[[], GuardResult],
                 cutoff: Callable[[], dtime],
                 submit_registration: SubmitRegistration,
                 device_name: Callable[[], str | None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = datetime.now,
                 quality=None, lighting=None, spoof=None,
                 report_alert: Callable[[dict[str, Any]], None] | None = None,
                 submit_tuning: Callable[[int, list[int]], tuple[bool, str]] | None = None):
        self._cfg = cfg
        self._store = store
        self._engine = engine
        self._extractor = extractor
        self._guard_check = guard_check
        self._cutoff = cutoff
        self._submit = submit_registration
        self._device_name = device_name
        self._clock = clock
        self._now = now
        self._quality = quality  # QualityMeter, or None to treat every frame as usable
        self._lighting = lighting  # Lighting, or None to use frames as they come
        self._spoof = spoof        # SpoofGuard, or None to skip photo/object checks
        self._report_alert = report_alert
        self._submit_tuning = submit_tuning
        self._lock = threading.RLock()
        self._gallery = store.load_embeddings()
        self._gallery_version = store.roster_version
        self._mode = Mode.IDLE
        self._result: ViewState | None = None
        self._result_until = 0.0
        self._cooldowns: dict[int, float] = {}
        self._miss_streak = 0
        # current attempt
        self._student = None
        self._decision: Decision | None = None
        self._blinks: BlinkCounter | None = None
        self._deadline = 0.0
        self._prev_box: Box | None = None
        self._last_seen = 0.0
        self._gestures: GestureDetector | None = None
        self._answer: str | None = None
        # frame quality (bumps / shaking)
        self._q: FrameQuality = GOOD
        self._last_t: float | None = None
        self._unusable_since: float | None = None
        self._grace_used = 0.0
        self._reverify_until: float | None = None
        # bright light behind the student
        self._light: LightInfo | None = None
        self._backlit_since: float | None = None
        self._scene_backlit = False
        self._rescue_turn = False
        self._face_present = False
        self._scan_backlit_counted = False
        # photos, phones and other objects
        self._clean_since: float | None = None
        self._tuning: dict[str, Any] | None = None
        # registration
        self._reg: RegisterRequest | None = None
        self._reg_deadline = 0.0
        self._reg_attempts = 0
        self._reg_message = ""
        self._samples: list[np.ndarray] = []
        self._last_capture = 0.0

    @property
    def threshold(self) -> float:
        return self._cfg.face.match_threshold

    def _refresh_gallery(self) -> None:
        if self._store.roster_version != self._gallery_version:
            self._gallery = self._store.load_embeddings()
            self._gallery_version = self._store.roster_version

    def reset(self) -> None:
        """Abandon the attendance attempt in progress (used when the system locks)."""
        with self._lock:
            if self._reg is None:
                self._mode = Mode.IDLE
            self._clear_attempt()

    # ---- called from the web / sync threads ------------------------------------------

    def answer(self, value: str) -> bool:
        """An on-screen Yes/No press. Returns False if no question is open."""
        with self._lock:
            if self._mode is not Mode.QUESTION or value not in ("yes", "no"):
                return False
            self._answer = value
            return True

    def start_registration(self, request: RegisterRequest) -> None:
        with self._lock:
            if self._reg is not None:
                raise RegistrationBusyError("A registration is already in progress.")
            self._clear_attempt()
            self._reg = request
            self._reg_deadline = self._clock() + self.REG_TIMEOUT_S
            self._reg_attempts = 0
            self._reg_message = "Look at the camera"
            self._samples = []
            self._mode = Mode.REG_WAIT

    def cancel_registration(self, job_id: int | None = None) -> None:
        """Stop the registration (only if it is `job_id`, when given). No server report."""
        with self._lock:
            if self._reg is not None and (job_id is None or self._reg.job_id == job_id):
                self._end_registration()
                self._mode = Mode.IDLE

    def registration_status(self) -> dict[str, Any] | None:
        with self._lock:
            if self._reg is None:
                return None
            n = self._cfg.face.enroll_samples
            return {"job_id": self._reg.job_id, "active": True, "message": self._reg_message,
                    "progress": (len(self._samples) / n
                                 if self._mode is Mode.REG_CAPTURE else None)}

    def start_tuning(self, job_id: int, phase: str, seconds: float) -> None:
        """Kiosk tuning: collect live-face scores for real faces or for photos/phones."""
        with self._lock:
            if self._reg is not None:
                raise RegistrationBusyError("A registration is in progress.")
            if self._tuning is not None and self._tuning["id"] == job_id:
                return
            self._clear_attempt()
            self._tuning = {"id": job_id, "phase": phase, "seconds": seconds,
                            "until": self._clock() + seconds, "scores": []}
            self._mode = Mode.TUNING

    def tuning_status(self) -> dict[str, Any] | None:
        with self._lock:
            if self._tuning is None:
                return None
            return {"job_id": self._tuning["id"], "samples": len(self._tuning["scores"])}

    # ---- frame processing (camera thread) --------------------------------------------

    def process(self, frame: np.ndarray) -> ViewState:
        with self._lock:
            t = self._clock()
            if self._mode is Mode.RESULT:
                if t < self._result_until and self._result is not None:
                    return self._result
                self._mode = Mode.REG_WAIT if self._reg else Mode.IDLE

            raw = frame  # the camera's own picture, for the photo/phone checks
            faces, frame = self._find_faces(frame, t)
            boxes = [face_box(f) for f in faces]
            self._measure(frame, faces, t)
            if self._mode is Mode.TUNING:
                return self._process_tuning(raw, faces, boxes, t)
            # Never the brightened picture: brightening a dark face amplifies sensor noise,
            # which the live-face model reads as paper or screen texture (false alarms).
            verdict = (self._spoof.check(raw, faces, t, self._q.usable)
                       if self._spoof is not None else CLEAN)
            if verdict.spoof or self._mode is Mode.BLOCKED:
                blocked = self._process_blocked(frame, faces, verdict, t)
                if blocked is not None:
                    return blocked
            if self._reg is not None:
                state = self._process_registration(frame, faces, boxes, t)
            else:
                handler = {
                    Mode.BLINK: self._process_blink,
                    Mode.CONFIRM: self._process_confirm,
                    Mode.QUESTION: self._process_question,
                }.get(self._mode, self._process_idle)
                state = handler(frame, faces, boxes, t)
            return state

    # ---- bright light behind the student ------------------------------------------------

    def _find_faces(self, frame, t: float):
        """Detects faces; with a bright background, retries on a brightened copy.

        Returns the faces and the frame to use from here on (brightened if the face is
        dark), so recognition and blink tracking agree. Nothing here is shown on screen.
        """
        faces = self._engine.detect(frame)
        work = frame
        if self._lighting is not None and not faces:
            self._rescue_turn = not self._rescue_turn
            if self._rescue_turn:  # every second frame, to save time
                self._scene_backlit = self._lighting.maybe_backlit(frame)
                if self._scene_backlit:
                    rescued = self._lighting.rescue(frame)
                    faces = self._engine.detect(rescued)
                    if faces:
                        work = rescued
        info = None
        if self._lighting is not None and len(faces) == 1:
            info = self._lighting.assess(frame, faces[0])
            self._scene_backlit = info.backlit
            if work is frame:
                work = self._lighting.enhance(frame, info)
        elif faces:
            self._scene_backlit = False
        self._light = info
        self._track_backlight(bool(faces), t)
        return faces, work

    # ---- photos, phones and other objects ---------------------------------------------

    def _process_blocked(self, frame, faces, verdict: SpoofVerdict, t: float) -> ViewState | None:
        """Returns the blocked screen, or None once the picture has been clean long enough."""
        if verdict.spoof:
            self._clean_since = None
            if self._mode is not Mode.BLOCKED:
                self._enter_blocked(frame, faces, verdict, t)
            return self._blocked_view()
        if self._clean_since is None:
            self._clean_since = t
        if t - self._clean_since < self._cfg.antispoof.clean_for_s:
            return self._blocked_view()
        self._mode = Mode.REG_WAIT if self._reg else Mode.IDLE
        self._spoof.reset()
        return None

    def _enter_blocked(self, frame, faces, verdict: SpoofVerdict, t: float) -> None:
        student = self._student
        if student is None and len(faces) == 1 and self._q.usable:
            sid, score = best_match(self._engine.embed(frame, faces[0]), *self._gallery)
            if sid is not None and score >= self.threshold:
                student = self._store.get_student(sid)
        if self._reg is not None:
            self._reg_failed("Foreign object detected.", t)
        self._clear_attempt()
        self._mode = Mode.BLOCKED
        self._clean_since = None
        if self._report_alert is not None:
            try:
                self._report_alert({
                    "kind": verdict.kind, "label": verdict.label, "reason": verdict.reason,
                    "student_id": student.id if student else None,
                    "student": f"{student.full_name} ({student.student_no})" if student else None,
                    "ts": self._now().isoformat(timespec="seconds"),
                })
            except Exception:
                pass  # reporting must never stop the block itself

    BLOCKED_MESSAGE = "Foreign object detected"

    def _blocked_view(self) -> ViewState:
        # One message for every case; the IT officer's log has the details.
        return ViewState(self.BLOCKED_MESSAGE, "", "error", [], blur=True)

    def _feed_shape(self, signals, faces, t: float) -> str | None:
        """Landmarks for the photo checks. Returns "genuine"/"fake" when a blink finished."""
        if self._spoof is not None and signals is not None and len(faces) == 1:
            return self._spoof.landmarks(signals.points, signals.yaw, signals.pitch,
                                         face_width(faces[0]), signals.blink, t)
        return None

    def _count_blink(self, signals, judged: str | None) -> bool:
        """True when a blink has just been completed. With photo checks on, only a genuine
        blink counts (eyes only, the rest of the face unchanged): a photo can't blink."""
        if signals is None or not self._q.steady or self._reverifying:
            return False
        if self._spoof is not None:
            if judged == "genuine":
                self._blinks.count += 1
                return True
            return False
        return self._blinks.update(signals.blink)

    # ---- kiosk tuning -------------------------------------------------------------------

    TUNING_PROMPTS = {
        "real": "Look at the camera normally",
        "spoof": "Hold up photos and phone screens showing faces",
        "phone": "Hold a phone up in front of your face",
        "clear": "Look at the camera. No phone.",
    }

    def _process_tuning(self, frame, faces, boxes, t: float) -> ViewState:
        """`frame` is the camera's own (not brightened) picture, as the checks see it."""
        job = self._tuning
        if self._spoof is not None and len(faces) == 1:
            if job["phase"] in ("phone", "clear"):
                # Phone training: the (slower) detector runs on every 3rd frame.
                job["frame"] = job.get("frame", 0) + 1
                if job["frame"] % 3 == 0:
                    job["scores"].append(self._spoof.phone_score(frame, faces[0]))
            else:
                job["scores"].append(self._spoof.score(frame, faces[0]))
        elapsed = job["seconds"] - (job["until"] - t)
        if t < job["until"]:
            prompt = self.TUNING_PROMPTS.get(job["phase"], "")
            return ViewState("Kiosk tuning", prompt, "info", boxes,
                             progress=min(1.0, max(0.0, elapsed / job["seconds"])))
        self._tuning = None
        self._mode = Mode.IDLE
        hist = np.histogram(np.asarray(job["scores"], dtype=np.float32), bins=50,
                            range=(0.0, 1.0))[0].astype(int).tolist()
        ok, message = False, "This kiosk cannot report tuning results."
        if self._submit_tuning is not None:
            try:
                ok, message = self._submit_tuning(job["id"], hist)
            except Exception as exc:
                ok, message = False, f"Could not reach the server ({exc})."
        return self._show(ViewState("Tuning step finished" if ok else "Tuning failed",
                                    message, "success" if ok else "error"), t)

    def _track_backlight(self, face_seen: bool, t: float) -> None:
        if self._scene_backlit:
            if self._backlit_since is None:
                self._backlit_since = t
        else:
            self._backlit_since = None
        # Daily numbers for the IT officer: one "scan" per face that appears.
        if face_seen and not self._face_present:
            self._count_scan(backlit=False)
            self._scan_backlit_counted = False
        self._face_present = face_seen
        if self._backlit_long(t) and not self._scan_backlit_counted:
            self._scan_backlit_counted = True
            self._count_scan(backlit=True)

    def _count_scan(self, backlit: bool) -> None:
        today = self._now().date().isoformat()
        if self._store.get("light_date") != today:
            self._store.set("light_date", today)
            self._store.set("light_scans", 0)
            self._store.set("light_backlit", 0)
        key = "light_backlit" if backlit else "light_scans"
        self._store.set(key, self._store.get_int(key) + 1)

    def _backlit_long(self, t: float) -> bool:
        return (self._backlit_since is not None
                and t - self._backlit_since >= self._cfg.lighting.hint_after_s)

    # ---- helpers ----------------------------------------------------------------------

    def _clear_attempt(self) -> None:
        self._student = None
        self._decision = None
        self._blinks = None
        self._prev_box = None
        if self._spoof is not None:
            self._spoof.end_attempt()
        self._gestures = None
        self._answer = None
        self._miss_streak = 0
        self._grace_used = 0.0
        self._reverify_until = None

    def _single_face(self, faces) -> tuple[np.ndarray | None, str]:
        if not faces:
            return None, ""
        if len(faces) > 1:
            return None, "One person at a time, please."
        if face_width(faces[0]) < self._cfg.face.min_face_px:
            return None, "Please come closer to the camera."
        return faces[0], ""

    def _continuous(self, box: Box, shift: tuple[float, float] = (0.0, 0.0)) -> bool:
        """True if this face box plausibly follows the previous one (no swap).

        `shift` is how far the whole picture moved (the camera was bumped); it is taken
        off the face's movement, so a jolt alone does not look like a different person.
        """
        prev, self._prev_box = self._prev_box, box
        if prev is None:
            return True
        px, py, pw, ph = prev
        x, y, w, h = box
        dx = abs((x + w / 2) - (px + pw / 2) - shift[0])
        dy = abs((y + h / 2) - (py + ph / 2) - shift[1])
        return dx <= 0.35 * pw and dy <= 0.35 * ph and 0.7 <= w / max(pw, 1) <= 1.4

    def _track(self, frame, faces, t: float) -> str | None:
        """Checks that the same single person is still in front of the camera.

        If the picture jumps (a hard bump), the check is not cancelled: the face is
        re-confirmed as the same person on the next sharp frame (see `_reverify`).
        """
        if len(faces) > 1:
            return "Only one person may be in front of the camera."
        if faces:
            self._last_seen = t
            jumped = not self._continuous(face_box(faces[0]), self._q.shift)
            if jumped and self._reverify_until is None:
                self._reverify_until = t + self._cfg.stability.reverify_window_s
            if self._reverify_until is not None:
                return self._reverify(frame, faces[0], t)
            return None
        if self._reverify_until is not None and t > self._reverify_until:
            return "Face changed during the check."
        if t - self._last_seen > self._cfg.liveness.face_lost_s:
            return "Face lost. Please stay in front of the camera."
        return None

    def _reverify(self, frame, face, t: float) -> str | None:
        """After a jump: wait for a sharp frame, then confirm it is still the same person."""
        if not self._q.usable:
            return "Face changed during the check." if t > self._reverify_until else None
        if self._reg is None:
            sid, score = best_match(self._engine.embed(frame, face), *self._gallery)
            if sid != self._student.id or score < self.threshold:
                return "Face changed during the check."
        elif self._mode is Mode.REG_BLINK:
            # No identity yet while registering: start the blink again for this face.
            # (Each photo taken later is compared with the first one.)
            self._blinks = BlinkCounter(self._cfg.liveness.blink_closed,
                                        self._cfg.liveness.blink_open)
        self._reverify_until = None
        return None

    @property
    def _reverifying(self) -> bool:
        return self._reverify_until is not None

    def _measure(self, frame, faces, t: float) -> None:
        """Scores this frame and pauses the check's timers while the picture is unusable."""
        face = faces[0] if len(faces) == 1 else None
        self._q = self._quality.assess(frame, face) if self._quality is not None else GOOD
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        if self._q.usable:
            self._unusable_since = None
            return
        if self._unusable_since is None:
            self._unusable_since = t
        timed = self._mode in (Mode.BLINK, Mode.CONFIRM, Mode.QUESTION,
                               Mode.REG_BLINK, Mode.REG_CAPTURE)
        extra = min(dt, self._cfg.stability.max_grace_s - self._grace_used)
        if timed and extra > 0:
            self._grace_used += extra
            self._deadline += extra
            self._last_seen += extra
            if self._reverify_until is not None:
                self._reverify_until += extra

    def _shaky(self, t: float) -> bool:
        """True once the picture has been unusable for longer than a quick bump."""
        return (self._unusable_since is not None
                and t - self._unusable_since >= self._cfg.stability.hint_after_s)

    BACKLIT_TIP = "Too much light behind you. Step closer, or turn away from the window."

    def _steady_hint(self, state: ViewState, t: float) -> ViewState:
        if state.question:
            return state
        if self._backlit_long(t):
            state.detail = self.BACKLIT_TIP
        elif self._shaky(t):
            state.detail = "Hold still…"
        return state

    def _begin_tracking(self, face, t: float) -> None:
        self._prev_box = None
        self._continuous(face_box(face))
        self._last_seen = t
        if self._spoof is not None:
            # A sign-out offer may simply be ignored, so not blinking there is no evidence.
            offer = self._decision is not None and self._decision.action is Action.SIGNOUT_OFFER
            self._spoof.new_attempt(t, expect_blink=not offer)

    def _show(self, state: ViewState, t: float, cooldown: float | None = None) -> ViewState:
        if cooldown and self._student is not None:
            self._cooldowns[self._student.id] = t + cooldown
        self._result = state
        self._result_until = t + self.RESULT_SECONDS
        self._mode = Mode.RESULT
        self._clear_attempt()
        return state

    def _fail(self, headline: str, detail: str, t: float) -> ViewState:
        return self._show(ViewState(headline, detail, "error"), t)

    # ---- attendance -----------------------------------------------------------------

    def _process_idle(self, frame, faces, boxes, t) -> ViewState:
        self._refresh_gallery()
        face, problem = self._single_face(faces)
        if face is None:
            self._miss_streak = 0
            if not problem and self._backlit_long(t):
                return ViewState("Too much light behind you",
                                 "Step closer, or turn away from the window.", "warn", boxes)
            return ViewState("Look at the camera to sign in", problem,
                             "warn" if problem else "idle", boxes)

        if not self._q.usable:
            # Blurred or shaken: don't judge the face on this frame, and don't count it.
            return self._steady_hint(ViewState("Look at the camera to sign in", "", "info", boxes),
                                     t)
        if self._spoof is not None and self._spoof.live_frames < self.LIVE_FRAMES_NEEDED:
            # Never name anyone until the face has passed the live-face check a few times.
            return self._steady_hint(ViewState("Look at the camera to sign in", "", "info", boxes),
                                     t)
        sid, score = best_match(self._engine.embed(frame, face), *self._gallery)
        student = (self._store.get_student(sid)
                   if sid is not None and score >= self.threshold else None)
        if student is None:
            self._miss_streak += 1
            if self._miss_streak < self.MISS_FRAMES_BEFORE_REJECT:
                return self._steady_hint(ViewState("Hold still…", "", "info", boxes), t)
            return self._fail("Face not recognised",
                              "If you are not registered, please see the IT officer.", t)
        self._miss_streak = 0

        if self._cooldowns.get(student.id, 0.0) > t:
            return ViewState(f"Hi {student.full_name}", "You're all set.", "info", boxes)

        now = self._now()
        cutoff = self._cutoff()
        decision = decide(self._store.sessions_for(student.id, now.date(), cutoff), now, cutoff,
                          self._cfg.attendance.min_session_minutes,
                          self._cfg.attendance.signout_blinks)
        self._student = student
        if decision.action is Action.STAY:
            since = _hhmm(decision.open_session.clock_in)
            return self._show(ViewState(f"Hi {student.full_name}",
                                        f"You're signed in since {since}.", "info"),
                              t, self.NOTICE_COOLDOWN_S)

        self._decision = decision
        self._blinks = BlinkCounter(self._cfg.liveness.blink_closed, self._cfg.liveness.blink_open)
        self._deadline = t + self._cfg.attendance.blink_timeout_s
        self._begin_tracking(face, t)
        self._mode = Mode.BLINK
        return self._blink_view(boxes)

    def _blink_view(self, boxes) -> ViewState:
        name = self._student.full_name
        d = self._decision
        blinks = {"count": self._blinks.count, "required": d.blinks}
        if d.action is Action.SIGNOUT_OFFER:
            since = _hhmm(d.open_session.clock_in)
            return ViewState(f"Hi {name}, you're signed in since {since}",
                             f"Blink {d.blinks} times to sign out", "info", boxes, blinks=blinks)
        prompt = {Action.CLOCK_IN: "Blink to clock in",
                  Action.CLOCK_OUT: "Blink to clock out"}.get(d.action, "Please blink")
        return ViewState(f"Hi {name}", prompt, "info", boxes, blinks=blinks)

    def _process_blink(self, frame, faces, boxes, t) -> ViewState:
        problem = self._track(frame, faces, t)
        if problem:
            return self._fail("Check cancelled", problem, t)
        signals = self._extractor.signals(frame, t)
        judged = self._feed_shape(signals, faces, t)
        # Blinks only count on steady frames from the confirmed person; the counter keeps
        # its state across skipped frames, so a blink begun before a bump still finishes.
        self._count_blink(signals, judged)
        if self._blinks.count >= self._decision.blinks:
            self._mode = Mode.CONFIRM
            self._deadline = t + self.CONFIRM_SECONDS
            return ViewState(f"Hi {self._student.full_name}", "Look at the camera", "info", boxes)
        if t > self._deadline:
            if self._decision.action is Action.SIGNOUT_OFFER:
                since = _hhmm(self._decision.open_session.clock_in)
                return self._show(ViewState(f"Hi {self._student.full_name}",
                                            f"You're still signed in (since {since}).", "info"),
                                  t, self.NOTICE_COOLDOWN_S)
            return self._fail("Liveness check failed",
                              "You didn't blink in time. Please try again.", t)
        return self._steady_hint(self._blink_view(boxes), t)

    def _process_confirm(self, frame, faces, boxes, t) -> ViewState:
        """After blinking: re-check, on an open-eyed frontal frame, that it's the same student."""
        student = self._student
        if t > self._deadline:
            return self._fail("Could not confirm your identity", "Please try again.", t)
        problem = self._track(frame, faces, t)
        if problem:
            return self._fail("Check cancelled", problem, t)
        face, _ = self._single_face(faces)
        if face is None or not self._q.usable or self._reverifying:
            return self._steady_hint(
                ViewState(f"Hi {student.full_name}", "Look at the camera", "info", boxes), t)
        signals = self._extractor.signals(frame, t)
        self._feed_shape(signals, faces, t)
        if (signals is None or abs(signals.yaw) > self.FRONTAL_YAW
                or signals.blink > self._cfg.liveness.blink_open
                or (self._spoof is not None and not self._spoof.live_now())):
            return ViewState(f"Hi {student.full_name}", "Look at the camera", "info", boxes)
        sid, score = best_match(self._engine.embed(frame, face), *self._gallery)
        if sid != student.id or score < self.threshold:
            return self._fail("Could not confirm your identity", "Please try again.", t)

        if self._decision.action is Action.ASK_RETURNING:
            self._mode = Mode.QUESTION
            self._deadline = t + self._cfg.attendance.question_timeout_s
            self._gestures = GestureDetector(self._cfg.gestures.nod_threshold,
                                             self._cfg.gestures.shake_threshold)
            self._answer = None
            return self._question_view(boxes)
        return self._execute(t)

    def _question_view(self, boxes) -> ViewState:
        return ViewState(f"Hi {self._student.full_name}, you clocked out earlier",
                         "Nod for yes, shake your head for no", "info", boxes,
                         question="Are you returning?")

    def _process_question(self, frame, faces, boxes, t) -> ViewState:
        answer = self._answer
        if answer is None and self._track(frame, faces, t) is None:
            if not self._q.steady or self._reverifying:
                # The kiosk moved: restart the nod/shake baseline so turning the device
                # can never be read as a head shake. The Yes/No buttons still work.
                self._gestures = GestureDetector(self._cfg.gestures.nod_threshold,
                                                 self._cfg.gestures.shake_threshold)
            else:
                signals = self._extractor.signals(frame, t)
                self._feed_shape(signals, faces, t)
                if signals is not None:
                    answer = self._gestures.update(signals.yaw, signals.pitch, t)
        elif answer is None:
            answer = "no"  # walked away or someone else stepped in
        if answer is None and t > self._deadline:
            answer = "no"
        if answer == "yes":
            return self._execute(t)
        if answer == "no":
            return self._show(ViewState(f"Okay, goodbye {self._student.full_name}!", "",
                                        "info"), t, self._cfg.attendance.cooldown_s)
        return self._question_view(boxes)

    def _execute(self, t: float) -> ViewState:
        """Record the clock-in or clock-out, after a final security check."""
        student, decision = self._student, self._decision
        guard = self._guard_check()
        if not guard.ok:
            return self._fail("Attendance is locked", guard.reasons[0], t)
        now = self._now()
        at = now.strftime("%H:%M")
        self._store.record_event(student.id, decision.event_kind, now,
                                 self._cutoff().strftime("%H:%M"), self._device_name(),
                                 guard.context)
        cooldown = self._cfg.attendance.cooldown_s
        if decision.action in (Action.CLOCK_OUT, Action.SIGNOUT_OFFER):
            state = ViewState(f"Goodbye, {student.full_name}!", f"Clocked out at {at}", "success")
        elif decision.action is Action.ASK_RETURNING:
            state = ViewState(f"Welcome back, {student.full_name}!",
                              f"Clocked in again at {at}", "success")
        elif decision.flag == FLAG_NO_MORNING_SIGNIN:
            state = ViewState(f"Welcome, {student.full_name}",
                              "You haven't signed in since morning, so you're clocked in now "
                              f"({at}).", "warn")
        else:
            state = ViewState(f"Welcome back to school, {student.full_name}!",
                              f"Clocked in at {at}", "success")
        return self._show(state, t, cooldown)

    # ---- registration -------------------------------------------------------------------

    def _end_registration(self) -> None:
        self._reg = None
        self._samples = []
        self._clear_attempt()

    def _reg_failed(self, message: str, t: float) -> ViewState:
        job_id = self._reg.job_id
        self._end_registration()
        try:
            self._submit(job_id, None, message)
        except Exception:
            pass  # the server marks it failed when the kiosk stops reporting it
        return self._show(ViewState("Registration failed", message, "error"), t)

    def _reg_retry(self, detail: str, t: float) -> ViewState:
        self._reg_attempts += 1
        if self._reg_attempts >= self.REG_MAX_ATTEMPTS:
            return self._reg_failed(f"{detail} Too many failed attempts.", t)
        self._samples = []
        self._reg_message = f"{detail} Starting again…"
        return self._show(ViewState("Let's try that again", self._reg_message, "warn"), t)

    def _reg_view(self, message: str, boxes, progress: float | None = None) -> ViewState:
        self._reg_message = message
        return ViewState(f"Registering {self._reg.full_name}", message, "info", boxes, progress)

    def _process_registration(self, frame, faces, boxes, t) -> ViewState:
        if t > self._reg_deadline:
            return self._reg_failed("Registration timed out.", t)

        if self._mode is Mode.REG_WAIT:
            face, problem = self._single_face(faces)
            if face is None:
                return self._reg_view(problem or "Look at the camera", boxes)
            self._blinks = BlinkCounter(self._cfg.liveness.blink_closed,
                                        self._cfg.liveness.blink_open)
            self._deadline = t + self.REG_BLINK_TIMEOUT_S
            self._begin_tracking(face, t)
            self._mode = Mode.REG_BLINK
            return self._reg_view("Look at the camera and blink", boxes)

        problem = self._track(frame, faces, t)
        if problem:
            return self._reg_retry(problem, t)

        if self._mode is Mode.REG_BLINK:
            signals = self._extractor.signals(frame, t)
            if self._count_blink(signals, self._feed_shape(signals, faces, t)):
                self._mode = Mode.REG_CAPTURE
                self._samples = []
                self._last_capture = 0.0
                return self._reg_view("Hold still while photos are taken", boxes, 0.0)
            if t > self._deadline:
                return self._reg_retry("No blink detected.", t)
            return self._reg_view("Hold still" if self._shaky(t)
                                  else "Look at the camera and blink", boxes)

        # REG_CAPTURE
        n = self._cfg.face.enroll_samples
        face, problem = self._single_face(faces)
        if face is None:
            return self._reg_view(problem or "Look at the camera", boxes, len(self._samples) / n)
        if self._light is not None and self._light.backlit:
            # Never store a backlit (silhouetted) face either. The IT officer is present
            # during registration and can move the student or the kiosk.
            return self._reg_view("Too much light behind the student. Move them or the kiosk.",
                                  boxes, len(self._samples) / n)
        if not self._q.usable or self._reverifying:
            # Never store a blurred or shaken photo: it would make this student harder
            # to recognise every day afterwards.
            return self._reg_view("Hold still" if self._shaky(t)
                                  else "Hold still while photos are taken",
                                  boxes, len(self._samples) / n)
        if t - self._last_capture >= self.CAPTURE_INTERVAL_S:
            emb = self._engine.embed(frame, face)
            if self._samples and float(emb @ self._samples[0]) < self.threshold:
                return self._reg_retry("A different face was detected.", t)
            self._samples.append(emb)
            self._last_capture = t
        if len(self._samples) >= n:
            return self._complete_registration(t)
        return self._reg_view("Hold still while photos are taken", boxes, len(self._samples) / n)

    def _complete_registration(self, t: float) -> ViewState:
        req = self._reg
        mean = normalize(np.mean(self._samples, axis=0))
        sid, score = best_match(mean, *self._gallery, exclude=req.student_id)
        if sid is not None and score >= self.threshold:
            other = self._store.get_student(sid)
            who = f"{other.full_name} ({other.student_no})" if other else "another student"
            return self._reg_failed(f"This face is already registered as {who}.", t)

        samples = list(self._samples)
        self._reg_message = "Saving…"
        try:
            ok, message = self._submit(req.job_id, samples, None)
        except Exception as exc:
            ok, message = False, f"Could not reach the server ({exc}). Please try again."
        self._end_registration()
        if not ok:
            return self._show(ViewState("Registration failed", message, "error"), t)
        return self._show(ViewState(f"{req.full_name} registered  ✓",
                                    "They can now sign in at any kiosk.", "success"), t)
