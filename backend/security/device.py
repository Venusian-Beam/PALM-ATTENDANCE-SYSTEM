"""Device lock: only run on a computer whose MAC address is approved."""
from __future__ import annotations

import re
from typing import Callable, Iterable

import psutil

from backend.security.result import CheckResult

_HEX = re.compile(r"^[0-9a-f]{12}$")
_NULL_MAC = "00:00:00:00:00:00"


def normalize_mac(value: str) -> str:
    """Return a MAC as lowercase colon-separated octets, e.g. '00:1a:2b:3c:4d:5e'."""
    raw = re.sub(r"[\s:.\-]", "", str(value)).lower()
    if not _HEX.match(raw):
        raise ValueError(f"Not a valid MAC address: {value!r}")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def local_macs() -> dict[str, str]:
    """Map each network adapter's MAC address to the adapter name."""
    found: dict[str, str] = {}
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != psutil.AF_LINK:
                continue
            try:
                mac = normalize_mac(addr.address)
            except ValueError:
                continue
            if mac != _NULL_MAC:
                found.setdefault(mac, iface)
    return found


def check_device(allowed: Iterable[str],
                 provider: Callable[[], dict[str, str]] = local_macs) -> CheckResult:
    allowed_set = {normalize_mac(m) for m in allowed}
    if not allowed_set:
        return CheckResult("device", False, "No approved device is configured.")
    for mac in provider():
        if mac in allowed_set:
            return CheckResult("device", True, "Approved device.", {"device_mac": mac})
    return CheckResult("device", False, "This computer is not an approved attendance device.")
