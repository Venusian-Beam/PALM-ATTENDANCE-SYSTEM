"""The school server: admin portal API, device (kiosk) sync API, and the Vue app.

Devices are approved by MAC address in the admin portal. Each sync the kiosk sends
its MACs; unknown or removed MACs are refused.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import auth
from backend.certs import cert_fingerprint
from backend.central.db import (DeviceError, DuplicateStudentError, ServerDB, decode_embedding)
from backend.central.tuning import (BINS, OBJECT_HIGHEST, OBJECT_LOWEST,
                                    choose_object_threshold, choose_threshold)
from backend.config import ROOT, ConfigError, parse_hhmm
from backend.face.engine import best_match, normalize
from backend.rules import IN, OUT
from backend.security.device import normalize_mac
from backend.security.location import haversine_m

log = logging.getLogger(__name__)

FRONTEND_DIST = ROOT / "frontend" / "dist"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
ONLINE_SECONDS = 20
MAX_CLOCK_SKEW_S = 120
GEOFENCE_SLACK_M = 100
APP_VERSION = "2.0"


# ---- request bodies ------------------------------------------------------------

class LoginBody(BaseModel):
    password: str = Field(max_length=200)


class RegisterBody(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    student_no: str = Field(min_length=1, max_length=40)
    device_id: int


class RecaptureBody(BaseModel):
    device_id: int


class PasswordBody(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class SettingsBody(BaseModel):
    clock_out_after: str = Field(max_length=5)
    wifi_ssid: str = Field(default="", max_length=64)
    wifi_bssids: list[str] = Field(default_factory=list, max_length=50)
    lat: float | None = None
    lon: float | None = None
    radius_m: float = 200.0


class DeviceBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    mac: str = Field(min_length=12, max_length=20)


class EventIn(BaseModel):
    id: str = Field(min_length=8, max_length=64)
    student_id: int
    kind: Literal["IN", "OUT"]
    ts: str = Field(max_length=40)
    cutoff: str | None = Field(default=None, max_length=5)
    ssid: str | None = Field(default=None, max_length=64)
    bssid: str | None = Field(default=None, max_length=20)
    lat: float | None = None
    lon: float | None = None


class RegistrationReport(BaseModel):
    job_id: int
    active: bool
    message: str = Field(default="", max_length=300)
    progress: float | None = None


class LightingReport(BaseModel):
    """Today's scans at a kiosk, and how many had bright light behind the student."""
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scans: int = Field(ge=0, le=100_000)
    backlit: int = Field(ge=0, le=100_000)


# A kiosk is flagged "Backlit" when at least this many of today's scans were backlit.
BACKLIT_MIN_SCANS = 10
BACKLIT_SHARE = 0.3


class AlertIn(BaseModel):
    """A photo, phone or screen held up at a kiosk (no image is ever sent)."""
    id: str = Field(min_length=1, max_length=64)
    ts: str = Field(max_length=40)
    kind: Literal["object", "photo"]
    label: str = Field(max_length=20)
    reason: str | None = Field(default=None, max_length=200)
    student_id: int | None = None


class TuningReport(BaseModel):
    job_id: int
    samples: int = 0


TUNING_SECONDS = 30.0


class SyncBody(BaseModel):
    macs: list[str] = Field(max_length=32)
    device_time: str = Field(max_length=40)
    app_version: str = Field(default="", max_length=20)
    ssid: str | None = Field(default=None, max_length=64)
    bssid: str | None = Field(default=None, max_length=20)
    outbox: list[EventIn] = Field(default_factory=list, max_length=1000)
    roster_version: int = -1
    events_after: int = 0
    pending: int = 0
    registration: RegistrationReport | None = None
    lighting: LightingReport | None = None
    alerts: list[AlertIn] = Field(default_factory=list, max_length=200)
    tuning: TuningReport | None = None


class TuningResult(BaseModel):
    macs: list[str] = Field(max_length=32)
    histogram: list[int] = Field(min_length=BINS, max_length=BINS)


class TuningStart(BaseModel):
    # real / spoof: photo detection; phone / clear: phone training
    phase: Literal["real", "spoof", "phone", "clear"]


