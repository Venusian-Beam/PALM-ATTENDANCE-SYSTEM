import pytest

from backend.config import Config, GeofenceConfig
from backend.security.device import check_device, normalize_mac
from backend.security.guard import SecurityGuard
from backend.security.result import CheckResult
from backend.security.location import (Fix, LocationError, check_location, evaluate_geofence,
                                       haversine_m)
from backend.security.network import WifiInfo, check_wifi, parse_netsh

NETSH_NEW = """
There is 1 interface on the system:

    Name                   : WiFi 2
    Description            : Intel(R) Dual Band Wireless-AC 8265
    Physical address       : 00:1a:2b:3c:4d:5e
    State                  : connected
    SSID                   : SCHOOL-NET
    AP BSSID               : 42:37:18:02:2a:cc
    Profile                : SCHOOL-NET
"""

NETSH_OLD = """
There are 2 interfaces on the system:

    Name                   : Wi-Fi
    State                  : disconnected

    Name                   : Wi-Fi 2
    State                  : connected
    SSID                   : Staff WiFi
    BSSID                  : AA-BB-CC-DD-EE-FF
"""


@pytest.mark.parametrize("raw", ["00-1A-2B-3C-4D-5E", "00:1a:2b:3c:4d:5e", "001a.2b3c.4d5e"])
def test_normalize_mac(raw):
    assert normalize_mac(raw) == "00:1a:2b:3c:4d:5e"


def test_normalize_mac_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_mac("not-a-mac")


def test_device_check():
    macs = {"00:1a:2b:3c:4d:5e": "WiFi"}
    assert check_device(["00-1A-2B-3C-4D-5E"], lambda: macs).ok
    assert not check_device(["00:11:22:33:44:55"], lambda: macs).ok
    assert not check_device([], lambda: macs).ok


def test_parse_netsh_new_format():
    [info] = parse_netsh(NETSH_NEW)
    assert (info.ssid, info.bssid) == ("SCHOOL-NET", "42:37:18:02:2a:cc")


def test_parse_netsh_old_format_skips_disconnected():
    [info] = parse_netsh(NETSH_OLD)
    assert (info.ssid, info.bssid, info.interface) == ("Staff WiFi", "aa:bb:cc:dd:ee:ff", "Wi-Fi 2")


def test_wifi_check():
    on_school = lambda: [WifiInfo("SCHOOL-NET", "42:37:18:02:2a:cc")]
    assert check_wifi("SCHOOL-NET", [], on_school).ok
    assert check_wifi("SCHOOL-NET", ["42:37:18:02:2a:cc"], on_school).ok
    assert not check_wifi("SCHOOL-NET", ["00:00:00:00:00:01"], on_school).ok  # fake hotspot
    assert not check_wifi("OTHER", [], on_school).ok
    assert not check_wifi("SCHOOL-NET", [], lambda: []).ok
    assert not check_wifi("", [], on_school).ok


def _north(lat, metres):
    return lat + metres / 111_195


def test_haversine():
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-3)


def test_geofence():
    assert evaluate_geofence(Fix(_north(6.5, 150), 3.4, 30), 6.5, 3.4, 200, 100, 500).ok
    assert not evaluate_geofence(Fix(_north(6.5, 400), 3.4, 30), 6.5, 3.4, 200, 100, 500).ok
    # accuracy slack is capped at 100 m
    assert evaluate_geofence(Fix(_north(6.5, 290), 3.4, 400), 6.5, 3.4, 200, 100, 500).ok
    assert not evaluate_geofence(Fix(_north(6.5, 350), 3.4, 400), 6.5, 3.4, 200, 100, 500).ok
    # too imprecise
    assert not evaluate_geofence(Fix(6.5, 3.4, 3000), 6.5, 3.4, 200, 100, 500).ok


def test_location_unconfigured_or_unavailable_fails():
    assert not check_location(GeofenceConfig(), lambda: Fix(0, 0, 10)).ok

    def broken():
        raise LocationError("off")
    assert not check_location(GeofenceConfig(lat=1, lon=1), broken).ok


def _guard(location_calls, **overrides):
    cfg = Config()
    cfg.wifi.ssid = "SCHOOL-NET"
    cfg.geofence = GeofenceConfig(lat=6.5, lon=3.4)

    def location():
        location_calls.append(1)
        return Fix(6.5, 3.4, 20)
    providers = dict(device_check=lambda: CheckResult("device", True, "ok",
                                                      {"device_name": "Gate A"}),
                     wifi_provider=lambda: [WifiInfo("SCHOOL-NET", None)],
                     location_provider=location)
    providers.update(overrides)
    return SecurityGuard(cfg, **providers)


def test_guard_passes_with_context():
    result = _guard([]).run_full()
    assert result.ok
    assert result.context["device_name"] == "Gate A"
    assert result.context["ssid"] == "SCHOOL-NET"


def test_guard_device_check_failure_blocks():
    result = _guard([], device_check=lambda: CheckResult("device", False, "not approved")).run_full()
    assert not result.ok and result.reasons == ["not approved"]


def test_guard_fails_closed_on_exception():
    def explode():
        raise RuntimeError("netsh missing")
    result = _guard([], wifi_provider=explode).run_full()
    assert not result.ok and "wifi" in result.reasons[0]


def test_guard_quick_reuses_recent_location():
    calls = []
    guard = _guard(calls)
    guard.run_full()
    guard.run_quick()
    guard.run_quick()
    assert len(calls) == 1


def test_guard_waits_for_settings():
    result = _guard([], settings_ready=lambda: False).run_full()
    assert not result.ok
    assert sum("Waiting for the school's settings" in r for r in result.reasons) == 2
