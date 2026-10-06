"""Wi-Fi check: the kiosk must be connected to the school network."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

from backend.security.device import normalize_mac
from backend.security.result import CheckResult

_NETSH_LINE = re.compile(r"^\s*([^:]+?)\s+:\s?(.*)$")


@dataclass
class WifiInfo:
    ssid: str
    bssid: str | None
    interface: str = ""


def _safe_mac(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_mac(value)
    except ValueError:
        return None


def parse_netsh(text: str) -> list[WifiInfo]:
    """Parse `netsh wlan show interfaces`, returning connected interfaces only."""
    blocks: list[dict[str, str]] = []
    for line in text.splitlines():
        m = _NETSH_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if key == "Name" or not blocks:
            blocks.append({})
        blocks[-1][key] = value

    result = []
    for b in blocks:
        if b.get("State", "").lower() != "connected" or not b.get("SSID"):
            continue
        bssid = b.get("AP BSSID") or b.get("BSSID")
        result.append(WifiInfo(b["SSID"], _safe_mac(bssid), b.get("Name", "")))
    return result


def _run(cmd: list[str]) -> str:
    kwargs = {}
    encoding = "utf-8"
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        encoding = "oem"
    out = subprocess.run(cmd, capture_output=True, timeout=10, **kwargs)
    return out.stdout.decode(encoding, errors="replace")


def current_wifi() -> list[WifiInfo]:
    if sys.platform != "win32":
        raise RuntimeError("Wi-Fi checks are only supported on Windows.")
    return parse_netsh(_run(["netsh", "wlan", "show", "interfaces"]))


def check_wifi(ssid: str, allowed_bssids: Iterable[str],
               provider: Callable[[], list[WifiInfo]] = current_wifi) -> CheckResult:
    if not ssid:
        return CheckResult("wifi", False, "No school Wi-Fi network is configured.")
    bssids = {normalize_mac(b) for b in allowed_bssids}
    connections = provider()
    if not connections:
        return CheckResult(
            "wifi", False,
            "Not connected to any Wi-Fi network. (On Windows 11 the network name is "
            "only visible when Location services are on.)")

    for conn in connections:
        if conn.ssid != ssid:
            continue
        if bssids and conn.bssid not in bssids:
            return CheckResult(
                "wifi", False,
                f"Connected to '{ssid}' through an access point that is not recognised "
                f"({conn.bssid or 'unknown'}).")
        return CheckResult("wifi", True, f"Connected to '{ssid}'.",
                           {"ssid": conn.ssid, "bssid": conn.bssid})

    names = ", ".join(f"'{c.ssid}'" for c in connections)
    return CheckResult("wifi", False, f"Not on the school Wi-Fi (connected to {names}).")
