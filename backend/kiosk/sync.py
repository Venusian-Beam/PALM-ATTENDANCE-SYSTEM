"""Keeps a kiosk in step with the school server.

Every few seconds: upload unsent events, download the roster / today's events /
school settings, and pick up registration jobs. Works through outages: events wait
in the local outbox, and the kiosk keeps running on its last known approval for up
to `kiosk.max_offline_hours`.
"""
from __future__ import annotations

import base64
import logging
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np

from backend.controller import RegisterRequest, RegistrationBusyError
from backend.kiosk.store import KioskStore
from backend.security.device import local_macs
from backend.security.network import WifiInfo, current_wifi
from backend.security.result import CheckResult

log = logging.getLogger(__name__)

APP_VERSION = "2.0"


def pinned_ssl_context(cert_file: Path) -> ssl.SSLContext:
    """Trust only the school server's own certificate (saved at setup)."""
    ctx = ssl.create_default_context(cafile=str(cert_file))
    ctx.check_hostname = False  # the server may be reached by IP; the certificate is pinned
    return ctx


def make_http_client(server_url: str, cert_file: Path, timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(base_url=server_url, verify=pinned_ssl_context(cert_file),
                        timeout=timeout)


def apply_settings(cfg, settings: dict[str, Any] | None) -> None:
    """Copy the school-wide settings from the server into this kiosk's config."""
    if not settings:
        return
    wifi = settings.get("wifi", {})
    cfg.wifi.ssid = wifi.get("ssid", "")
    cfg.wifi.allowed_bssids = list(wifi.get("allowed_bssids", []))
    geo = settings.get("geofence", {})
    cfg.geofence.lat = geo.get("lat")
    cfg.geofence.lon = geo.get("lon")
    cfg.geofence.radius_m = float(geo.get("radius_m") or cfg.geofence.radius_m)


class SyncClient:
    def __init__(self, cfg, store: KioskStore, http: httpx.Client,
                 controller: Callable[[], Any],
                 macs_provider: Callable[[], dict[str, str]] = local_macs,
                 wifi_provider: Callable[[], list[WifiInfo]] = current_wifi,
                 on_change: Callable[[], None] = lambda: None,
                 now: Callable[[], datetime] = datetime.now):
        self._cfg = cfg
        self._store = store
        self._http = http
        self._controller = controller
        self._macs_provider = macs_provider
        self._wifi_provider = wifi_provider
        self._on_change = on_change
        self._now = now
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sync", daemon=True)
        self._finished_jobs: set[int] = set()
        self._finished_tuning: set[int] = set()
        self._pending_job = False
        self.online = False
        self.last_error = ""
        self.clock_error: str | None = None
        self._macs: list[str] = []
        apply_settings(cfg, store.settings())

    # ---- approval, used by the security guard ------------------------------------------

    def macs(self) -> list[str]:
        if not self._macs:
            try:
                self._macs = list(self._macs_provider())
            except Exception:
                self._macs = []
        return self._macs

    def approval_check(self) -> CheckResult:
        name = self._store.get("device_name")
        if self.clock_error:
            return CheckResult("device", False, self.clock_error)
        if self._store.get("approved") != "1":
            message = self._store.get("approval_message") or (
                "This device has not been approved yet. Ask the IT officer to add one of "
                f"these MAC addresses in the admin portal: {', '.join(self.macs())}.")
            if not self.online and not self._store.get("approval_message"):
                message = ("Cannot reach the attendance server to check this device. "
                           f"{self.last_error}".strip())
            return CheckResult("device", False, message)
        last_ok = self._store.get("last_sync_ok")
        hours = ((self._now() - datetime.fromisoformat(last_ok)).total_seconds() / 3600
                 if last_ok else float("inf"))
        if hours > self._cfg.kiosk.max_offline_hours:
            return CheckResult(
                "device", False,
                f"This kiosk has not reached the attendance server for {hours:.0f} hours. "
                "Check the network and that the server PC is switched on.")
        detail = f"Approved as '{name}'."
        if not self.online:
            detail += f" Offline: {self._store.pending_count()} record(s) waiting to upload."
        return CheckResult("device", True, detail, {"device_name": name})

    # ---- registration (called from the controller) --------------------------------------

    def submit_registration(self, job_id: int, samples: list[np.ndarray] | None,
                            error: str | None) -> tuple[bool, str]:
        self._finished_jobs.add(job_id)
        body = {"macs": self.macs(), "error": error,
                "embeddings": [base64.b64encode(np.asarray(s, dtype=np.float32).tobytes()).decode()
                               for s in samples or []]}
        response = self._http.post(f"/api/device/registration/{job_id}/result", json=body)
        response.raise_for_status()
        data = response.json()
        self._wake.set()  # fetch the new roster straight away
        return bool(data.get("ok")), str(data.get("message", ""))

    def submit_tuning(self, job_id: int, histogram: list[int]) -> tuple[bool, str]:
        """Kiosk tuning: send the live-face score histogram for one step."""
        self._finished_tuning.add(job_id)
        response = self._http.post(f"/api/device/tuning/{job_id}/result",
                                   json={"macs": self.macs(), "histogram": histogram})
        response.raise_for_status()
        data = response.json()
        return bool(data.get("ok")), str(data.get("message", ""))

    # ---- sync ---------------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        last_prune = None
        while not self._stop.is_set():
            self.sync_once()
            today = self._now().date()
            if last_prune != today:
                self._store.prune(today)
                last_prune = today
            busy = self._pending_job or self._controller().registration_status() is not None
            self._wake.wait(1.0 if busy else self._cfg.kiosk.sync_interval_s)
            self._wake.clear()

    def _current_wifi(self) -> tuple[str | None, str | None]:
        try:
            conns = self._wifi_provider()
        except Exception:
            return None, None
        return (conns[0].ssid, conns[0].bssid) if conns else (None, None)

    def _lighting_report(self) -> dict[str, Any] | None:
        day = self._store.get("light_date")
        if not day:
            return None
        return {"date": day, "scans": self._store.get_int("light_scans"),
                "backlit": self._store.get_int("light_backlit")}

    def sync_once(self) -> None:
        before = (self.online, self.clock_error, self._store.get("approved"))
        ssid, bssid = self._current_wifi()
        outbox = self._store.outbox()
        body = {
            "macs": self.macs(),
            "device_time": self._now().isoformat(timespec="seconds"),
            "app_version": APP_VERSION,
            "ssid": ssid, "bssid": bssid,
            "outbox": outbox,
            "roster_version": self._store.roster_version,
            "events_after": self._store.get_int("events_after", 0),
            "pending": self._store.pending_count(),
            "registration": self._controller().registration_status(),
            "lighting": self._lighting_report(),
            "alerts": self._store.alerts(),
            "tuning": self._controller().tuning_status(),
        }
        try:
            response = self._http.post("/api/device/sync", json=body)
        except httpx.HTTPError as exc:
            self.online = False
            self.last_error = f"({type(exc).__name__})"
            log.debug("Sync failed: %s", exc)
        else:
            self._handle(response)
        if before != (self.online, self.clock_error, self._store.get("approved")):
            self._on_change()

    def _handle(self, response: httpx.Response) -> None:
        self.online = True
        self.last_error = ""
        try:
            data = response.json()
        except ValueError:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        if response.status_code == 409 and isinstance(detail, dict) and detail.get("code") == "clock":
            self.clock_error = detail.get("message")
            return
        self.clock_error = None
        if response.status_code == 403 and isinstance(detail, dict):
            self._store.set("approved", "0")
            self._store.set("approval_message", detail.get("message", "Not approved."))
            return
        if response.status_code != 200:
            self.last_error = f"(server error {response.status_code})"
            return

        rejected = [r["id"] for r in data.get("rejected", [])]
        for r in data.get("rejected", []):
            log.warning("Server rejected event %s: %s", r["id"], r["reason"])
        self._store.mark_uploaded(data.get("accepted", []), rejected)
        self._store.set("device_name", data.get("device_name", ""))
        self._store.set("last_sync_ok", self._now().isoformat(timespec="seconds"))
        if not data.get("approved"):
            self._store.set("approved", "0")
            self._store.set("approval_message", data.get("message", "Not approved."))
            return
        self._store.set("approved", "1")
        self._store.set("approval_message", "")

        if data.get("settings") and data["settings"] != self._store.settings():
            self._store.set_settings(data["settings"])
            apply_settings(self._cfg, data["settings"])
            self._on_change()
        if data.get("roster"):
            self._store.replace_roster(data["roster"])
        events = data.get("events", [])
        if events:
            self._store.merge_server_events(events)
            self._store.set("events_after", max(e["seq"] for e in events))
        self._store.alerts_uploaded(data.get("alerts_accepted", []))
        for key in ("spoof_threshold", "object_threshold"):
            value = data.get(key)
            self._store.set(key, "" if value is None else str(value))
        self._handle_job(data.get("registration_job"))
        self._handle_tuning(data.get("tuning_job"))

    def _handle_tuning(self, job: dict[str, Any] | None) -> None:
        if not job or job.get("status") != "waiting" or job["id"] in self._finished_tuning:
            return
        try:
            self._controller().start_tuning(job["id"], job["phase"], float(job["seconds"]))
        except RegistrationBusyError:
            pass

    def _handle_job(self, job: dict[str, Any] | None) -> None:
        controller = self._controller()
        self._pending_job = bool(job and job["status"] in ("waiting", "capturing"))
        if not job:
            return
        if job["status"] == "cancelled":
            controller.cancel_registration(job["id"])
            return
        if job["id"] in self._finished_jobs or controller.registration_status() is not None:
            return
        try:
            controller.start_registration(RegisterRequest(
                job["id"], job["student_no"], job["full_name"], job.get("student_id")))
        except RegistrationBusyError:
            pass
