"""Set up a kiosk laptop and connect it to the school server.

    python scripts/configure_kiosk.py

Afterwards the IT officer adds this laptop's MAC address in the admin portal
(Devices). Until then the kiosk shows "This device is not approved".
"""
from __future__ import annotations

import argparse
import ssl
from pathlib import Path
from urllib.parse import urlparse

from _common import ROOT, ask, load_or_create, save, yes

from backend.certs import fingerprint_pem
from backend.config import DEFAULT_CONFIG_PATH, load_config
from backend.security.device import local_macs


def normalise_url(text: str) -> tuple[str, str, int]:
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host, port = parsed.hostname, parsed.port or 8443
    if not host:
        raise ValueError("Not a valid address.")
    return f"https://{host}:{port}", host, port


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args()
    path = Path(args.config)
    raw = load_or_create(path, ROOT / "config.kiosk.example.yaml", "kiosk")
    kiosk = raw.setdefault("kiosk", {})

    print("== Connect to the school server ==")
    while True:
        try:
            url, host, port = normalise_url(ask("Server address (e.g. 192.168.1.20)",
                                                kiosk.get("server_url", "")))
            pem = ssl.get_server_certificate((host, port), timeout=10)
            break
        except (OSError, ValueError) as exc:
            print(f"Could not reach the server: {exc}. Is it running (python main.py server)?")
    print(f"\nThe server's certificate fingerprint is:\n  {fingerprint_pem(pem)}")
    print("Check it matches the one in the admin portal (Settings) or printed by "
          "configure_server.py.")
    if not yes("Does it match?"):
        print("Not saved. Make sure you typed the right server address.")
        return

    kiosk["server_url"] = url
    save(path, raw)
    cfg = load_config(path)
    cfg.kiosk.server_cert.parent.mkdir(parents=True, exist_ok=True)
    cfg.kiosk.server_cert.write_text(pem)
    print(f"Server certificate saved to {cfg.kiosk.server_cert}.")

    print("\n== Approve this kiosk ==")
    print("In the admin portal, open Devices > Add device and enter a name and ONE of these "
          "MAC addresses (the Wi-Fi one is usually best):")
    for mac, iface in local_macs().items():
        print(f"  {mac}   ({iface})")
    print("\nTip: in Windows Wi-Fi settings, turn OFF 'Random hardware addresses' for the "
          "school network, or the Wi-Fi MAC may change.")
    print("\nThen start the kiosk with:  python main.py kiosk")


if __name__ == "__main__":
    main()
