"""The server's SQLite database: students, faces, devices, events, sessions, jobs.

Only the server writes to this file. Kiosks send events through the device API.
Sessions are derived from events (see backend/rules.py) and rebuilt whenever events
for a student's day arrive.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from backend.rules import Event, build_sessions, parse_hhmm
from backend.security.device import normalize_mac

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY,
    student_no  TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS face_embeddings (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    mac          TEXT NOT NULL UNIQUE,
    added_at     TEXT NOT NULL,
    removed_at   TEXT,
    last_seen    TEXT,
    last_ip      TEXT,
    last_ssid    TEXT,
    last_bssid   TEXT,
    app_version  TEXT,
    pending      INTEGER NOT NULL DEFAULT 0,
    alt_ip       TEXT,
    alt_seen     TEXT,
    light_date   TEXT,
    light_scans  INTEGER,
    light_backlit INTEGER,
    spoof_threshold REAL,
    object_threshold REAL,
    spoof_date   TEXT,
    spoof_count  INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid         TEXT NOT NULL UNIQUE,
    student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    ts           TEXT NOT NULL,
    date         TEXT NOT NULL,
    cutoff       TEXT,
    device_id    INTEGER,
    device_name  TEXT,
    ssid         TEXT,
    bssid        TEXT,
    lat          REAL,
    lon          REAL,
    received_at  TEXT NOT NULL,
    status       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(date, student_id);
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    clock_in    TEXT NOT NULL,
    clock_out   TEXT,
    in_flag     TEXT,
    out_flag    TEXT,
    closed      INTEGER NOT NULL,
    in_device   TEXT,
    out_device  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(date, student_id);
CREATE TABLE IF NOT EXISTS registration_jobs (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER NOT NULL REFERENCES devices(id),
    student_no  TEXT NOT NULL,
    full_name   TEXT NOT NULL,
    student_id  INTEGER,
    status      TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    progress    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    uuid        TEXT NOT NULL UNIQUE,
    device_id   INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL,
    reason      TEXT,
    student_id  INTEGER,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tuning_jobs (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER NOT NULL REFERENCES devices(id),
    phase       TEXT NOT NULL,
    seconds     REAL NOT NULL,
    status      TEXT NOT NULL,
    histogram   TEXT,
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_log (
    id      INTEGER PRIMARY KEY,
    time    TEXT NOT NULL,
    ip      TEXT,
    action  TEXT NOT NULL,
    detail  TEXT
);
"""

ROSTER_VERSION_KEY = "roster_version"
SCHOOL_KEY = "school"
CUTOFF_KEY = "clock_out_after"
ACTIVE_JOB_STATES = ("waiting", "capturing")


class DuplicateStudentError(Exception):
    pass


class DeviceError(Exception):
    pass


@dataclass
class Student:
    id: int
    student_no: str
    full_name: str
    created_at: str
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def encode_embedding(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).ravel().tobytes()).decode()


def decode_embedding(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float32)


