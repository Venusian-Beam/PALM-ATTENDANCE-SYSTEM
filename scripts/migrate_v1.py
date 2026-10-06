"""Move data from the single-kiosk version (v1) to the school server.

Copy the old kiosk's data/pas.db and config.yaml to the server PC, then run (on the
server, after configure_server.py, before any kiosks sync):

    python scripts/migrate_v1.py --old-db C:\\path\\to\\old\\pas.db --old-config C:\\path\\to\\old\\config.yaml

It copies students, face data, attendance, the admin password and log; turns the old
kiosk into the device "Main kiosk"; and copies its Wi-Fi and location settings.
"""
from __future__ import annotations

import argparse
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

import yaml
from _common import ROOT  # noqa: F401  (adds the project to sys.path)

from backend.central.db import ServerDB
from backend.config import DEFAULT_CONFIG_PATH, load_config
from backend.rules import IN, OUT
from backend.security.device import normalize_mac


def migrate(old_db: Path, old_config: Path | None, db: ServerDB, default_cutoff: str) -> dict:
    old = sqlite3.connect(str(old_db))
    old.row_factory = sqlite3.Row
    if db.list_students():
        raise SystemExit("The server already has students; migrate into an empty database.")
    raw = yaml.safe_load(old_config.read_text(encoding="utf-8")) if old_config else {}

    macs = (raw.get("device") or {}).get("allowed_macs") or []
    device = db.add_device("Main kiosk", normalize_mac(macs[0])) if macs else None
    wifi, geo = raw.get("wifi") or {}, raw.get("geofence") or {}
    if wifi or geo:
        db.set_school_settings({"wifi_ssid": wifi.get("ssid", ""),
                                "wifi_bssids": wifi.get("allowed_bssids") or [],
                                "lat": geo.get("lat"), "lon": geo.get("lon"),
                                "radius_m": geo.get("radius_m", 200)})

    conn = db._conn  # direct inserts keep the original student ids
    with conn:
        for r in old.execute("SELECT * FROM students"):
            conn.execute("INSERT INTO students (id, student_no, full_name, created_at) "
                         "VALUES (?, ?, ?, ?)",
                         (r["id"], r["student_no"], r["full_name"], r["created_at"]))
        for r in old.execute("SELECT student_id, embedding FROM face_embeddings"):
            conn.execute("INSERT INTO face_embeddings (student_id, embedding) VALUES (?, ?)",
                         (r["student_id"], r["embedding"]))
        for key in ("admin_password_hash", "clock_out_after"):
            row = old.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                             (key, row[0]))
        for r in old.execute("SELECT time, ip, action, detail FROM admin_log ORDER BY id"):
            conn.execute("INSERT INTO admin_log (time, ip, action, detail) VALUES (?, ?, ?, ?)",
                         tuple(r))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('roster_version', '1')")

    events = 0
    name = device["name"] if device else "Main kiosk"
    with conn:
        for s in old.execute("SELECT * FROM sessions ORDER BY id"):
            pairs = [(IN, s["clock_in"])] + ([(OUT, s["clock_out"])] if s["clock_out"] else [])
            for kind, clock in pairs:
                ts = datetime.fromisoformat(f"{s['date']}T{clock}")
                conn.execute(
                    "INSERT INTO events (uuid, student_id, kind, ts, date, device_id, "
                    "device_name, ssid, lat, lon, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), s["student_id"], kind, ts.isoformat(), s["date"],
                     device["id"] if device else None, name, s["ssid"], s["lat"], s["lon"],
                     datetime.now().isoformat(timespec="seconds")))
                events += 1
    db.rebuild_all(db.cutoff(default_cutoff), date.today())
    old.close()
    return {"students": len(db.list_students()), "events": events,
            "device": device["mac"] if device else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old-db", required=True, type=Path)
    parser.add_argument("--old-config", type=Path)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args()
    cfg = load_config(args.config)
    if cfg.role != "server":
        raise SystemExit("Run this on the server PC (its config.yaml must have role: server).")
    db = ServerDB(cfg.database)
    result = migrate(args.old_db, args.old_config, db, cfg.attendance.clock_out_after)
    db.close()
    print(f"Moved {result['students']} students and {result['events']} attendance events.")
    if result["device"]:
        print(f"The old kiosk ({result['device']}) is now the device 'Main kiosk'.")


if __name__ == "__main__":
    main()
