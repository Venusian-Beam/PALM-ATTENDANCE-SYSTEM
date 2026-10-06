"""Controller flows on a kiosk: scripted fake frames, a fake clock and a local store."""
import base64
from datetime import date, datetime, time

import numpy as np
import pytest

from backend.config import Config
from backend.controller import Controller, RegisterRequest, RegistrationBusyError
from backend.face.lighting import LightInfo
from backend.face.quality import FrameQuality
from backend.face.blink import BlinkCounter
from backend.face.signals import FaceSignals
from backend.face.spoof import CLEAN, SpoofVerdict
from backend.kiosk.store import KioskStore
from backend.rules import FLAG_NO_MORNING_SIGNIN, IN, OUT
from backend.security.guard import GuardResult
from backend.security.result import CheckResult

OPEN, CLOSED = FaceSignals(0.05, 0.0, 0.0), FaceSignals(0.9, 0.0, 0.0)
DAY = date(2026, 10, 2)
CUTOFF = time(17, 30)


def unit(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def face(x=200, w=200):
    return np.array([x, 100, w, w] + [0] * 10 + [0.99], dtype=np.float32)


def frame(emb=None, signals=OPEN, faces=None, steady=True, sharp=True, shift=(0.0, 0.0),
          **extra):
    return {"faces": [face()] if faces is None else faces, "emb": emb, "signals": signals,
            "steady": steady, "sharp": sharp, "shift": shift, **extra}


def blurry(emb, signals=OPEN, **kw):
    return frame(emb, signals, sharp=False, **kw)


class FakeEngine:
    def detect(self, f):
        return f["faces"]

    def embed(self, f, _face):
        return f["emb"]


class FakeExtractor:
    def signals(self, f, _t):
        return f["signals"]


class FakeLighting:
    """Scripted backlight: frames carry `backlit`, `rescue_faces` and `enhanced_emb`."""

    def assess(self, f, _face):
        lit = f.get("backlit", False)
        return LightInfo(30.0 if lit else 130.0, 240.0 if lit else 120.0, lit, lit)

    def enhance(self, f, info):
        if info is None or not info.dark:
            return f
        return {**f, "emb": f.get("enhanced_emb", f["emb"]), "enhanced": True}

    def maybe_backlit(self, f):
        return f.get("backlit", False)

    def rescue(self, f):
        return {**f, "faces": f.get("rescue_faces", []),
                "emb": f.get("enhanced_emb", f["emb"]), "rescued": True}


class FakeQuality:
    """Frame quality comes from the scripted frame (default: steady and sharp)."""

    def assess(self, f, _face):
        return FrameQuality(f.get("steady", True), f.get("sharp", True), f.get("shift", (0, 0)))


PHONE = SpoofVerdict("object", "phone", "phone detected (0.80)")


class FakeSpoof:
    """Frames carry `spoof` (a SpoofVerdict) and `fake_blink` (a photo being tilted)."""

    def __init__(self):
        self.live_frames = 0
        self.fake = False
        self.attempts = []
        self._blinks = BlinkCounter()

    def check(self, f, faces, t, steady):
        self.saw_brightened = getattr(self, "saw_brightened", False) or f.get("enhanced", False)
        verdict = f.get("spoof", CLEAN)
        self.fake = f.get("fake_blink", False)
        self.live_frames = 0 if verdict.spoof else self.live_frames + (1 if faces else 0)
        return verdict

    def landmarks(self, points, yaw, pitch, width, blink, t):
        if self._blinks.update(blink):
            return "fake" if self.fake else "genuine"
        return None

    def new_attempt(self, t=None, expect_blink=True):
        self.attempts.append(expect_blink)
        self._blinks = BlinkCounter()

    def end_attempt(self):
        pass

    def reset(self):
        self.live_frames = 0

    def live_now(self):
        return True

    def score(self, f, face):
        return f.get("score", 0.9)

    def phone_score(self, f, face):
        return f.get("phone", 0.0)


def roster(students, version=1):
    return {"version": version, "students": [
        {"id": sid, "student_no": no, "full_name": name,
         "embeddings": [base64.b64encode(e.tobytes()).decode() for e in embs]}
        for sid, no, name, embs in students]}


ADA = (1, "001", "Ada Obi", [unit(1)])


@pytest.fixture
def store():
    s = KioskStore(":memory:")
    s.replace_roster(roster([ADA]))
    yield s
    s.close()


class Env:
    def __init__(self, store, hhmm=(7, 40), guard_ok=True, lighting=None, spoof=None):
        self.store = store
        self.t = 0.0
        self.now = datetime(2026, 10, 2, *hhmm)
        self.guard_ok = guard_ok
        self.submitted = []
        self.submit_reply = (True, "Saved.")
        self.alerts = []
        cfg = Config()
        cfg.face.enroll_samples = 3
        self.ctrl = Controller(cfg, store, FakeEngine(), FakeExtractor(), self.guard,
                               cutoff=lambda: CUTOFF, submit_registration=self.submit,
                               device_name=lambda: "Gate A", clock=self.clock,
                               now=lambda: self.now, quality=FakeQuality(),
                               lighting=lighting, spoof=spoof,
                               report_alert=self.alerts.append)

    def clock(self):
        self.t += 0.1
        return self.t

    def guard(self):
        if self.guard_ok:
            return GuardResult([CheckResult("wifi", True, "", {"ssid": "SCHOOL"}),
                                CheckResult("location", True, "", {"lat": 6.5, "lon": 3.4})])
        return GuardResult([CheckResult("wifi", False, "Not on the school Wi-Fi.")])

    def submit(self, job_id, samples, error):
        self.submitted.append((job_id, samples, error))
        return self.submit_reply if samples else (False, error)

    def run(self, frames):
        return [self.ctrl.process(f) for f in frames]

    def wait(self, seconds):
        self.t += seconds
        self.ctrl.process(frame(faces=[]))


def blinks(emb, n):
    return [frame(emb, OPEN)] + [frame(emb, s) for _ in range(n) for s in (CLOSED, OPEN)]


def scan(emb, n_blinks=1):
    return blinks(emb, n_blinks) + [frame(emb, OPEN)]


def sessions(store, sid=1):
    return store.sessions_for(sid, DAY, CUTOFF)


def record(store, kind, h, m):
    store.record_event(1, kind, datetime(2026, 10, 2, h, m), "17:30", "Gate B", {})


def test_morning_clock_in_records_an_event(store):
    env = Env(store)
    states = env.run(scan(unit(1)))
    assert states[0].detail == "Blink to clock in"
    assert states[-1].headline == "Welcome back to school, Ada Obi!"
    [ev] = store.outbox()
    assert (ev["kind"], ev["ts"], ev["ssid"], ev["lat"]) == (IN, "2026-10-02T07:40:00",
                                                             "SCHOOL", 6.5)
    assert not sessions(store)[0].closed


def test_photo_never_blinks(store):
    states = Env(store).run([frame(unit(1), OPEN)] * 100)
    assert any(s.headline == "Liveness check failed" for s in states)
    assert store.outbox() == []


def test_one_blink_while_clocked_in_does_not_sign_out(store):
    record(store, IN, 7, 40)
    env = Env(store, (14, 0))
    states = env.run(blinks(unit(1), 1) + [frame(unit(1), OPEN)] * 90)
    assert states[0].detail == "Blink 3 times to sign out"
    assert any("still signed in" in s.detail for s in states)
    assert not sessions(store)[0].closed


def test_three_blinks_sign_out(store):
    record(store, IN, 7, 40)
    env = Env(store, (14, 10))
    assert env.run(scan(unit(1), 3))[-1].headline == "Goodbye, Ada Obi!"
    assert sessions(store)[0].clock_out == "14:10:00"


def test_clock_in_at_other_gate_is_respected(store):
    """Gate B's clock-in (synced from the server) makes this gate offer sign-out."""
    store.merge_server_events([{"id": "x1", "student_id": 1, "kind": IN,
                                "ts": "2026-10-02T07:30:00", "device": "Gate B"}])
    assert Env(store, (9, 0)).run([frame(unit(1))])[0].detail == "Blink 3 times to sign out"


def test_after_cutoff_scan_clocks_out(store):
    record(store, IN, 7, 40)
    env = Env(store, (17, 35))
    states = env.run(scan(unit(1)))
    assert states[0].detail == "Blink to clock out"
    assert states[-1].headline == "Goodbye, Ada Obi!"


def test_first_scan_after_cutoff_flagged_then_next_scan_clocks_out(store):
    env = Env(store, (17, 40))
    assert "haven't signed in since morning" in env.run(scan(unit(1)))[-1].detail
    assert sessions(store)[0].in_flag == FLAG_NO_MORNING_SIGNIN
    env.wait(5)
    assert env.run([frame(unit(1))])[0].detail == "You're all set."   # cooldown
    env.wait(40)
    env.now = datetime(2026, 10, 2, 17, 41)
    assert "signed in since 17:40" in env.run([frame(unit(1))])[0].detail
    env.wait(10)
    env.now = datetime(2026, 10, 2, 18, 5)
    assert env.run(scan(unit(1)))[-1].headline == "Goodbye, Ada Obi!"


def _clocked_out(store):
    record(store, IN, 7, 40)
    record(store, OUT, 12, 0)


def test_returning_nod_clocks_in_again(store):
    _clocked_out(store)
    env = Env(store, (13, 0))
    assert env.run(scan(unit(1)))[-1].question == "Are you returning?"
    nod = [FaceSignals(0.05, 0.0, p) for p in (0, 0, 0, 0.05, 0.15, 0.16, 0.02)]
    assert env.run([frame(unit(1), s) for s in nod])[-1].headline == "Welcome back, Ada Obi!"
    assert [s.closed for s in sessions(store)] == [True, False]


def test_returning_shake_and_button(store):
    _clocked_out(store)
    env = Env(store, (13, 0))
    env.run(scan(unit(1)))
    shake = [FaceSignals(0.05, y, 0.0) for y in (0, 0, 0, 0.3, 0.0, -0.3, 0.0)]
    assert env.run([frame(unit(1), s) for s in shake])[-1].headline == "Okay, goodbye Ada Obi!"
    assert len(sessions(store)) == 1
    env.wait(40)
    env.run(scan(unit(1)))
    assert env.ctrl.answer("yes")
    assert env.run([frame(unit(1))])[0].headline == "Welcome back, Ada Obi!"


def test_unknown_face_and_swaps(store):
    env = Env(store)
    states = env.run([frame(unit(99))] * Controller.MISS_FRAMES_BEFORE_REJECT)
    assert states[-1].headline == "Face not recognised"
    env.wait(5)
    env.run([frame(unit(1))])
    # The picture jumps and it is now someone else: cancelled.
    state = env.run([frame(unit(2), CLOSED, faces=[face(x=500)])])[0]
    assert (state.headline, state.detail) == ("Check cancelled", "Face changed during the check.")
    env.wait(5)
    frames = scan(unit(1))
    frames[-1] = frame(unit(2), OPEN)
    assert env.run(frames)[-1].headline == "Could not confirm your identity"
    assert store.outbox() == []


def test_guard_failure_blocks_recording(store):
    assert Env(store, guard_ok=False).run(scan(unit(1)))[-1].headline == "Attendance is locked"
    assert store.outbox() == []


def test_new_roster_is_picked_up(store):
    env = Env(store)
    assert env.run([frame(unit(7))] * 6)[-1].headline == "Face not recognised"
    store.replace_roster(roster([ADA, (2, "002", "Bola", [unit(7)])], version=2))
    env.wait(5)
    assert env.run([frame(unit(7))])[0].headline == "Hi Bola"


# ---- registration (results go to the server) ---------------------------------------------

def register(env, emb, request):
    env.ctrl.start_registration(request)
    env.run(blinks(emb, 1))
    for _ in range(20):
        if env.ctrl.registration_status() is None:
            break
        env.t += 0.4
        env.ctrl.process(frame(emb, OPEN))


def test_registration_submits_samples_to_server(store):
    env = Env(store)
    register(env, unit(5), RegisterRequest(42, "005", "Chidi"))
    [(job_id, samples, error)] = env.submitted
    assert job_id == 42 and len(samples) == 3 and error is None
    assert env.ctrl.registration_status() is None


def test_registration_server_refusal_is_shown(store):
    env = Env(store)
    env.submit_reply = (False, "Student ID 005 is already registered.")
    env.ctrl.start_registration(RegisterRequest(1, "005", "Chidi"))
    env.run(blinks(unit(5), 1))
    states = []
    for _ in range(20):
        env.t += 0.4
        states.append(env.ctrl.process(frame(unit(5), OPEN)))
    assert any(s.detail == "Student ID 005 is already registered." for s in states)


def test_registration_rejects_known_face_locally(store):
    env = Env(store)
    register(env, unit(1), RegisterRequest(3, "009", "Fake Ada"))
    [(_, samples, error)] = env.submitted
    assert samples is None and "already registered as Ada Obi" in error


def test_registration_busy_and_cancel(store):
    env = Env(store)
    env.ctrl.start_registration(RegisterRequest(1, "009", "X"))
    with pytest.raises(RegistrationBusyError):
        env.ctrl.start_registration(RegisterRequest(2, "010", "Y"))
    env.ctrl.cancel_registration(job_id=99)  # a different job: ignored
    assert env.ctrl.registration_status()["job_id"] == 1
    env.ctrl.cancel_registration(job_id=1)
    assert env.ctrl.registration_status() is None


# ---- bumps and shaking -------------------------------------------------------------------

def test_blurry_frames_never_reject_a_student(store):
    env = Env(store)
    states = env.run([blurry(unit(99))] * 20 + [blurry(unit(1))] * 20)
    assert not any(s.headline == "Face not recognised" for s in states)
    assert env.run(scan(unit(1)))[-1].headline == "Welcome back to school, Ada Obi!"


def test_bump_mid_blink_is_compensated_and_keeps_the_blink(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN), frame(unit(1), CLOSED)])
    # The kiosk is bumped: the whole picture (and the face with it) moves 300 px.
    jolt = frame(unit(1), OPEN, faces=[face(x=500)], steady=False, shift=(300.0, 0.0))
    states = env.run([jolt, frame(unit(1), OPEN, faces=[face(x=500)]),
                      frame(unit(1), OPEN, faces=[face(x=500)])])
    assert not any(s.headline == "Check cancelled" for s in states)
    assert states[-1].headline == "Welcome back to school, Ada Obi!"


