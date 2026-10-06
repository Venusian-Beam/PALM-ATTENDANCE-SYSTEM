"""Runs a kiosk: camera, background security checks, and syncing with the server."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, time as dtime
from typing import Any, Callable

import httpx
import numpy as np

from backend.camera import CameraService, open_webcam
from backend.config import ConfigError, parse_hhmm
from backend.controller import Controller, ViewState
from backend.face.lighting import Lighting
from backend.face.quality import QualityMeter
from backend.kiosk.store import KioskStore
from backend.kiosk.sync import SyncClient
from backend.security.device import local_macs
from backend.security.guard import GuardResult, SecurityGuard
from backend.security.network import current_wifi

log = logging.getLogger(__name__)


class KioskRuntime:
    def __init__(self, cfg, store: KioskStore, engine, extractor, http: httpx.Client,
                 capture_factory=open_webcam,
                 macs_provider: Callable[[], dict[str, str]] = local_macs,
                 wifi_provider=current_wifi, location_provider=None,
                 now: Callable[[], datetime] = datetime.now,
                 spoof=None):
        self.cfg = cfg
        self.store = store
        self.http = http
        self._extractor = extractor
        self._now = now
        self.sync = SyncClient(cfg, store, http, controller=lambda: self.controller,
                               macs_provider=macs_provider, wifi_provider=wifi_provider,
                               on_change=self.request_recheck, now=now)
        self.guard = SecurityGuard(cfg, device_check=self.sync.approval_check,
                                   wifi_provider=wifi_provider,
                                   location_provider=location_provider,
                                   settings_ready=lambda: store.settings() is not None)
        self.controller = Controller(cfg, store, engine, extractor,
                                     guard_check=self._quick_check, cutoff=self.cutoff,
                                     submit_registration=self.sync.submit_registration,
                                     device_name=lambda: store.get("device_name"), now=now,
                                     quality=QualityMeter(cfg.stability),
                                     lighting=Lighting(cfg.lighting),
                                     spoof=spoof,
                                     report_alert=store.add_alert,
                                     submit_tuning=self.sync.submit_tuning)
        self.camera = CameraService(cfg.camera_index, self._process_frame, capture_factory)
        self._guard_result: GuardResult | None = None
        self._stop = threading.Event()
        self._recheck = threading.Event()
        self._guard_thread = threading.Thread(target=self._guard_loop, name="guard", daemon=True)

    def cutoff(self) -> dtime:
        settings = self.store.settings() or {}
        try:
            return parse_hhmm(settings.get("clock_out_after") or
                              self.cfg.attendance.clock_out_after)
        except ConfigError:
            return parse_hhmm(self.cfg.attendance.clock_out_after)

    # ---- security state -------------------------------------------------------------

    @property
    def guard_result(self) -> GuardResult | None:
        return self._guard_result

    @property
    def unlocked(self) -> bool:
        result = self._guard_result
        return result is not None and result.ok

    def _set_guard_result(self, result: GuardResult | None) -> None:
        was_unlocked = self.unlocked
        self._guard_result = result
        if result is not None and not result.ok:
            log.warning("Security check failed: %s", "; ".join(result.reasons))
            if was_unlocked:
                self.controller.cancel_registration()
                self.controller.reset()

    def _quick_check(self) -> GuardResult:
        """Called by the controller right before it records a clock-in or clock-out."""
        result = self.guard.run_quick()
        if not result.ok:
            self._set_guard_result(result)
        return result

    def request_recheck(self) -> None:
        if self._guard_result is not None and not self._guard_result.ok:
            self._guard_result = None  # show "checking…" until the new result arrives
        self._recheck.set()

    # ---- threads ----------------------------------------------------------------------

    def start(self) -> None:
        self._guard_thread.start()
        self.sync.start()
        self.camera.start()

    def stop(self) -> None:
        self._stop.set()
        self._recheck.set()
        self.camera.stop()
        self.sync.stop()
        stop_objects = getattr(getattr(self.controller, "_spoof", None), "stop", None)
        if stop_objects is not None:
            stop_objects()
        self._guard_thread.join(timeout=3)
        try:
            self._extractor.close()
        except Exception:
            log.exception("Error closing the face landmarker")

    def _guard_loop(self) -> None:
        interval = max(30.0, self.cfg.security.recheck_minutes * 60)
        while not self._stop.is_set():
            result = self.guard.run_full()
            if not self._stop.is_set():
                self._set_guard_result(result)
            self._recheck.wait(timeout=interval)
            self._recheck.clear()

    def _process_frame(self, frame: np.ndarray) -> ViewState | None:
        if not self.unlocked:
            return None
        return self.controller.process(frame)

    # ---- state for the kiosk screen ---------------------------------------------------

    def screen_state(self) -> dict[str, Any]:
        _, _, view = self.camera.latest()
        result = self._guard_result
        return {
            "locked": not self.unlocked,
            "checking": result is None,
            "checks": result.to_dict() if result else [],
            "camera_ok": self.camera.camera_ok,
            "view": view.to_dict() if view else None,
            "device": {"name": self.store.get("device_name") or None,
                       "online": self.sync.online,
                       "pending": self.store.pending_count(),
                       "macs": self.sync.macs()},
        }