class ThresholdBody(BaseModel):
    threshold: float | None = Field(default=None, ge=0.05, le=0.95)


class ObjectThresholdBody(BaseModel):
    threshold: float | None = Field(default=None, ge=OBJECT_LOWEST, le=OBJECT_HIGHEST)


class RegistrationResult(BaseModel):
    macs: list[str] = Field(max_length=32)
    embeddings: list[str] = Field(default_factory=list, max_length=20)
    error: str | None = Field(default=None, max_length=300)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


class RateLimiter:
    def __init__(self, max_calls: int = 60, per_seconds: float = 10.0):
        self._max, self._per = max_calls, per_seconds
        self._calls: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            while calls and now - calls[0] > self._per:
                calls.popleft()
            if len(calls) >= self._max:
                return False
            calls.append(now)
            return True


def _networks(values: list[str]):
    return [ipaddress.ip_network(n, strict=False) for n in values or []]


def _in_networks(ip: str, networks) -> bool:
    if ip in LOCAL_HOSTS or not networks:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def create_server_app(cfg, db: ServerDB, tokens: auth.TokenStore | None = None,
                      limiter: auth.LoginLimiter | None = None,
                      frontend_dir: Path = FRONTEND_DIST,
                      now: Callable[[], datetime] = datetime.now) -> FastAPI:
    tokens = tokens or auth.TokenStore()
    limiter = limiter or auth.LoginLimiter()
    device_rate = RateLimiter()
    admin_nets = _networks(cfg.server.admin_allowed_networks)
    device_nets = _networks(cfg.server.device_allowed_networks)
    default_cutoff = cfg.attendance.clock_out_after
    fingerprint = cert_fingerprint(cfg.server.cert_file)

    app = FastAPI(title="PAS server", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def cutoff() -> str:
        return db.cutoff(default_cutoff)

    def device_status(d: dict[str, Any]) -> dict[str, Any]:
        current = now()
        seen = datetime.fromisoformat(d["last_seen"]) if d["last_seen"] else None
        alt = datetime.fromisoformat(d["alt_seen"]) if d["alt_seen"] else None
        return {
            "id": d["id"], "name": d["name"], "mac": d["mac"], "added_at": d["added_at"],
            "removed": d["removed_at"] is not None, "removed_at": d["removed_at"],
            "online": bool(seen and (current - seen).total_seconds() <= ONLINE_SECONDS
                           and d["removed_at"] is None),
            "last_seen": d["last_seen"], "last_ip": d["last_ip"], "last_ssid": d["last_ssid"],
            "last_bssid": d["last_bssid"], "pending": d["pending"],
            "app_version": d["app_version"],
            "mac_conflict": (f"This MAC was also used from {d['alt_ip']} at {d['alt_seen']}"
                             if alt and (current - alt) < timedelta(hours=1) else None),
            "backlit": backlit_warning(d, current),
            "spoof_today": (d.get("spoof_count") or 0
                            if d.get("spoof_date") == current.date().isoformat() else 0),
            "spoof_threshold": d.get("spoof_threshold"),
            "object_threshold": d.get("object_threshold"),
        }

    LABELS = {"phone": "Phone", "screen": "Screen", "laptop": "Laptop", "photo": "Photo"}

    def log_alerts(device: dict[str, Any], ip: str, alerts: list[AlertIn]) -> list[str]:
        new = db.add_alerts(device, [a.model_dump() for a in alerts], now())
        for _, a in new:
            what = LABELS.get(a["label"], "Foreign object")
            detail = f"{what} held up at {device['name']}"
            student = db.get_student(a["student_id"]) if a.get("student_id") else None
            if student:
                detail += f" (looked like {student.full_name}, {student.student_no})"
            if a.get("reason"):
                detail += f" · {a['reason']}"
            db.log_admin(ip, "spoof_attempt", detail)
        return [a.id for a in alerts]

    def backlit_warning(d: dict[str, Any], current: datetime) -> str | None:
        scans, backlit = d.get("light_scans") or 0, d.get("light_backlit") or 0
        if (d.get("light_date") != current.date().isoformat() or scans < BACKLIT_MIN_SCANS
                or backlit < BACKLIT_SHARE * scans):
            return None
        return (f"{backlit} of {scans} scans today had bright light behind the student. "
                "Move the kiosk away from windows or doors, or add a light in front.")

    # ---- access rules --------------------------------------------------------------

    def admin_network(request: Request) -> str:
        ip = client_ip(request)
        if not _in_networks(ip, admin_nets):
            raise HTTPException(403, "Not allowed from this network.")
        return ip

    def require_admin(ip: str = Depends(admin_network),
                      authorization: str | None = Header(default=None)) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not tokens.validate(token):
            raise HTTPException(401, "Please log in.")
        return ip

    def device_error(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(status, {"code": code, "message": message})

    def identify_device(request: Request, macs: list[str],
                        rate_limited: bool = True) -> tuple[dict[str, Any], str]:
        ip = client_ip(request)
        if not _in_networks(ip, device_nets):
            raise device_error(403, "network", "Kiosks must connect from the school network.")
        # Per computer (address + MACs), so kiosks behind one address don't block each other.
        if rate_limited and not device_rate.allow(f"{ip}|{','.join(sorted(macs))}"):
            raise device_error(429, "rate", "Too many requests.")
        device = db.find_device(macs)
        if device is None:
            readable = []
            for m in macs:
                try:
                    readable.append(normalize_mac(m))
                except ValueError:
                    pass
            raise device_error(403, "not_approved",
                               "This device is not approved. Ask the IT officer to add one of "
                               f"these MAC addresses: {', '.join(readable) or 'none found'}.")
        return device, ip

    # ---- device API ---------------------------------------------------------------------

    def validate_event(ev: EventIn, device: dict[str, Any], school: dict[str, Any],
                       current: datetime) -> str | None:
        try:
            ts = datetime.fromisoformat(ev.ts)
        except ValueError:
            return "bad timestamp"
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if ts > current + timedelta(seconds=MAX_CLOCK_SKEW_S):
            return "timestamp in the future"
        if device["removed_at"] and ts >= datetime.fromisoformat(device["removed_at"]):
            return "recorded after the device was removed"
        if db.get_student(ev.student_id) is None:
            return "unknown student"
        if not school["wifi_ssid"] or ev.ssid != school["wifi_ssid"]:
            return "not recorded on the school Wi-Fi"
        if school["wifi_bssids"]:
            try:
                bssid = normalize_mac(ev.bssid or "")
            except ValueError:
                bssid = None
            if bssid not in school["wifi_bssids"]:
                return "not recorded on a school access point"
        if school["lat"] is None or ev.lat is None or ev.lon is None:
            return "no location"
        if haversine_m(ev.lat, ev.lon, school["lat"], school["lon"]) > \
                school["radius_m"] + GEOFENCE_SLACK_M:
            return "recorded outside the school grounds"
        return None

    def kiosk_settings(school: dict[str, Any]) -> dict[str, Any]:
        return {"clock_out_after": cutoff(),
                "wifi": {"ssid": school["wifi_ssid"], "allowed_bssids": school["wifi_bssids"]},
                "geofence": {"lat": school["lat"], "lon": school["lon"],
                             "radius_m": school["radius_m"]}}

    @app.post("/api/device/sync")
    def device_sync(body: SyncBody, request: Request):
        device, ip = identify_device(request, body.macs)
        current = now()
        db.touch_device(device, ip, body.ssid, body.bssid, body.app_version, body.pending,
                        current, body.lighting.model_dump() if body.lighting else None)
        try:
            device_time = datetime.fromisoformat(body.device_time).replace(tzinfo=None)
        except ValueError:
            raise device_error(400, "bad_time", "Bad device time.") from None
        if abs((device_time - current).total_seconds()) > MAX_CLOCK_SKEW_S:
            raise device_error(409, "clock",
                               "This computer's clock is wrong. Set the correct date and time "
                               f"(the server says {current:%Y-%m-%d %H:%M}).")

        school = db.school_settings()
        good, rejected = [], []
        for ev in body.outbox:
            reason = validate_event(ev, device, school, current)
            if reason:
                rejected.append({"id": ev.id, "reason": reason})
                log.warning("Rejected event %s from %s: %s", ev.id, device["name"], reason)
            else:
                good.append(ev.model_dump())
        accepted = db.add_events(device, good, cutoff(), current.date()) if good else []

        approved = device["removed_at"] is None
        response: dict[str, Any] = {
            "approved": approved,
            "device_name": device["name"],
            "server_time": current.isoformat(timespec="seconds"),
            "accepted": accepted,
            "rejected": rejected,
        }
        if not approved:
            response["message"] = ("This device has been removed by the IT officer. "
                                   "Ask them to add it again.")
            return response

        if body.registration:
            job = db.get_job(body.registration.job_id)
            if job and job["device_id"] == device["id"] and job["status"] in ("waiting",
                                                                                 "capturing"):
                if body.registration.active:
                    db.update_job(job["id"], "capturing", body.registration.message,
                                  body.registration.progress)
        else:
            # The kiosk was capturing but has stopped without sending a result.
            job = db.job_for_device(device["id"])
            if job and job["status"] == "capturing":
                db.update_job(job["id"], "failed",
                              "The kiosk stopped the registration. Please try again.")

        response["alerts_accepted"] = log_alerts(device, ip, body.alerts) if body.alerts else []
        response["spoof_threshold"] = device.get("spoof_threshold")
        response["object_threshold"] = device.get("object_threshold")
        tuning = db.active_tuning_job(device["id"])
        if tuning:
            started = datetime.fromisoformat(tuning["updated_at"])
            if body.tuning and body.tuning.job_id == tuning["id"]:
                if tuning["status"] == "waiting":
                    db.update_tuning_job(tuning["id"], "running", "Collecting…")
                    tuning["status"] = "running"
            elif tuning["status"] == "running" and \
                    (current - started).total_seconds() > tuning["seconds"] + 30:
                db.update_tuning_job(tuning["id"], "failed", "The kiosk stopped the tuning step.")
                tuning = None
            if tuning:
                response["tuning_job"] = {"id": tuning["id"], "phase": tuning["phase"],
                                          "seconds": tuning["seconds"],
                                          "status": tuning["status"]}

        response["settings"] = kiosk_settings(school)
        if body.roster_version != db.roster_version():
            response["roster"] = db.roster()
        response["events"] = db.events_after(body.events_after, current.date())
        response["latest_seq"] = db.latest_seq()
        job = db.job_for_device(device["id"])
        if job:
            response["registration_job"] = {
                "id": job["id"], "status": job["status"], "student_no": job["student_no"],
                "full_name": job["full_name"], "student_id": job["student_id"]}
        return response

    @app.post("/api/device/registration/{job_id}/result")
    def registration_result(job_id: int, body: RegistrationResult, request: Request):
        device, _ = identify_device(request, body.macs, rate_limited=False)
        job = db.get_job(job_id)
        if not job or job["device_id"] != device["id"] or device["removed_at"]:
            raise HTTPException(404, "No such registration for this device.")
        if job["status"] not in ("waiting", "capturing"):
            return {"ok": False, "message": "This registration was cancelled."}
        if body.error or not body.embeddings:
            msg = body.error or "No face samples were received."
            db.update_job(job_id, "failed", msg)
            return {"ok": False, "message": msg}
        try:
            samples = [normalize(decode_embedding(e)) for e in body.embeddings]
        except ValueError:
            db.update_job(job_id, "failed", "Bad face data.")
            return {"ok": False, "message": "Bad face data."}
        if any(s.shape != samples[0].shape for s in samples):
            db.update_job(job_id, "failed", "Bad face data.")
            return {"ok": False, "message": "Bad face data."}

        ids, matrix = db.load_embeddings()
        mean = normalize(sum(samples) / len(samples))
        sid, score = best_match(mean, ids, matrix, exclude=job["student_id"])
        if sid is not None and score >= cfg.face.match_threshold:
            other = db.get_student(sid)
            msg = f"This face is already registered as {other.full_name} ({other.student_no})."
            db.update_job(job_id, "failed", msg)
            return {"ok": False, "message": msg}
        try:
            if job["student_id"] is not None:
                if db.get_student(job["student_id"]) is None:
                    raise DuplicateStudentError("The student was deleted.")
                db.replace_embeddings(job["student_id"], samples)
            else:
                db.create_student(job["student_no"], job["full_name"], samples)
        except DuplicateStudentError as exc:
            db.update_job(job_id, "failed", str(exc))
            return {"ok": False, "message": str(exc)}
        msg = f"{job['full_name']} registered."
        db.update_job(job_id, "done", msg, 1.0)
        return {"ok": True, "message": msg}

    @app.post("/api/device/tuning/{job_id}/result")
    def tuning_result(job_id: int, body: TuningResult, request: Request):
        device, _ = identify_device(request, body.macs, rate_limited=False)
        job = db.get_tuning_job(job_id)
        if not job or job["device_id"] != device["id"] or device["removed_at"]:
            raise HTTPException(404, "No such tuning step for this device.")
        if job["status"] not in ("waiting", "running"):
            return {"ok": False, "message": "This tuning step was cancelled."}
        if any(n < 0 for n in body.histogram):
            raise HTTPException(400, "Bad histogram.")
        samples = sum(body.histogram)
        if samples < 30:
            msg = "Too few face frames. Keep a face in view and run the step again."
            db.update_tuning_job(job_id, "failed", msg, body.histogram)
            return {"ok": False, "message": msg}
        db.update_tuning_job(job_id, "done", f"{samples} frames recorded.", body.histogram)
        return {"ok": True, "message": f"{samples} frames recorded."}

    # ---- admin: login -----------------------------------------------------------------

    @app.post("/api/admin/login")
    def login(body: LoginBody, ip: str = Depends(admin_network)):
        if limiter.locked(ip):
            raise HTTPException(429, "Too many wrong passwords. Try again in a minute.")
        if not auth.check_admin_password(db, body.password):
            limiter.failed(ip)
            db.log_admin(ip, "login_failed")
            raise HTTPException(401, "Wrong password.")
        limiter.succeeded(ip)
        db.log_admin(ip, "login")
        return {"token": tokens.issue()}

    @app.post("/api/admin/logout")
    def logout(authorization: str | None = Header(default=None)):
        tokens.revoke((authorization or "").removeprefix("Bearer ").strip())
        return {"ok": True}

    # ---- admin: devices ----------------------------------------------------------------

    @app.get("/api/admin/devices")
    def devices(_: str = Depends(require_admin)):
        return [device_status(d) for d in db.list_devices()]

    @app.post("/api/admin/devices")
    def add_device(body: DeviceBody, ip: str = Depends(require_admin)):
        try:
            device = db.add_device(body.name, body.mac)
        except DeviceError as exc:
            raise HTTPException(400, str(exc)) from None
        db.log_admin(ip, "add_device", f"{device['name']} {device['mac']}")
        return device_status(device)

    @app.delete("/api/admin/devices/{device_id}")
    def remove_device(device_id: int, ip: str = Depends(require_admin)):
        device = db.get_device(device_id)
        if device is None or device["removed_at"]:
            raise HTTPException(404, "Device not found.")
        db.remove_device(device_id, now())
        db.log_admin(ip, "remove_device", f"{device['name']} {device['mac']}")
        return {"ok": True}

    @app.post("/api/admin/devices/{device_id}/allow-access-point")
    def allow_access_point(device_id: int, ip: str = Depends(require_admin)):
        device = db.get_device(device_id)
        if device is None or not device["last_bssid"]:
            raise HTTPException(400, "This device hasn't reported an access point yet.")
        school = db.school_settings()
        school["wifi_bssids"] = sorted(set(school["wifi_bssids"]) | {device["last_bssid"]})
        db.set_school_settings(school)
        db.log_admin(ip, "allow_access_point", device["last_bssid"])
        return {"wifi_bssids": school["wifi_bssids"]}

    # ---- admin: kiosk tuning (anti-spoof threshold) -----------------------------------------

    def suggest(choose, first, second) -> dict[str, Any] | None:
        if not (first and second):
            return None
        try:
            return choose(first["histogram"], second["histogram"])
        except ValueError as exc:
            return {"error": str(exc)}

    def tuning_summary(device: dict[str, Any]) -> dict[str, Any]:
        done = {phase: db.latest_tuning(device["id"], phase)
                for phase in ("real", "spoof", "phone", "clear")}
        active = db.active_tuning_job(device["id"])
        steps = {phase: ({"done_at": job["updated_at"], "samples": sum(job["histogram"])}
                         if job else None) for phase, job in done.items()}
        return {
            "threshold": device.get("spoof_threshold"),
            "default_threshold": cfg.antispoof.threshold,
            "object_threshold": device.get("object_threshold"),
            "default_object_threshold": cfg.antispoof.object_score,
            "object_suggestion": suggest(choose_object_threshold, done["phone"], done["clear"]),
            "steps": steps,
            "active": ({"id": active["id"], "phase": active["phase"], "status": active["status"],
                        "message": active["message"], "seconds": active["seconds"]}
                       if active else None),
            "suggestion": suggest(choose_threshold, done["real"], done["spoof"]),
        }

    def tunable_device(device_id: int) -> dict[str, Any]:
        device = db.get_device(device_id)
        if device is None or device["removed_at"]:
            raise HTTPException(404, "Device not found.")
        return device

    @app.get("/api/admin/devices/{device_id}/tuning")
    def tuning_status(device_id: int, _: str = Depends(require_admin)):
        return tuning_summary(tunable_device(device_id))

    @app.post("/api/admin/devices/{device_id}/tuning")
    def start_tuning(device_id: int, body: TuningStart, ip: str = Depends(require_admin)):
        device = tunable_device(device_id)
        try:
            db.create_tuning_job(device_id, body.phase, TUNING_SECONDS)
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from None
        db.log_admin(ip, "kiosk_tuning", f"{device['name']}: {body.phase} step")
        return tuning_summary(device)

    @app.post("/api/admin/devices/{device_id}/spoof-threshold")
    def set_spoof_threshold(device_id: int, body: ThresholdBody, ip: str = Depends(require_admin)):
        device = tunable_device(device_id)
        value = None if body.threshold is None else round(body.threshold, 2)
        db.set_spoof_threshold(device_id, value)
        db.log_admin(ip, "spoof_threshold",
                     f"{device['name']}: {'default' if value is None else value}")
        return tuning_summary(db.get_device(device_id))

    @app.post("/api/admin/devices/{device_id}/object-threshold")
    def set_object_threshold(device_id: int, body: ObjectThresholdBody,
                             ip: str = Depends(require_admin)):
        device = tunable_device(device_id)
        value = None if body.threshold is None else round(body.threshold, 2)
        db.set_object_threshold(device_id, value)
        db.log_admin(ip, "object_threshold",
                     f"{device['name']}: {'default' if value is None else value}")
        return tuning_summary(db.get_device(device_id))

    # ---- admin: students + registration --------------------------------------------------

    @app.get("/api/admin/students")
    def students(_: str = Depends(require_admin)):
        return [s.to_dict() for s in db.list_students()]

    @app.delete("/api/admin/students/{student_id}")
    def delete_student(student_id: int, ip: str = Depends(require_admin)):
        student = db.get_student(student_id)
        if student is None:
            raise HTTPException(404, "Student not found.")
        db.delete_student(student_id)
        db.log_admin(ip, "delete_student", f"{student.student_no} {student.full_name}")
        return {"ok": True}

    def _online_device(device_id: int) -> dict[str, Any]:
        device = db.get_device(device_id)
        if device is None or device["removed_at"]:
            raise HTTPException(404, "Device not found.")
        if not device_status(device)["online"]:
            raise HTTPException(409, f"{device['name']} is offline. Switch it on and try again.")
        return device

    @app.post("/api/admin/register")
    def register(body: RegisterBody, ip: str = Depends(require_admin)):
        student_no, full_name = body.student_no.strip(), body.full_name.strip()
        if not student_no or not full_name:
            raise HTTPException(422, "Name and student ID are required.")
        if db.student_no_exists(student_no):
            raise HTTPException(409, f"Student ID {student_no} is already registered.")
        device = _online_device(body.device_id)
        try:
            job = db.create_job(device["id"], student_no, full_name)
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from None
        db.log_admin(ip, "register_student", f"{student_no} {full_name} on {device['name']}")
        return job

    @app.post("/api/admin/students/{student_id}/recapture")
    def recapture(student_id: int, body: RecaptureBody, ip: str = Depends(require_admin)):
        student = db.get_student(student_id)
        if student is None:
            raise HTTPException(404, "Student not found.")
        device = _online_device(body.device_id)
        try:
            job = db.create_job(device["id"], student.student_no, student.full_name, student.id)
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from None
        db.log_admin(ip, "recapture_face", f"{student.student_no} on {device['name']}")
        return job

    @app.get("/api/admin/register/{job_id}")
    def job_status(job_id: int, _: str = Depends(require_admin)):
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "Not found.")
        if job["status"] in ("waiting", "capturing"):
            device = db.get_device(job["device_id"])
            if device and not device_status(device)["online"]:
                db.update_job(job_id, "failed", f"{device['name']} went offline.")
                job = db.get_job(job_id)
        return job

    @app.post("/api/admin/register/{job_id}/cancel")
    def cancel_job(job_id: int, _: str = Depends(require_admin)):
        job = db.get_job(job_id)
        if job and job["status"] in ("waiting", "capturing"):
            db.update_job(job_id, "cancelled", "Registration cancelled.")
        return {"ok": True}

    # ---- admin: attendance + settings ------------------------------------------------------

    @app.get("/api/admin/sessions")
    def sessions(day: date | None = Query(default=None, alias="date"),
                 _: str = Depends(require_admin)):
        day = day or now().date()
        rows = db.sessions_on(day)
        open_ids = {r["student_id"] for r in rows if not r["closed"]}
        all_ids = {r["student_id"] for r in rows}
        return {"date": day.isoformat(), "sessions": rows,
                "counts": {"in": len(open_ids), "out": len(all_ids - open_ids),
                           "students": len(all_ids)}}

    @app.get("/api/admin/settings")
    def get_settings(_: str = Depends(require_admin)):
        return {**db.school_settings(), "clock_out_after": cutoff(),
                "certificate_fingerprint": fingerprint,
                "students": len(db.list_students())}

    @app.put("/api/admin/settings")
    def put_settings(body: SettingsBody, ip: str = Depends(require_admin)):
        try:
            value = parse_hhmm(body.clock_out_after).strftime("%H:%M")
            school = db.set_school_settings(body.model_dump())
        except (ConfigError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        db.set_setting("clock_out_after", value)
        db.log_admin(ip, "update_settings",
                     f"clock-out {value}, Wi-Fi '{school['wifi_ssid']}', "
                     f"{len(school['wifi_bssids'])} access point(s), radius {school['radius_m']:.0f} m")
        return {**school, "clock_out_after": value}

    @app.post("/api/admin/password")
    def change_password(body: PasswordBody, ip: str = Depends(require_admin)):
        if not auth.check_admin_password(db, body.current):
            raise HTTPException(400, "The current password is wrong.")
        try:
            auth.set_admin_password(db, body.new)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        tokens.revoke_all()
        db.log_admin(ip, "change_password")
        return {"token": tokens.issue()}

    @app.get("/api/admin/log")
    def admin_log(_: str = Depends(require_admin)):
        return db.admin_log(200)

    # ---- the Vue app -----------------------------------------------------------------------

    mount_frontend(app, frontend_dir)
    return app


def mount_frontend(app: FastAPI, frontend_dir: Path) -> None:
    index = frontend_dir / "index.html"
    if (frontend_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        if not index.exists():
            return HTMLResponse("<h1>PAS</h1><p>The screens are not built yet. Run "
                                "<code>npm install</code> and <code>npm run build</code> in "
                                "the <code>frontend</code> folder.</p>", status_code=503)
        candidate = (frontend_dir / path).resolve()
        if path and candidate.is_file() and frontend_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