def test_jump_is_rechecked_not_cancelled_for_the_same_student(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN)])
    states = env.run([frame(unit(1), CLOSED, faces=[face(x=500)]),
                      frame(unit(1), OPEN, faces=[face(x=500)]),
                      frame(unit(1), OPEN, faces=[face(x=500)])])
    assert states[-1].headline == "Welcome back to school, Ada Obi!"


def test_jump_waits_for_a_sharp_frame_before_rechecking(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN)])
    shaken = [blurry(unit(2), faces=[face(x=500)])] * 5  # blurry: identity not judged yet
    assert not any(s.headline == "Check cancelled" for s in env.run(shaken))
    state = env.run([frame(unit(2), faces=[face(x=500)])])[0]
    assert state.detail == "Face changed during the check."


def test_shake_pauses_the_blink_timer_up_to_the_cap(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN)])                   # blink check starts (8 s)
    states = env.run([blurry(unit(1))] * 30 + [frame(unit(1), OPEN)] * 75)  # 10.5 s < 8 + 3
    assert not any(s.headline == "Liveness check failed" for s in states)  # 3 s were paused

    env = Env(store)                                  # a fresh attempt
    env.run([frame(unit(1), OPEN)])
    long_shake = [blurry(unit(1))] * 60                # 6 s, but at most 4 s is forgiven
    states = env.run(long_shake + [frame(unit(1), OPEN)] * 55)
    assert not any(s.headline == "Liveness check failed" for s in states)
    states = env.run([frame(unit(1), OPEN)] * 10)
    assert any(s.headline == "Liveness check failed" for s in states)


