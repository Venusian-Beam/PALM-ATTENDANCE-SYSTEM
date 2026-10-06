"""The school server: device approval by MAC, sync, events, registration jobs, admin API."""
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.central.app import create_server_app
from backend.central.db import ServerDB, encode_embedding
from backend.config import Config

NOW = datetime(2026, 10, 2, 8, 0)
MAC_A = "aa:aa:aa:aa:aa:01"
MAC_B = "bb:bb:bb:bb:bb:02"
PASSWORD = "it-officer-pass"
KIOSK_IP = ("192.168.1.31", 5000)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    cfg = Config(role="server")
    db = ServerDB(":memory:")
    auth.set_admin_password(db, PASSWORD)
    db.set_school_settings({"wifi_ssid": "SCHOOL", "lat": 6.5, "lon": 3.4, "radius_m": 200})
    clock = Clock()
    app = create_server_app(cfg, db, now=clock)
    yield db, app, clock
    db.close()


def client(app, address=KIOSK_IP):
    return TestClient(app, client=address)


def admin(app, address=("192.168.1.10", 1)):
    c = client(app, address)
    token = c.post("/api/admin/login", json={"password": PASSWORD}).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def unit(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def event(student_id, kind="IN", ts=NOW, **ctx):
    base = {"id": str(uuid.uuid4()), "student_id": student_id, "kind": kind,
            "ts": ts.isoformat(), "ssid": "SCHOOL", "lat": 6.5, "lon": 3.4}
    base.update(ctx)
    return base


def sync(c, macs, clock, **extra):
    body = {"macs": macs, "device_time": clock.now.isoformat(), "ssid": "SCHOOL"}
    body.update(extra)
    return c.post("/api/device/sync", json=body)


# ---- device approval --------------------------------------------------------------

def test_unknown_mac_is_refused_with_its_macs_listed(env):
    db, app, clock = env
    r = sync(client(app), ["AA-AA-AA-AA-AA-01"], clock)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_approved"
    assert MAC_A in r.json()["detail"]["message"]


def test_admin_adds_device_then_it_syncs(env):
    db, app, clock = env
    a = admin(app)
    assert a.post("/api/admin/devices", json={"name": "Gate A", "mac": "AA-AA-AA-AA-AA-01"}
                  ).status_code == 200
    r = sync(client(app), ["11:22:33:44:55:66", MAC_A], clock)
    assert r.status_code == 200
    data = r.json()
    assert data["approved"] and data["device_name"] == "Gate A"
    assert data["settings"]["wifi"]["ssid"] == "SCHOOL"
    [dev] = a.get("/api/admin/devices").json()
    assert dev["online"] and dev["last_ip"] == "192.168.1.31"


def test_device_validation(env):
    db, app, _ = env
    a = admin(app)
    assert a.post("/api/admin/devices", json={"name": "X", "mac": "not-a-mac-addr"}).status_code == 400
    a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A})
    dup = a.post("/api/admin/devices", json={"name": "Again", "mac": MAC_A.upper()})
    assert dup.status_code == 400 and "already added" in dup.json()["detail"]


def test_removed_device_is_refused_but_earlier_events_are_kept(env):
    db, app, clock = env
    a = admin(app)
    dev = a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A}).json()
    student = db.create_student("001", "Ada", [unit(1)])
    clock.now = NOW + timedelta(hours=1)
    assert a.delete(f"/api/admin/devices/{dev['id']}").status_code == 200

    before = event(student.id, ts=NOW)                        # recorded offline before removal
    after = event(student.id, "OUT", ts=NOW + timedelta(hours=2))
    clock.now = NOW + timedelta(hours=2)
    data = sync(client(app), [MAC_A], clock, outbox=[before, after]).json()
    assert not data["approved"] and "removed" in data["message"]
    assert data["accepted"] == [before["id"]]
    assert data["rejected"][0]["reason"] == "recorded after the device was removed"
    assert "roster" not in data and "events" not in data
    # Re-adding the same MAC works again.
    assert a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A}).status_code == 200
    assert sync(client(app), [MAC_A], clock).json()["approved"]


