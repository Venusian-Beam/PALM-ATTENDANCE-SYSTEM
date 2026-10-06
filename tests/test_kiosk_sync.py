"""A kiosk's sync client and local app, talking to a real (in-memory) server."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.central.app import create_server_app
from backend.central.db import ServerDB
from backend.config import Config
from backend.controller import RegisterRequest
from backend.kiosk.app import create_kiosk_app
from backend.kiosk.store import KioskStore
from backend.kiosk.sync import SyncClient
from backend.rules import IN

NOW = datetime(2026, 10, 2, 8, 0)
MAC = "aa:aa:aa:aa:aa:01"
PASSWORD = "it-officer-pass"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FlakyClient:
    """Wraps the test client; `down = True` simulates the server being unreachable."""

    def __init__(self, inner):
        self.inner = inner
        self.down = False

    def post(self, *args, **kwargs):
        if self.down:
            raise httpx.ConnectError("server down")
        return self.inner.post(*args, **kwargs)

    def request(self, *args, **kwargs):
        if self.down:
            raise httpx.ConnectError("server down")
        return self.inner.request(*args, **kwargs)


class FakeController:
    def __init__(self):
        self.started: list[RegisterRequest] = []
        self.cancelled: list[int] = []
        self.active: dict | None = None

    def registration_status(self):
        return self.active

    def start_registration(self, request):
        self.started.append(request)
        self.active = {"job_id": request.job_id, "active": True, "message": "Blink",
                       "progress": None}

    def cancel_registration(self, job_id=None):
        self.cancelled.append(job_id)
        self.active = None

    def tuning_status(self):
        return None

    def start_tuning(self, job_id, phase, seconds):
        self.tuning = (job_id, phase, seconds)


def unit(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    server_db = ServerDB(":memory:")
    auth.set_admin_password(server_db, PASSWORD)
    server_db.set_school_settings({"wifi_ssid": "SCHOOL", "lat": 6.5, "lon": 3.4,
                                   "radius_m": 200, "wifi_bssids": []})
    server_app = create_server_app(Config(role="server"), server_db, now=clock)
    http = FlakyClient(TestClient(server_app, client=("192.168.1.31", 1)))

    cfg = Config()
    store = KioskStore(tmp_path / "kiosk.db")
    controller = FakeController()
    sync = SyncClient(cfg, store, http, controller=lambda: controller,
                      macs_provider=lambda: {MAC: "Wi-Fi", "11:22:33:44:55:66": "Ethernet"},
                      wifi_provider=lambda: [], now=clock)
    yield SimpleNamespace(clock=clock, db=server_db, app=server_app, http=http, cfg=cfg,
                          store=store, controller=controller, sync=sync, tmp=tmp_path)
    store.close()


def admin(app):
    c = TestClient(app, client=("192.168.1.10", 1))
    token = c.post("/api/admin/login", json={"password": PASSWORD}).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def test_not_approved_until_admin_adds_mac(env):
    env.sync.sync_once()
    check = env.sync.approval_check()
    assert not check.ok and MAC in check.detail

    admin(env.app).post("/api/admin/devices", json={"name": "Gate A", "mac": MAC})
    env.sync.sync_once()
    check = env.sync.approval_check()
    assert check.ok and "Gate A" in check.detail
    assert env.cfg.wifi.ssid == "SCHOOL" and env.cfg.geofence.lat == 6.5


def test_events_upload_and_offline_queue(env):
    env.db.add_device("Gate A", MAC)
    ada = env.db.create_student("001", "Ada", [unit(1)])
    env.sync.sync_once()
    assert env.store.get_student(ada.id).full_name == "Ada"  # roster arrived

    env.http.down = True
    env.store.record_event(ada.id, IN, NOW, "17:30", "Gate A",
                           {"ssid": "SCHOOL", "lat": 6.5, "lon": 3.4})
    env.sync.sync_once()
    assert not env.sync.online and env.store.pending_count() == 1
    check = env.sync.approval_check()
    assert check.ok and "Offline: 1 record(s) waiting" in check.detail

    # The outbox survives a restart of the kiosk.
    reopened = KioskStore(env.tmp / "kiosk.db")
    assert reopened.pending_count() == 1
    reopened.close()

    env.http.down = False
    env.sync.sync_once()
    assert env.sync.online and env.store.pending_count() == 0
    [session] = env.db.sessions_on(NOW.date())
    assert session["clock_in"] == "08:00:00" and session["in_device"] == "Gate A"


def test_locks_after_too_long_offline(env):
    env.db.add_device("Gate A", MAC)
    env.sync.sync_once()
    env.http.down = True
    env.clock.now = NOW + timedelta(hours=11)
    env.sync.sync_once()
    assert env.sync.approval_check().ok
    env.clock.now = NOW + timedelta(hours=13)
    check = env.sync.approval_check()
    assert not check.ok and "has not reached the attendance server" in check.detail


def test_removed_device_locks(env):
    dev = env.db.add_device("Gate A", MAC)
    env.sync.sync_once()
    assert env.sync.approval_check().ok
    env.db.remove_device(dev["id"], NOW)
    env.clock.now = NOW + timedelta(minutes=1)
    env.sync.sync_once()
    check = env.sync.approval_check()
    assert not check.ok and "removed" in check.detail


def test_wrong_clock_locks(env):
    env.db.add_device("Gate A", MAC)
    env.sync._now = lambda: NOW - timedelta(minutes=30)
    env.sync.sync_once()
    check = env.sync.approval_check()
    assert not check.ok and "clock is wrong" in check.detail


def test_rejected_events_leave_the_outbox(env):
    env.db.add_device("Gate A", MAC)
    ada = env.db.create_student("001", "Ada", [unit(1)])
    env.store.record_event(ada.id, IN, NOW, "17:30", "Gate A", {"ssid": "CoffeeShop"})
    env.sync.sync_once()
    assert env.store.pending_count() == 0 and env.db.sessions_on(NOW.date()) == []


def test_registration_job_is_picked_up_and_result_saved(env):
    dev = env.db.add_device("Gate A", MAC)
    env.sync.sync_once()
    a = admin(env.app)
    job = a.post("/api/admin/register", json={"full_name": "Chidi", "student_no": "005",
                                              "device_id": dev["id"]}).json()
    env.sync.sync_once()
    [request] = env.controller.started
    assert (request.job_id, request.full_name) == (job["id"], "Chidi")

    ok, message = env.sync.submit_registration(job["id"], [unit(5)] * 3, None)
    assert ok and "Chidi registered" in message
    env.controller.active = None
    env.sync.sync_once()
    assert env.store.student_count() == 1
    assert len(env.controller.started) == 1  # the finished job is not restarted


def test_cancelled_job_cancels_on_kiosk(env):
    dev = env.db.add_device("Gate A", MAC)
    env.sync.sync_once()
    a = admin(env.app)
    job = a.post("/api/admin/register", json={"full_name": "C", "student_no": "7",
                                              "device_id": dev["id"]}).json()
    env.sync.sync_once()
    a.post(f"/api/admin/register/{job['id']}/cancel")
    env.sync.sync_once()
    assert env.controller.cancelled == [job["id"]]


def test_kiosk_app_is_local_only_and_proxies_admin(env):
    runtime = SimpleNamespace(controller=SimpleNamespace(answer=lambda v: False),
                              store=env.store, sync=env.sync,
                              request_recheck=lambda: None, camera=None)
    app = create_kiosk_app(runtime, env.http)
    local = TestClient(app, client=("127.0.0.1", 1))
    remote = TestClient(app, client=("192.168.1.50", 1))
    assert remote.post("/api/kiosk/answer", json={"answer": "yes"}).status_code == 403
    assert remote.get("/api/admin/devices").status_code == 403
    assert local.post("/api/kiosk/answer", json={"answer": "yes"}).json() == {"accepted": False}

    assert local.get("/api/admin/devices").status_code == 401  # proxied to the server
    token = local.post("/api/admin/login", json={"password": PASSWORD}).json()["token"]
    devices = local.get("/api/admin/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices.status_code == 200 and devices.json() == []
    env.http.down = True
    assert local.get("/api/admin/devices").status_code == 502