def test_blink_on_a_shaken_frame_does_not_count(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN)])
    states = env.run([frame(unit(1), CLOSED, steady=False), frame(unit(1), OPEN)])
    assert states[-1].blinks == {"count": 0, "required": 1}


def test_hold_still_hint_only_after_a_second(store):
    env = Env(store)
    env.run([frame(unit(1), OPEN)])
    states = env.run([blurry(unit(1))] * 15)
    assert states[5].detail == "Blink to clock in"
    assert states[12].detail == "Hold still…"


def test_moving_kiosk_is_never_read_as_a_head_shake(store):
    _clocked_out(store)
    env = Env(store, (13, 0))
    assert env.run(scan(unit(1)))[-1].question == "Are you returning?"
    turning = [FaceSignals(0.05, y, 0.0) for y in (0, 0.3, 0.0, -0.3, 0.0, 0.3, -0.3)]
    states = env.run([frame(unit(1), sig, steady=False) for sig in turning])
    assert not any(s.headline.startswith("Okay, goodbye") for s in states)
    assert env.ctrl.answer("yes")
    assert env.run([frame(unit(1))])[0].headline == "Welcome back, Ada Obi!"


def test_registration_never_stores_blurry_photos(store):
    env = Env(store)
    env.ctrl.start_registration(RegisterRequest(7, "007", "Kofi"))
    env.run(blinks(unit(5), 1))
    for _ in range(10):
        env.t += 0.4
        env.ctrl.process(blurry(unit(5)))
    assert env.ctrl.registration_status()["progress"] == 0
    for _ in range(20):
        if env.ctrl.registration_status() is None:
            break
        env.t += 0.4
        env.ctrl.process(frame(unit(5), OPEN))
    [(job_id, samples, error)] = env.submitted
    assert job_id == 7 and len(samples) == 3 and error is None