def test_wrong_clock_is_refused(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    body = {"macs": [MAC_A], "device_time": (NOW - timedelta(minutes=10)).isoformat()}
    r = client(app).post("/api/device/sync", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "clock"


def test_device_network_restriction():
    cfg = Config(role="server")
    cfg.server.device_allowed_networks = ["10.0.0.0/8"]
    db = ServerDB(":memory:")
    db.add_device("Gate A", MAC_A)
    clock = Clock()
    app = create_server_app(cfg, db, now=clock)
    assert sync(client(app), [MAC_A], clock).status_code == 403
    assert sync(client(app, ("10.1.1.1", 1)), [MAC_A], clock).status_code == 200


def test_same_mac_from_two_computers_is_flagged(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    sync(client(app, ("192.168.1.31", 1)), [MAC_A], clock)
    clock.now = NOW + timedelta(minutes=1)
    sync(client(app, ("192.168.1.99", 1)), [MAC_A], clock)
    [dev] = admin(app).get("/api/admin/devices").json()
    assert dev["mac_conflict"] and "192.168.1.31" in dev["mac_conflict"]


# ---- events + sessions ---------------------------------------------------------------

def test_events_from_two_gates_make_one_session(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    db.add_device("Gate B", MAC_B)
    ada = db.create_student("001", "Ada", [unit(1)])
    e_in = event(ada.id, "IN", NOW)
    assert sync(client(app), [MAC_A], clock, outbox=[e_in]).json()["accepted"] == [e_in["id"]]
    # Re-upload is harmless.
    sync(client(app), [MAC_A], clock, outbox=[e_in])
    clock.now = NOW + timedelta(hours=6)
    sync(client(app, ("192.168.1.32", 1)), [MAC_B], clock,
         outbox=[event(ada.id, "OUT", clock.now)])

    data = admin(app).get("/api/admin/sessions?date=2026-10-02").json()
    [s] = data["sessions"]
    assert (s["clock_in"], s["clock_out"]) == ("08:00:00", "14:00:00")
    assert (s["in_device"], s["out_device"]) == ("Gate A", "Gate B")
    assert data["counts"] == {"in": 0, "out": 1, "students": 1}


def test_events_must_be_on_school_wifi_and_grounds(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    ada = db.create_student("001", "Ada", [unit(1)])
    bad = [event(ada.id, ssid="CoffeeShop"), event(ada.id, lat=6.6, lon=3.4),
           event(ada.id, lat=None, lon=None), event(999), event(ada.id, ts=NOW + timedelta(hours=1))]
    data = sync(client(app), [MAC_A], clock, outbox=bad).json()
    assert data["accepted"] == []
    assert [r["reason"] for r in data["rejected"]] == [
        "not recorded on the school Wi-Fi", "recorded outside the school grounds",
        "no location", "unknown student", "timestamp in the future"]


def test_access_point_lock(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    ada = db.create_student("001", "Ada", [unit(1)])
    sync(client(app), [MAC_A], clock, bssid="42:37:18:02:2a:cc")
    a = admin(app)
    assert a.post(f"/api/admin/devices/{dev['id']}/allow-access-point").json() == {
        "wifi_bssids": ["42:37:18:02:2a:cc"]}
    data = sync(client(app), [MAC_A], clock,
                outbox=[event(ada.id, bssid="00:00:00:00:00:09"),
                        event(ada.id, bssid="42:37:18:02:2a:cc")]).json()
    assert len(data["accepted"]) == 1 and data["rejected"][0]["reason"] == \
        "not recorded on a school access point"


def test_kiosks_receive_roster_and_each_others_events(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    db.add_device("Gate B", MAC_B)
    ada = db.create_student("001", "Ada", [unit(1), unit(2)])
    first = sync(client(app), [MAC_A], clock, outbox=[event(ada.id)]).json()
    assert first["roster"]["students"][0]["full_name"] == "Ada"
    assert len(first["roster"]["students"][0]["embeddings"]) == 2

    b = sync(client(app), [MAC_B], clock, roster_version=first["roster"]["version"]).json()
    assert "roster" not in b  # already up to date
    assert [e["kind"] for e in b["events"]] == ["IN"] and b["events"][0]["device"] == "Gate A"
    again = sync(client(app), [MAC_B], clock, events_after=b["events"][-1]["seq"]).json()
    assert again["events"] == []


def test_stale_open_sessions_get_no_signout(env):
    db, app, clock = env
    db.add_device("Gate A", MAC_A)
    ada = db.create_student("001", "Ada", [unit(1)])
    sync(client(app), [MAC_A], clock, outbox=[event(ada.id)])
    assert db.close_stale_sessions("17:30", date(2026, 10, 3)) == 1
    [s] = db.sessions_on(date(2026, 10, 2))
    assert s["closed"] and s["flags"] == ["No sign-out"]


# ---- registration jobs -------------------------------------------------------------------

def test_registration_job_lifecycle(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    a = admin(app)
    offline = a.post("/api/admin/register", json={"full_name": "Chidi", "student_no": "005",
                                                  "device_id": dev["id"]})
    assert offline.status_code == 409 and "offline" in offline.json()["detail"]

    sync(client(app), [MAC_A], clock)  # now online
    job = a.post("/api/admin/register", json={"full_name": "Chidi", "student_no": "005",
                                              "device_id": dev["id"]}).json()
    assert job["status"] == "waiting"
    busy = a.post("/api/admin/register", json={"full_name": "X", "student_no": "006",
                                               "device_id": dev["id"]})
    assert busy.status_code == 409

    data = sync(client(app), [MAC_A], clock).json()
    assert data["registration_job"]["full_name"] == "Chidi"
    sync(client(app), [MAC_A], clock, registration={"job_id": job["id"], "active": True,
                                                    "message": "Blink", "progress": 0.4})
    assert a.get(f"/api/admin/register/{job['id']}").json()["status"] == "capturing"

    result = client(app).post(f"/api/device/registration/{job['id']}/result",
                              json={"macs": [MAC_A],
                                    "embeddings": [encode_embedding(unit(5))] * 3}).json()
    assert result["ok"]
    assert a.get(f"/api/admin/register/{job['id']}").json()["status"] == "done"
    assert [s["full_name"] for s in a.get("/api/admin/students").json()] == ["Chidi"]
    assert sync(client(app), [MAC_A], clock).json()["roster"]["students"][0]["full_name"] == "Chidi"


def test_registration_rejects_duplicate_face_and_other_devices(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    db.add_device("Gate B", MAC_B)
    db.create_student("001", "Ada", [unit(1)])
    sync(client(app), [MAC_A], clock)
    a = admin(app)
    job = a.post("/api/admin/register", json={"full_name": "Fake", "student_no": "002",
                                              "device_id": dev["id"]}).json()
    wrong = client(app).post(f"/api/device/registration/{job['id']}/result",
                             json={"macs": [MAC_B], "embeddings": [encode_embedding(unit(1))]})
    assert wrong.status_code == 404
    dup = client(app).post(f"/api/device/registration/{job['id']}/result",
                           json={"macs": [MAC_A], "embeddings": [encode_embedding(unit(1))]}).json()
    assert not dup["ok"] and "already registered as Ada" in dup["message"]
    assert not db.student_no_exists("002")


def test_cancelled_job_is_reported_to_device(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    sync(client(app), [MAC_A], clock)
    a = admin(app)
    job = a.post("/api/admin/register", json={"full_name": "C", "student_no": "7",
                                              "device_id": dev["id"]}).json()
    a.post(f"/api/admin/register/{job['id']}/cancel")
    data = sync(client(app), [MAC_A], clock).json()
    assert data["registration_job"]["status"] == "cancelled"
    late = client(app).post(f"/api/device/registration/{job['id']}/result",
                            json={"macs": [MAC_A], "embeddings": [encode_embedding(unit(3))]})
    assert late.json()["ok"] is False


# ---- admin -------------------------------------------------------------------------------------

def test_admin_routes_need_login(env):
    db, app, _ = env
    c = client(app, ("192.168.1.10", 1))
    for method, path in (("get", "/api/admin/students"), ("get", "/api/admin/devices"),
                         ("post", "/api/admin/devices"), ("get", "/api/admin/settings"),
                         ("get", "/api/admin/sessions")):
        assert getattr(c, method)(path).status_code == 401


def test_settings_roundtrip_and_validation(env):
    db, app, _ = env
    a = admin(app)
    body = {"clock_out_after": "16:45", "wifi_ssid": "SCHOOL2", "wifi_bssids": ["AA-BB-CC-DD-EE-FF"],
            "lat": 6.6, "lon": 3.5, "radius_m": 300}
    saved = a.put("/api/admin/settings", json=body).json()
    assert saved["wifi_bssids"] == ["aa:bb:cc:dd:ee:ff"] and saved["clock_out_after"] == "16:45"
    assert a.get("/api/admin/settings").json()["wifi_ssid"] == "SCHOOL2"
    assert a.put("/api/admin/settings", json={**body, "lat": 123}).status_code == 400
    assert a.put("/api/admin/settings", json={**body, "clock_out_after": "25:00"}).status_code == 400


def test_login_lockout_and_password_change(env):
    db, app, _ = env
    c = client(app, ("192.168.1.10", 1))
    for _ in range(5):
        c.post("/api/admin/login", json={"password": "wrong"})
    assert c.post("/api/admin/login", json={"password": PASSWORD}).status_code == 429

    a = admin(app, ("192.168.1.11", 1))
    r = a.post("/api/admin/password", json={"current": PASSWORD, "new": "new-password-1"})
    assert r.status_code == 200
    assert a.get("/api/admin/students").status_code == 401  # old token revoked


def test_spa_fallback(tmp_path):
    (tmp_path / "index.html").write_text("<div id=app></div>")
    cfg = Config(role="server")
    app = create_server_app(cfg, ServerDB(":memory:"), frontend_dir=tmp_path)
    c = client(app)
    assert c.get("/admin/devices").text == "<div id=app></div>"
    assert c.get("/api/nope").status_code == 404


def test_rate_limit_is_per_device_and_skips_registration_upload(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    db.add_device("Gate B", MAC_B)
    same_ip = client(app)  # both kiosks behind one address
    for _ in range(60):
        assert sync(same_ip, [MAC_A], clock).status_code == 200
    assert sync(same_ip, [MAC_A], clock).status_code == 429
    assert sync(same_ip, [MAC_B], clock).status_code == 200  # the other kiosk is unaffected
    a = admin(app)
    job = a.post("/api/admin/register", json={"full_name": "C", "student_no": "7",
                                              "device_id": dev["id"]}).json()
    result = same_ip.post(f"/api/device/registration/{job['id']}/result",
                          json={"macs": [MAC_A], "embeddings": [encode_embedding(unit(3))]})
    assert result.status_code == 200 and result.json()["ok"]


def test_registration_abandoned_by_kiosk_is_failed(env):
    db, app, clock = env
    dev = db.add_device("Gate A", MAC_A)
    sync(client(app), [MAC_A], clock)
    a = admin(app)
    job = a.post("/api/admin/register", json={"full_name": "C", "student_no": "7",
                                              "device_id": dev["id"]}).json()
    sync(client(app), [MAC_A], clock, registration={"job_id": job["id"], "active": True,
                                                    "message": "Blink"})
    assert a.get(f"/api/admin/register/{job['id']}").json()["status"] == "capturing"
    sync(client(app), [MAC_A], clock)  # kiosk no longer reports it
    after = a.get(f"/api/admin/register/{job['id']}").json()
    assert after["status"] == "failed" and "stopped" in after["message"]


# ---- backlight warning --------------------------------------------------------------------

def test_backlit_kiosk_is_flagged_for_the_it_officer(env):
    db, app, clock = env
    a = admin(app)
    a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A})
    c = client(app)
    today = NOW.date().isoformat()
    sync(c, [MAC_A], clock, lighting={"date": today, "scans": 20, "backlit": 4})
    [dev] = a.get("/api/admin/devices").json()
    assert dev["backlit"] is None                       # 20% is below the 30% bar
    sync(c, [MAC_A], clock, lighting={"date": today, "scans": 20, "backlit": 9})
    [dev] = a.get("/api/admin/devices").json()
    assert dev["backlit"].startswith("9 of 20 scans today")
    sync(c, [MAC_A], clock, lighting={"date": "2026-10-01", "scans": 20, "backlit": 9})
    [dev] = a.get("/api/admin/devices").json()
    assert dev["backlit"] is None                       # yesterday's numbers don't count


def test_old_database_gets_the_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "mac TEXT NOT NULL UNIQUE, added_at TEXT NOT NULL, removed_at TEXT, "
                "last_seen TEXT, last_ip TEXT, last_ssid TEXT, last_bssid TEXT, "
                "app_version TEXT, pending INTEGER NOT NULL DEFAULT 0, alt_ip TEXT, "
                "alt_seen TEXT)")
    old.commit()
    old.close()
    db = ServerDB(path)
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(devices)")}
    assert {"light_date", "light_scans", "light_backlit", "spoof_threshold", "spoof_date",
            "spoof_count"} <= cols
    db.close()


# ---- photos and phones held up ----------------------------------------------------------

def alert(label="phone", student_id=None, **kw):
    return {"id": str(uuid.uuid4()), "ts": NOW.isoformat(), "kind": "object", "label": label,
            "reason": f"{label} detected (0.80)", "student_id": student_id, **kw}


def test_spoof_alerts_are_logged_once_and_counted_for_today(env):
    db, app, clock = env
    a = admin(app)
    a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A})
    sid = db.create_student("008", "Oscar Essoun", [unit(1)]).id
    c = client(app)
    first = alert("phone", sid)
    reply = sync(c, [MAC_A], clock, alerts=[first, alert("photo", kind="photo")]).json()
    assert len(reply["alerts_accepted"]) == 2
    sync(c, [MAC_A], clock, alerts=[first])            # re-sent after a lost reply
    [dev] = a.get("/api/admin/devices").json()
    assert dev["spoof_today"] == 2
    log = [e for e in a.get("/api/admin/log").json() if e["action"] == "spoof_attempt"]
    assert len(log) == 2
    assert any("Phone held up at Gate A (looked like Oscar Essoun, 008)" in e["detail"]
               for e in log)


def test_tuning_steps_and_saved_threshold_reach_the_kiosk(env):
    db, app, clock = env
    a = admin(app)
    dev_id = a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A}).json()["id"]
    c = client(app)
    assert sync(c, [MAC_A], clock).json()["spoof_threshold"] is None

    summary = a.post(f"/api/admin/devices/{dev_id}/tuning", json={"phase": "real"}).json()
    assert summary["active"]["status"] == "waiting"
    job = sync(c, [MAC_A], clock).json()["tuning_job"]
    assert job["phase"] == "real" and job["status"] == "waiting" and job["seconds"] == 30
    job = sync(c, [MAC_A], clock, tuning={"job_id": job["id"], "samples": 10}).json()["tuning_job"]
    assert job["status"] == "running"
    real = [0] * 50
    real[40] = 300
    r = c.post(f"/api/device/tuning/{job['id']}/result", json={"macs": [MAC_A], "histogram": real})
    assert r.json()["ok"]
    assert "tuning_job" not in sync(c, [MAC_A], clock).json()

    a.post(f"/api/admin/devices/{dev_id}/tuning", json={"phase": "spoof"})
    job = sync(c, [MAC_A], clock).json()["tuning_job"]
    spoof = [0] * 50
    spoof[3] = 300
    c.post(f"/api/device/tuning/{job['id']}/result", json={"macs": [MAC_A], "histogram": spoof})
    summary = a.get(f"/api/admin/devices/{dev_id}/tuning").json()
    assert summary["suggestion"]["quality"] == "Good"
    suggested = summary["suggestion"]["threshold"]
    assert 0.08 < suggested < 0.82

    a.post(f"/api/admin/devices/{dev_id}/spoof-threshold", json={"threshold": suggested})
    assert sync(c, [MAC_A], clock).json()["spoof_threshold"] == suggested
    a.post(f"/api/admin/devices/{dev_id}/spoof-threshold", json={"threshold": None})
    assert sync(c, [MAC_A], clock).json()["spoof_threshold"] is None


def test_tuning_step_with_no_faces_fails(env):
    db, app, clock = env
    a = admin(app)
    dev_id = a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A}).json()["id"]
    c = client(app)
    a.post(f"/api/admin/devices/{dev_id}/tuning", json={"phase": "real"})
    job = sync(c, [MAC_A], clock).json()["tuning_job"]
    r = c.post(f"/api/device/tuning/{job['id']}/result",
               json={"macs": [MAC_A], "histogram": [0] * 50}).json()
    assert not r["ok"]
    assert a.get(f"/api/admin/devices/{dev_id}/tuning").json()["steps"]["real"] is None



def test_phone_training_sets_the_kiosk_phone_threshold(env):
    db, app, clock = env
    a = admin(app)
    dev_id = a.post("/api/admin/devices", json={"name": "Gate A", "mac": MAC_A}).json()["id"]
    c = client(app)
    assert sync(c, [MAC_A], clock).json()["object_threshold"] is None
    for phase, bin_no in (("phone", 35), ("clear", 2)):
        a.post(f"/api/admin/devices/{dev_id}/tuning", json={"phase": phase})
        job = sync(c, [MAC_A], clock).json()["tuning_job"]
        assert job["phase"] == phase
        hist = [0] * 50
        hist[bin_no] = 200
        assert c.post(f"/api/device/tuning/{job['id']}/result",
                      json={"macs": [MAC_A], "histogram": hist}).json()["ok"]
    summary = a.get(f"/api/admin/devices/{dev_id}/tuning").json()
    suggestion = summary["object_suggestion"]
    assert suggestion["quality"] == "Good" and summary["suggestion"] is None
    a.post(f"/api/admin/devices/{dev_id}/object-threshold",
           json={"threshold": suggestion["threshold"]})
    assert sync(c, [MAC_A], clock).json()["object_threshold"] == suggestion["threshold"]
    assert a.post(f"/api/admin/devices/{dev_id}/object-threshold",
                  json={"threshold": 0.05}).status_code == 422      # below the floor
    a.post(f"/api/admin/devices/{dev_id}/object-threshold", json={"threshold": None})
    assert sync(c, [MAC_A], clock).json()["object_threshold"] is None
    log = [e["action"] for e in a.get("/api/admin/log").json()]
    assert "object_threshold" in log