class ServerDB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()

    # Columns added after the first release; older databases get them on start-up.
    _ADDED_COLUMNS = {"devices": (("light_date", "TEXT"), ("light_scans", "INTEGER"),
                                  ("light_backlit", "INTEGER"), ("spoof_threshold", "REAL"),
                                  ("object_threshold", "REAL"),
                                  ("spoof_date", "TEXT"), ("spoof_count", "INTEGER"))}

    def _migrate(self) -> None:
        for table, columns in self._ADDED_COLUMNS.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup_to(self, target: Path) -> None:
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    # ---- settings + admin log ------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?",
                                     (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def school_settings(self) -> dict[str, Any]:
        raw = self.get_setting(SCHOOL_KEY)
        data = json.loads(raw) if raw else {}
        return {
            "wifi_ssid": data.get("wifi_ssid", ""),
            "wifi_bssids": data.get("wifi_bssids", []),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "radius_m": data.get("radius_m", 200.0),
        }

    def set_school_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "wifi_ssid": str(settings.get("wifi_ssid") or "").strip(),
            "wifi_bssids": sorted({normalize_mac(b) for b in settings.get("wifi_bssids") or []}),
            "lat": None if settings.get("lat") in (None, "") else float(settings["lat"]),
            "lon": None if settings.get("lon") in (None, "") else float(settings["lon"]),
            "radius_m": float(settings.get("radius_m") or 200.0),
        }
        if clean["lat"] is not None and not -90 <= clean["lat"] <= 90:
            raise ValueError("Latitude must be between -90 and 90.")
        if clean["lon"] is not None and not -180 <= clean["lon"] <= 180:
            raise ValueError("Longitude must be between -180 and 180.")
        if not 10 <= clean["radius_m"] <= 5000:
            raise ValueError("The radius must be between 10 and 5000 metres.")
        self.set_setting(SCHOOL_KEY, json.dumps(clean))
        return clean

    def cutoff(self, default: str = "17:30") -> str:
        return self.get_setting(CUTOFF_KEY) or default

    def log_admin(self, ip: str | None, action: str, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO admin_log (time, ip, action, detail) VALUES (?, ?, ?, ?)",
                (now_iso(), ip, action, detail))

    def admin_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT time, ip, action, detail FROM admin_log ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- students + roster -----------------------------------------------------

    def roster_version(self) -> int:
        return int(self.get_setting(ROSTER_VERSION_KEY) or 0)

    def _bump_roster(self) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) "
            "DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
            (ROSTER_VERSION_KEY,))

    def create_student(self, student_no: str, full_name: str,
                       embeddings: Iterable[np.ndarray]) -> Student:
        student_no, full_name = student_no.strip(), full_name.strip()
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(
                    "INSERT INTO students (student_no, full_name, created_at) VALUES (?, ?, ?)",
                    (student_no, full_name, now_iso()))
            except sqlite3.IntegrityError:
                raise DuplicateStudentError(
                    f"Student ID {student_no} is already registered.") from None
            self._insert_embeddings(cur.lastrowid, embeddings)
            self._bump_roster()
        return self.get_student(cur.lastrowid)

    def replace_embeddings(self, student_id: int, embeddings: Iterable[np.ndarray]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM face_embeddings WHERE student_id = ?", (student_id,))
            self._insert_embeddings(student_id, embeddings)
            self._bump_roster()

    def _insert_embeddings(self, student_id: int, embeddings: Iterable[np.ndarray]) -> None:
        self._conn.executemany(
            "INSERT INTO face_embeddings (student_id, embedding) VALUES (?, ?)",
            [(student_id, np.asarray(e, dtype=np.float32).ravel().tobytes())
             for e in embeddings])

    def student_no_exists(self, student_no: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM students WHERE student_no = ?",
                                      (student_no.strip(),)).fetchone() is not None

    def delete_student(self, student_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
            if cur.rowcount:
                self._bump_roster()
        return cur.rowcount > 0

    def get_student(self, student_id: int) -> Student | None:
        rows = self._students("WHERE s.id = ?", (student_id,))
        return rows[0] if rows else None

    def list_students(self) -> list[Student]:
        return self._students("", ())

    def _students(self, where: str, params: tuple) -> list[Student]:
        sql = ("SELECT s.*, (SELECT COUNT(*) FROM face_embeddings f WHERE f.student_id = s.id) "
               f"AS samples FROM students s {where} ORDER BY s.full_name COLLATE NOCASE")
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Student(r["id"], r["student_no"], r["full_name"], r["created_at"], r["samples"])
                for r in rows]

    def load_embeddings(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT student_id, embedding FROM face_embeddings").fetchall()
        if not rows:
            return np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        return ids, np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])

    def roster(self) -> dict[str, Any]:
        """Everything a kiosk needs to recognise students."""
        with self._lock:
            version = self.roster_version()
            students = self._conn.execute(
                "SELECT id, student_no, full_name FROM students").fetchall()
            embeddings = self._conn.execute(
                "SELECT student_id, embedding FROM face_embeddings").fetchall()
        faces: dict[int, list[str]] = {}
        for r in embeddings:
            faces.setdefault(r[0], []).append(base64.b64encode(r[1]).decode())
        return {"version": version,
                "students": [{"id": s["id"], "student_no": s["student_no"],
                              "full_name": s["full_name"], "embeddings": faces.get(s["id"], [])}
                             for s in students]}

    # ---- devices ---------------------------------------------------------------

    def add_device(self, name: str, mac: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise DeviceError("Give the device a name.")
        try:
            mac = normalize_mac(mac)
        except ValueError as exc:
            raise DeviceError(str(exc)) from None
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
            if row and row["removed_at"] is None:
                raise DeviceError(f"{mac} is already added as '{row['name']}'.")
            if row:  # re-adding a removed device keeps its history
                self._conn.execute(
                    "UPDATE devices SET name = ?, removed_at = NULL, added_at = ? WHERE id = ?",
                    (name, now_iso(), row["id"]))
                device_id = row["id"]
            else:
                device_id = self._conn.execute(
                    "INSERT INTO devices (name, mac, added_at) VALUES (?, ?, ?)",
                    (name, mac, now_iso())).lastrowid
        return self.get_device(device_id)

    def remove_device(self, device_id: int, now: datetime | None = None) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE devices SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
                (now_iso(now), device_id))
            self._conn.execute(
                "UPDATE registration_jobs SET status = 'cancelled', message = 'Device removed.' "
                f"WHERE device_id = ? AND status IN {ACTIVE_JOB_STATES}", (device_id,))
        return cur.rowcount > 0

    def get_device(self, device_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id = ?",
                                     (device_id,)).fetchone()
        return dict(row) if row else None

    def find_device(self, macs: Iterable[str]) -> dict[str, Any] | None:
        """The device matching any of these MACs (active ones first)."""
        normalized = []
        for m in macs:
            try:
                normalized.append(normalize_mac(m))
            except ValueError:
                continue
        if not normalized:
            return None
        marks = ",".join("?" * len(normalized))
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM devices WHERE mac IN ({marks}) "
                "ORDER BY removed_at IS NOT NULL, id LIMIT 1", normalized).fetchone()
        return dict(row) if row else None

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY removed_at IS NOT NULL, name").fetchall()
        return [dict(r) for r in rows]

    def touch_device(self, device: dict[str, Any], ip: str, ssid: str | None,
                     bssid: str | None, version: str | None, pending: int,
                     now: datetime, lighting: dict[str, Any] | None = None) -> None:
        """Record a sync; remember a second IP seen within 10 minutes (copied MAC?)."""
        alt_ip, alt_seen = device.get("alt_ip"), device.get("alt_seen")
        last_seen = device.get("last_seen")
        if (device.get("last_ip") and device["last_ip"] != ip and last_seen
                and now - datetime.fromisoformat(last_seen) < timedelta(minutes=10)):
            alt_ip, alt_seen = device["last_ip"], last_seen
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE devices SET last_seen = ?, last_ip = ?, last_ssid = ?, last_bssid = ?, "
                "app_version = ?, pending = ?, alt_ip = ?, alt_seen = ? WHERE id = ?",
                (now_iso(now), ip, ssid, bssid, version, pending, alt_ip, alt_seen,
                 device["id"]))
            if lighting:
                self._conn.execute(
                    "UPDATE devices SET light_date = ?, light_scans = ?, light_backlit = ? "
                    "WHERE id = ?",
                    (lighting["date"], lighting["scans"], lighting["backlit"], device["id"]))

    # ---- events + sessions -------------------------------------------------------

    def add_events(self, device: dict[str, Any], events: list[dict[str, Any]],
                   default_cutoff: str, today: date) -> list[str]:
        """Store validated events (idempotent) and rebuild the affected days."""
        stored, affected = [], set()
        with self._lock, self._conn:
            for ev in events:
                ts = datetime.fromisoformat(ev["ts"])
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO events (uuid, student_id, kind, ts, date, cutoff,"
                    " device_id, device_name, ssid, bssid, lat, lon, received_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ev["id"], ev["student_id"], ev["kind"], ts.isoformat(timespec="seconds"),
                     ts.date().isoformat(), ev.get("cutoff"), device["id"], device["name"],
                     ev.get("ssid"), ev.get("bssid"), ev.get("lat"), ev.get("lon"), now_iso()))
                stored.append(ev["id"])
                if cur.rowcount:
                    affected.add((ev["student_id"], ts.date()))
            for student_id, day in affected:
                self._rebuild_day(student_id, day, default_cutoff, today)
        return stored

    def _day_events(self, student_id: int, day: date) -> list[Event]:
        rows = self._conn.execute(
            "SELECT uuid, student_id, kind, ts, cutoff, device_name FROM events "
            "WHERE student_id = ? AND date = ?", (student_id, day.isoformat())).fetchall()
        return [Event(r["uuid"], r["student_id"], r["kind"], datetime.fromisoformat(r["ts"]),
                      r["cutoff"], r["device_name"]) for r in rows]

    def _rebuild_day(self, student_id: int, day: date, default_cutoff: str, today: date) -> None:
        sessions, statuses = build_sessions(self._day_events(student_id, day),
                                            parse_hhmm(default_cutoff), today)
        self._conn.execute("DELETE FROM sessions WHERE student_id = ? AND date = ?",
                           (student_id, day.isoformat()))
        self._conn.executemany(
            "INSERT INTO sessions (student_id, date, clock_in, clock_out, in_flag, out_flag,"
            " closed, in_device, out_device) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(s.student_id, s.date, s.clock_in, s.clock_out, s.in_flag, s.out_flag,
              int(s.closed), s.in_device, s.out_device) for s in sessions])
        self._conn.executemany("UPDATE events SET status = ? WHERE uuid = ?",
                               [(status, uuid) for uuid, status in statuses.items()])

    def rebuild_all(self, default_cutoff: str, today: date) -> None:
        with self._lock, self._conn:
            days = self._conn.execute(
                "SELECT DISTINCT student_id, date FROM events").fetchall()
            for r in days:
                self._rebuild_day(r[0], date.fromisoformat(r[1]), default_cutoff, today)

    def close_stale_sessions(self, default_cutoff: str, today: date) -> int:
        """Rebuild days that still have an open session, so they get 'No sign-out'."""
        with self._lock, self._conn:
            stale = self._conn.execute(
                "SELECT DISTINCT student_id, date FROM sessions WHERE closed = 0 AND date < ?",
                (today.isoformat(),)).fetchall()
            for r in stale:
                self._rebuild_day(r[0], date.fromisoformat(r[1]), default_cutoff, today)
        return len(stale)

    def events_after(self, seq: int, day: date, limit: int = 2000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, uuid, student_id, kind, ts, cutoff, device_name FROM events "
                "WHERE seq > ? AND date = ? ORDER BY seq LIMIT ?",
                (seq, day.isoformat(), limit)).fetchall()
        return [{"seq": r["seq"], "id": r["uuid"], "student_id": r["student_id"],
                 "kind": r["kind"], "ts": r["ts"], "cutoff": r["cutoff"],
                 "device": r["device_name"]} for r in rows]

    def latest_seq(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    def sessions_on(self, day: date) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT x.*, s.student_no, s.full_name FROM sessions x "
                "JOIN students s ON s.id = x.student_id WHERE x.date = ? "
                "ORDER BY x.clock_in, x.id", (day.isoformat(),)).fetchall()
        return [{"id": r["id"], "student_id": r["student_id"], "student_no": r["student_no"],
                 "full_name": r["full_name"], "clock_in": r["clock_in"],
                 "clock_out": r["clock_out"], "closed": bool(r["closed"]),
                 "in_device": r["in_device"], "out_device": r["out_device"],
                 "flags": [f for f in (r["in_flag"], r["out_flag"]) if f]} for r in rows]

    # ---- registration jobs -----------------------------------------------------

    def create_job(self, device_id: int, student_no: str, full_name: str,
                   student_id: int | None = None) -> dict[str, Any]:
        with self._lock, self._conn:
            busy = self._conn.execute(
                f"SELECT 1 FROM registration_jobs WHERE device_id = ? AND status IN "
                f"{ACTIVE_JOB_STATES}", (device_id,)).fetchone()
            if busy:
                raise DeviceError("That kiosk is already registering someone.")
            job_id = self._conn.execute(
                "INSERT INTO registration_jobs (device_id, student_no, full_name, student_id,"
                " status, message, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'waiting', 'Waiting for the kiosk…', ?, ?)",
                (device_id, student_no.strip(), full_name.strip(), student_id, now_iso(),
                 now_iso())).lastrowid
        return self.get_job(job_id)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT j.*, d.name AS device_name FROM registration_jobs j "
                "JOIN devices d ON d.id = j.device_id WHERE j.id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def job_for_device(self, device_id: int) -> dict[str, Any] | None:
        """The newest unfinished job for a device, including one just cancelled."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM registration_jobs WHERE device_id = ? AND (status IN "
                f"{ACTIVE_JOB_STATES} OR (status = 'cancelled' AND updated_at >= ?)) "
                "ORDER BY id DESC LIMIT 1",
                (device_id, now_iso(datetime.now() - timedelta(minutes=2)))).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: int, status: str, message: str = "",
                   progress: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE registration_jobs SET status = ?, message = ?, progress = ?, "
                "updated_at = ? WHERE id = ?", (status, message, progress, now_iso(), job_id))

    # ---- photo / phone alerts ----------------------------------------------------------

    def add_alerts(self, device: dict[str, Any], alerts: list[dict[str, Any]],
                   now: datetime) -> list[tuple[str, dict[str, Any]]]:
        """Store alerts (idempotent). Returns (uuid, alert) for each one that is new."""
        new = []
        with self._lock, self._conn:
            for a in alerts:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO alerts (uuid, device_id, ts, kind, label, reason,"
                    " student_id, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (a["id"], device["id"], a["ts"], a["kind"], a["label"], a.get("reason"),
                     a.get("student_id"), now_iso(now)))
                if cur.rowcount:
                    new.append((a["id"], a))
            if new:
                today = now.date().isoformat()
                row = self._conn.execute("SELECT spoof_date, spoof_count FROM devices WHERE id = ?",
                                         (device["id"],)).fetchone()
                count = (row["spoof_count"] or 0) if row and row["spoof_date"] == today else 0
                self._conn.execute(
                    "UPDATE devices SET spoof_date = ?, spoof_count = ? WHERE id = ?",
                    (today, count + len(new), device["id"]))
        return new

    def set_spoof_threshold(self, device_id: int, threshold: float | None) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE devices SET spoof_threshold = ? WHERE id = ?",
                               (threshold, device_id))

    def set_object_threshold(self, device_id: int, threshold: float | None) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE devices SET object_threshold = ? WHERE id = ?",
                               (threshold, device_id))

    # ---- kiosk tuning jobs ----------------------------------------------------------------

    def create_tuning_job(self, device_id: int, phase: str, seconds: float) -> dict[str, Any]:
        with self._lock, self._conn:
            busy = self._conn.execute(
                "SELECT 1 FROM registration_jobs WHERE device_id = ? AND status IN "
                f"{ACTIVE_JOB_STATES}", (device_id,)).fetchone()
            if busy:
                raise DeviceError("That kiosk is registering someone. Try again afterwards.")
            self._conn.execute(
                "UPDATE tuning_jobs SET status = 'cancelled', updated_at = ? "
                "WHERE device_id = ? AND status IN ('waiting', 'running')", (now_iso(), device_id))
            job_id = self._conn.execute(
                "INSERT INTO tuning_jobs (device_id, phase, seconds, status, message,"
                " created_at, updated_at) VALUES (?, ?, ?, 'waiting', 'Waiting for the kiosk…',"
                " ?, ?)", (device_id, phase, seconds, now_iso(), now_iso())).lastrowid
        return self.get_tuning_job(job_id)

    def get_tuning_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tuning_jobs WHERE id = ?",
                                     (job_id,)).fetchone()
        return self._tuning_row(row)

    @staticmethod
    def _tuning_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        job["histogram"] = json.loads(job["histogram"]) if job["histogram"] else None
        return job

    def active_tuning_job(self, device_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tuning_jobs WHERE device_id = ? AND status IN "
                "('waiting', 'running') ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        return self._tuning_row(row)

    def latest_tuning(self, device_id: int, phase: str) -> dict[str, Any] | None:
        """The newest finished step of this kind for a device."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tuning_jobs WHERE device_id = ? AND phase = ? AND status = 'done' "
                "ORDER BY id DESC LIMIT 1", (device_id, phase)).fetchone()
        return self._tuning_row(row)

    def update_tuning_job(self, job_id: int, status: str, message: str = "",
                          histogram: list[int] | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tuning_jobs SET status = ?, message = ?, "
                "histogram = COALESCE(?, histogram), updated_at = ? WHERE id = ?",
                (status, message, json.dumps(histogram) if histogram is not None else None,
                 now_iso(), job_id))