# ---- bright light behind the student -----------------------------------------------------

def backlit(emb=None, signals=OPEN, **kw):
    """A silhouetted face: only the brightened picture shows who it is."""
    return {**frame(None, signals, **kw), "backlit": True, "enhanced_emb": emb}


def test_brightened_face_is_what_gets_recognised(store):
    env = Env(store, lighting=FakeLighting())
    frames = [backlit(unit(1), s) for s in (OPEN, CLOSED, OPEN, OPEN)]
    states = env.run(frames)
    assert states[0].headline == "Hi Ada Obi"
    assert states[-1].headline == "Welcome back to school, Ada Obi!"


def test_face_hidden_by_backlight_is_found_on_a_brightened_copy(store):
    env = Env(store, lighting=FakeLighting())

    def hidden(signals):
        return {**backlit(unit(1), signals, faces=[]), "rescue_faces": [face()]}

    # Rescue detection runs on every second empty frame, so each moment is sent twice.
    frames = [hidden(s) for s in (OPEN, OPEN, CLOSED, CLOSED, OPEN, OPEN, OPEN, OPEN)]
    assert env.run(frames)[-1].headline == "Welcome back to school, Ada Obi!"


def test_too_much_light_hint_only_after_a_moment(store):
    env = Env(store, lighting=FakeLighting())
    states = env.run([backlit(None, faces=[])] * 25)  # silhouette, nothing detectable
    assert states[5].headline == "Look at the camera to sign in"
    assert states[24].headline == "Too much light behind you"


