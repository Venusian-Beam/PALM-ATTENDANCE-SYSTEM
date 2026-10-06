"""Geofence check: the kiosk must be physically at the school."""
from __future__ import annotations

import asyncio
import math
import sys
from dataclasses import dataclass
from typing import Callable

from backend.security.result import CheckResult

EARTH_RADIUS_M = 6_371_008.8

_ENABLE_HINT = ("Turn on Location services and 'Let desktop apps access your location' in "
                "Windows Settings > Privacy & security > Location.")


class LocationError(Exception):
    pass


@dataclass
class Fix:
    lat: float
    lon: float
    accuracy_m: float
    source: str = ""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


async def _windows_fix() -> Fix:
    from winrt.windows.devices.geolocation import (
        GeolocationAccessStatus, Geolocator, PositionAccuracy)

    access = await Geolocator.request_access_async()
    if access != GeolocationAccessStatus.ALLOWED:
        raise LocationError("Location access is denied. " + _ENABLE_HINT)
    locator = Geolocator()
    locator.desired_accuracy = PositionAccuracy.HIGH
    pos = await locator.get_geoposition_async()
    coord = pos.coordinate
    point = coord.point.position
    source = getattr(coord.position_source, "name", str(coord.position_source))
    return Fix(point.latitude, point.longitude, float(coord.accuracy), source)


def get_fix(timeout_s: float = 20.0) -> Fix:
    """Ask Windows for the current position (uses nearby Wi-Fi networks)."""
    if sys.platform != "win32":
        raise LocationError("Location lookup is only supported on Windows.")
    try:
        return asyncio.run(asyncio.wait_for(_windows_fix(), timeout_s))
    except asyncio.TimeoutError:
        raise LocationError("Timed out waiting for a location. " + _ENABLE_HINT) from None
    except LocationError:
        raise
    except Exception as exc:
        raise LocationError(f"Location unavailable ({exc}). " + _ENABLE_HINT) from exc


def evaluate_geofence(fix: Fix, lat: float, lon: float, radius_m: float,
                      accuracy_buffer_m: float, max_accuracy_m: float) -> CheckResult:
    data = {"lat": fix.lat, "lon": fix.lon, "accuracy_m": fix.accuracy_m}
    if fix.accuracy_m > max_accuracy_m:
        return CheckResult(
            "location", False,
            f"Location is too imprecise (±{fix.accuracy_m:.0f} m, limit {max_accuracy_m:.0f} m).",
            data)
    distance = haversine_m(fix.lat, fix.lon, lat, lon)
    data["distance_m"] = distance
    allowed = radius_m + min(fix.accuracy_m, accuracy_buffer_m)
    if distance > allowed:
        return CheckResult(
            "location", False,
            f"Outside school grounds ({distance:.0f} m from school, allowed {allowed:.0f} m).",
            data)
    return CheckResult("location", True, f"On school grounds ({distance:.0f} m from centre).", data)


def check_location(geofence, provider: Callable[[], Fix]) -> CheckResult:
    if geofence.lat is None or geofence.lon is None:
        return CheckResult("location", False, "The school location is not configured.")
    try:
        fix = provider()
    except LocationError as exc:
        return CheckResult("location", False, str(exc))
    return evaluate_geofence(fix, geofence.lat, geofence.lon, geofence.radius_m,
                             geofence.accuracy_buffer_m, geofence.max_accuracy_m)
