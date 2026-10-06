"""Set up the school server PC (database + admin portal).

    python scripts/configure_server.py             full setup
    python scripts/configure_server.py --new-cert  create a new HTTPS certificate
                                                   (kiosks must then be set up again)
"""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from _common import ROOT, ask, load_or_create, save, yes

from backend import auth
from backend.central.db import ServerDB
from backend.certs import cert_fingerprint, local_ipv4, make_certificate
from backend.config import DEFAULT_CONFIG_PATH, load_config
from backend.security.location import LocationError, get_fix
from backend.security.network import current_wifi


def setup_password(db: ServerDB) -> None:
    print("\n== Admin (IT officer) password ==")
    if auth.has_admin_password(db) and not yes("An admin password is set. Change it?", False):
        return
    while True:
        first = getpass.getpass(f"New admin password (at least {auth.MIN_PASSWORD_LENGTH} "
                                "characters): ")
        if len(first) < auth.MIN_PASSWORD_LENGTH:
            print("Too short.")
            continue
        if getpass.getpass("Again: ") != first:
            print("Did not match.")
            continue
        auth.set_admin_password(db, first)
        print("Admin password saved.")
        return


def setup_school(db: ServerDB) -> None:
    print("\n== School Wi-Fi and location ==")
    print("These can also be changed later in the admin portal (Settings).")
    school = db.school_settings()
    detected = ""
    try:
        conns = current_wifi()
        detected = conns[0].ssid if conns else ""
    except Exception:
        pass
    school["wifi_ssid"] = ask("School Wi-Fi name that kiosks use",
                              school["wifi_ssid"] or detected)
    if school["lat"] is None or yes("Update the school location?", False):
        print("Getting this computer's location (this can take a few seconds)…")
        try:
            fix = get_fix(30)
            print(f"Found, accurate to about ±{fix.accuracy_m:.0f} m.")
            if yes("Is this PC at the school, and should this be the school's centre?"):
                school["lat"], school["lon"] = round(fix.lat, 6), round(fix.lon, 6)
        except LocationError as exc:
            print(f"Could not get the location: {exc}")
        if school["lat"] is None and yes("Enter the school's coordinates by hand?"):
            school["lat"] = float(ask("Latitude (e.g. 6.524379)"))
            school["lon"] = float(ask("Longitude (e.g. 3.379206)"))
    school["radius_m"] = float(ask("Allowed radius around the school in metres",
                                   str(int(school["radius_m"]))))
    db.set_school_settings(school)
    print("School settings saved.")


def setup_certificate(cfg, force: bool) -> None:
    print("\n== HTTPS certificate ==")
    if cfg.server.cert_file.exists() and not force:
        print("Keeping the existing certificate.")
        return
    covers = make_certificate(cfg.server.cert_file, cfg.server.key_file)
    print(f"Certificate created for {', '.join(covers)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--new-cert", action="store_true")
    args = parser.parse_args()
    path = Path(args.config)
    raw = load_or_create(path, ROOT / "config.server.example.yaml", "server")
    save(path, raw)
    cfg = load_config(path)

    if args.new_cert:
        setup_certificate(cfg, force=True)
        print("Set up every kiosk again with scripts/configure_kiosk.py.")
        return

    db = ServerDB(cfg.database)
    setup_password(db)
    setup_school(db)
    setup_certificate(cfg, force=False)
    db.close()

    print("\nSetup complete. Start the server with:  python main.py server")
    print(f"Certificate fingerprint (to confirm when setting up kiosks):\n  "
          f"{cert_fingerprint(cfg.server.cert_file)}")
    for ip in local_ipv4():
        print(f"Admin portal / kiosk server address: https://{ip}:{cfg.server.port}")


if __name__ == "__main__":
    main()
