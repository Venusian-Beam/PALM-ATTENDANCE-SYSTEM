"""A kiosk's local SQLite cache (data/kiosk.db).

Holds the roster from the server, today's events (from every kiosk), the outbox of
events not yet uploaded, and the last known settings and approval. This is what lets
a kiosk keep working while the server is unreachable.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from backend.rules import Event, Session, build_sessions

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY,
    student_no  TEXT NOT NULL,
    full_name   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    student_id  INTEGER NOT NULL,
    embedding   BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    uuid        TEXT PRIMARY KEY,
    student_id  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    date        TEXT NOT NULL,
    cutoff      TEXT,
    device      TEXT,
    ssid        TEXT,
    bssid       TEXT,
    lat         REAL,
    lon         REAL,
    pending     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(date, student_id);
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    uuid        TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL,
    reason      TEXT,
    student_id  INTEGER
);
"""


@dataclass
class RosterStudent:
    id: int
    student_no: str
    full_name: str


class KioskStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
        self._roster_version = self.get_int("roster_version", -1)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- meta ------------------------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key)
        return int(value) if value is not None else default

    def set(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def settings(self) -> dict[str, Any] | None:
        raw = self.get("settings")
        return json.loads(raw) if raw else None

    def set_settings(self, settings: dict[str, Any]) -> None:
        self.set("settings", json.dumps(settings))

    # ---- roster -----------------------------------------------------------------

    @property
    def roster_version(self) -> int:
        return self._roster_version

    def replace_roster(self, roster: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM students")
            self._conn.execute("DELETE FROM embeddings")
            for s in roster["students"]:
                self._conn.execute(
                    "INSERT INTO students (id, student_no, full_name) VALUES (?, ?, ?)",
                    (s["id"], s["student_no"], s["full_name"]))
                self._conn.executemany(
                    "INSERT INTO embeddings (student_id, embedding) VALUES (?, ?)",
                    [(s["id"], base64.b64decode(e)) for e in s["embeddings"]])
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('roster_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(roster["version"]),))
        self._roster_version = int(roster["version"])

    def get_student(self, student_id: int) -> RosterStudent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM students WHERE id = ?",
                                     (student_id,)).fetchone()
        return RosterStudent(row["id"], row["student_no"], row["full_name"]) if row else None

    def student_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]

    def load_embeddings(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            rows = self._conn.execute("SELECT student_id, embedding FROM embeddings").fetchall()
        if not rows:
            return np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        return ids, np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])

    # ---- events -------------------------------------------------------------------

    def record_event(self, student_id: int, kind: str, when: datetime, cutoff: str,
                     device: str | None, context: dict[str, Any]) -> str:
        """Save a new event from this kiosk; it waits in the outbox until uploaded."""
        event_id = str(uuid.uuid4())
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (uuid, student_id, kind, ts, date, cutoff, device, ssid,"
                " bssid, lat, lon, pending) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (event_id, student_id, kind, when.isoformat(timespec="seconds"),
                 when.date().isoformat(), cutoff, device, context.get("ssid"),
                 context.get("bssid"), context.get("lat"), context.get("lon")))
        return event_id

    def sessions_for(self, student_id: int, day: date, default_cutoff: time) -> list[Session]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT uuid, student_id, kind, ts, cutoff, device FROM events "
                "WHERE student_id = ? AND date = ?", (student_id, day.isoformat())).fetchall()
        events = [Event(r["uuid"], r["student_id"], r["kind"], datetime.fromisoformat(r["ts"]),
                        r["cutoff"], r["device"]) for r in rows]
        sessions, _ = build_sessions(events, default_cutoff, day)
        return sessions

    def outbox(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT uuid, student_id, kind, ts, cutoff, ssid, bssid, lat, lon FROM events "
                "WHERE pending = 1 ORDER BY ts LIMIT ?", (limit,)).fetchall()
        return [{"id": r["uuid"], "student_id": r["student_id"], "kind": r["kind"],
                 "ts": r["ts"], "cutoff": r["cutoff"], "ssid": r["ssid"], "bssid": r["bssid"],
                 "lat": r["lat"], "lon": r["lon"]} for r in rows]

    # ---- spoof alerts (a photo / phone was held up), waiting to be uploaded ---------------

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO alerts (uuid, ts, kind, label, reason, student_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), alert["ts"], alert["kind"], alert["label"],
                 alert.get("reason"), alert.get("student_id")))

    def alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT uuid, ts, kind, label, reason, student_id FROM alerts ORDER BY ts LIMIT ?",
                (limit,)).fetchall()
        return [{"id": r["uuid"], "ts": r["ts"], "kind": r["kind"], "label": r["label"],
                 "reason": r["reason"], "student_id": r["student_id"]} for r in rows]

    def alerts_uploaded(self, ids: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM alerts WHERE uuid = ?", [(i,) for i in ids])

    def pending_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events WHERE pending = 1").fetchone()[0]

    def mark_uploaded(self, accepted: list[str], rejected: list[str]) -> None:
        """Accepted events stay (for today's decisions); rejected ones are dropped."""
        with self._lock, self._conn:
            self._conn.executemany("UPDATE events SET pending = 0 WHERE uuid = ?",
                                   [(i,) for i in accepted])
            self._conn.executemany("DELETE FROM events WHERE uuid = ? AND pending = 1",
                                   [(i,) for i in rejected])

    def merge_server_events(self, events: list[dict[str, Any]]) -> None:
        """Events from the server (including other kiosks' scans)."""
        with self._lock, self._conn:
            for ev in events:
                ts = datetime.fromisoformat(ev["ts"])
                self._conn.execute(
                    "INSERT INTO events (uuid, student_id, kind, ts, date, cutoff, device, pending)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(uuid) DO UPDATE SET pending = 0",
                    (ev["id"], ev["student_id"], ev["kind"], ts.isoformat(timespec="seconds"),
                     ts.date().isoformat(), ev.get("cutoff"), ev.get("device")))

    def prune(self, today: date) -> None:
        """Drop uploaded events older than yesterday; they're kept on the server."""
        cutoff = (today - timedelta(days=1)).isoformat()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM events WHERE pending = 0 AND date < ?", (cutoff,))