def test_registration_waits_for_good_light(store):
    env = Env(store, lighting=FakeLighting())
    env.ctrl.start_registration(RegisterRequest(8, "008", "Esi"))
    env.run([backlit(unit(5), s) for s in (OPEN, CLOSED, OPEN)])
    for _ in range(10):
        env.t += 0.4
        state = env.ctrl.process(backlit(unit(5)))
    assert env.ctrl.registration_status()["progress"] == 0
    assert "Too much light behind the student" in state.detail
    for _ in range(20):
        if env.ctrl.registration_status() is None:
            break
        env.t += 0.4
        env.ctrl.process(frame(unit(5), OPEN))
    [(job_id, samples, error)] = env.submitted
    assert job_id == 8 and len(samples) == 3


# ---- photos, phones and other objects ---------------------------------------------------

def test_foreign_object_blurs_and_shows_one_message(store):
    env = Env(store, spoof=FakeSpoof())
    state = env.run([frame(unit(1), spoof=PHONE)])[-1]
    assert state.headline == "Foreign object detected" and state.detail == ""
    assert state.blur and state.tone == "error" and state.boxes == []
    assert env.store.pending_count() == 0


def test_object_mid_blink_abandons_the_attempt_and_records_nothing(store):
    spoof = FakeSpoof()
    env = Env(store, spoof=spoof)
    env.run([frame(unit(1))] * 4)                       # live, recognised, asked to blink
    env.run([frame(unit(1), CLOSED, spoof=PHONE)])
    env.run([frame(unit(1), OPEN)] * 3)                 # still blocked (not clean long enough)
    assert env.store.pending_count() == 0
    assert len(env.alerts) == 1
    alert = env.alerts[0]
    assert alert["label"] == "phone" and alert["student_id"] == 1


