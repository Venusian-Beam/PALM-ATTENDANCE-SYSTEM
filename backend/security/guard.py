"""Runs every security check together. Any failure or error blocks attendance."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.security.location import Fix, check_location, get_fix
from backend.security.network import WifiInfo, check_wifi, current_wifi
from backend.security.result import CheckResult


@dataclass
class GuardResult:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [c.detail for c in self.checks if not c.ok]

    @property
    def context(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for c in self.checks:
            merged.update(c.data)
        return merged

    def to_dict(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks]


def _safe(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    try:
        return fn()
    except Exception as exc:  # fail closed
        return CheckResult(name, False, f"The {name} check failed: {exc}")


class SecurityGuard:
    """`device_check` decides whether this computer is an approved kiosk (on kiosks it
    reports the server's approval). Wi-Fi and location are compared with `cfg.wifi` and
    `cfg.geofence`, which kiosks receive from the server."""

    def __init__(self, cfg, device_check: Callable[[], CheckResult],
                 wifi_provider: Callable[[], list[WifiInfo]] = current_wifi,
                 location_provider: Callable[[], Fix] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 settings_ready: Callable[[], bool] = lambda: True):
        self._cfg = cfg
        self._device_check = device_check
        self._settings_ready = settings_ready
        self._wifi_provider = wifi_provider
        self._location_provider = location_provider or (
            lambda: get_fix(cfg.security.location_timeout_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._location_cache: tuple[float, CheckResult] | None = None

    def _device(self) -> CheckResult:
        return _safe("device", self._device_check)

    def _waiting(self, name: str) -> CheckResult | None:
        if self._settings_ready():
            return None
        return CheckResult(name, False, "Waiting for the school's settings from the server "
                                        "(the device must be approved first).")

    def _wifi(self) -> CheckResult:
        if waiting := self._waiting("wifi"):
            return waiting
        return _safe("wifi", lambda: check_wifi(self._cfg.wifi.ssid,
                                                self._cfg.wifi.allowed_bssids,
                                                self._wifi_provider))

    def _location(self, fresh: bool) -> CheckResult:
        if waiting := self._waiting("location"):
            return waiting
        max_age = self._cfg.security.recheck_minutes * 60
        cached = self._location_cache
        if not fresh and cached and cached[1].ok and self._clock() - cached[0] <= max_age:
            return cached[1]
        result = _safe("location", lambda: check_location(self._cfg.geofence,
                                                          self._location_provider))
        self._location_cache = (self._clock(), result)
        return result

    def run_full(self) -> GuardResult:
        """All checks, with a fresh location fix (slow: can take several seconds)."""
        with self._lock:
            return GuardResult([self._device(), self._wifi(), self._location(fresh=True)])

    def run_quick(self) -> GuardResult:
        """Device and Wi-Fi checked live; a recent passing location fix is reused."""
        with self._lock:
            return GuardResult([self._device(), self._wifi(), self._location(fresh=False)])
