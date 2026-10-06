import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

from backend.central.db import ServerDB

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from migrate_v1 import migrate  # noqa: E402

V1_SCHEMA = """
CREATE TABLE students (id INTEGER PRIMARY KEY, student_no TEXT, full_name TEXT, created_at TEXT);
CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, student_id INTEGER, embedding BLOB);
CREATE TABLE sessions (id INTEGER PRIMARY KEY, student_id INTEGER, date TEXT, clock_in TEXT,
    clock_out TEXT, in_flag TEXT, out_flag TEXT, closed INTEGER, device_mac TEXT, ssid TEXT,
    lat REAL, lon REAL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE admin_log (id INTEGER PRIMARY KEY, time TEXT, ip TEXT, action TEXT, detail TEXT);
"""


def test_migrate_v1(tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO students VALUES (7, '001', 'Ada', '2026-09-01T08:00:00')")
    conn.execute("INSERT INTO face_embeddings VALUES (1, 7, ?)",
                 (np.ones(128, dtype=np.float32).tobytes(),))
    conn.execute("INSERT INTO sessions VALUES (1, 7, '2026-09-30', '07:40:00', '14:00:00',"
                 " NULL, NULL, 1, 'x', 'SCHOOL', 6.5, 3.4)")
    conn.execute("INSERT INTO sessions VALUES (2, 7, '2026-10-01', '07:45:00', NULL,"
                 " NULL, 'No sign-out', 1, 'x', 'SCHOOL', 6.5, 3.4)")
    conn.execute("INSERT INTO settings VALUES ('admin_password_hash', 'pbkdf2_sha256$1$aa$bb')")
    conn.execute("INSERT INTO admin_log VALUES (1, '2026-09-01T08:00:00', '127.0.0.1', 'login', '')")
    conn.commit()
    conn.close()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("device:\n  allowed_macs: ['00-1A-2B-3C-4D-5E']\n"
                   "wifi:\n  ssid: SCHOOL\n  allowed_bssids: []\n"
                   "geofence:\n  lat: 6.5\n  lon: 3.4\n  radius_m: 250\n")

    db = ServerDB(":memory:")
    result = migrate(old, cfg, db, "17:30")
    assert result == {"students": 1, "events": 3, "device": "00:1a:2b:3c:4d:5e"}
    assert db.get_student(7).samples == 1
    assert db.get_setting("admin_password_hash").startswith("pbkdf2")
    assert db.school_settings()["radius_m"] == 250
    assert db.list_devices()[0]["name"] == "Main kiosk"
    [done] = db.sessions_on(date(2026, 9, 30))
    assert (done["clock_in"], done["clock_out"]) == ("07:40:00", "14:00:00")
    [stale] = db.sessions_on(date(2026, 10, 1))
    assert stale["flags"] == ["No sign-out"]