def test_one_alert_per_episode_and_back_to_normal_once_clean(store):
    env = Env(store, spoof=FakeSpoof())
    env.run([frame(unit(1), spoof=PHONE)] * 10)
    assert len(env.alerts) == 1
    states = env.run([frame(unit(1))] * 15)             # 1.5 s clean
    assert states[0].headline == "Foreign object detected"
    assert states[-1].headline != "Foreign object detected" and not states[-1].blur


def test_tilted_photo_blinks_do_not_count(store):
    env = Env(store, spoof=FakeSpoof())
    env.run([frame(unit(1))] * 4)
    states = env.run([frame(unit(1), s, fake_blink=True) for s in (CLOSED, OPEN) * 3])
    assert states[-1].blinks["count"] == 0
    assert env.store.pending_count() == 0


def test_genuine_blink_clocks_in_with_photo_checks_on(store):
    env = Env(store, spoof=FakeSpoof())
    env.run([frame(unit(1))] * 4)
    env.run(blinks(unit(1), 1))
    env.run([frame(unit(1))] * 3)                       # confirm
    assert env.store.pending_count() == 1


def test_no_name_before_the_face_passes_the_live_check(store):
    env = Env(store, spoof=FakeSpoof())
    state = env.run([frame(unit(1))])[-1]
    assert "Ada" not in state.headline


def test_daily_backlight_numbers_for_the_it_officer(store):
    env = Env(store, lighting=FakeLighting())
    env.run([backlit(unit(1))] * 20)                    # one backlit visit (2 s)
    env.run([frame(None, faces=[])] * 3)
    env.run([frame(unit(1))])                           # a normal visit
    assert store.get("light_date") == "2026-10-02"
    assert (store.get_int("light_scans"), store.get_int("light_backlit")) == (2, 1)



def test_phone_training_collects_phone_scores(store):
    spoof = FakeSpoof()
    env = Env(store, spoof=spoof)
    sent = []
    env.ctrl._submit_tuning = lambda job_id, hist: (sent.append((job_id, hist)) or (True, "ok"))
    env.ctrl.start_tuning(4, "phone", 3.0)
    states = env.run([frame(unit(1), phone=0.7)] * 30)
    assert states[0].headline == "Kiosk tuning"
    assert states[0].detail == "Hold a phone up in front of your face"
    env.run([frame(unit(1), phone=0.7)] * 5)
    [(job_id, hist)] = sent
    assert job_id == 4 and sum(hist) >= 8 and hist[35] == sum(hist)   # every score was 0.7
    assert env.store.pending_count() == 0


def test_photo_checks_see_the_camera_picture_not_the_brightened_one(store):
    spoof = FakeSpoof()
    env = Env(store, lighting=FakeLighting(), spoof=spoof)
    env.run([backlit(unit(1))] * 5)                     # dark face: brightened for recognition
    assert not spoof.saw_brightened
