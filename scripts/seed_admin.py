"""Create or reset the admin (IT officer) password on the server.

    python scripts/seed_admin.py                          generate a new password
    python scripts/seed_admin.py --password "My-Pass-1"   use this password

Run it on the server PC. The new password works immediately, even while the server
is running. There is one admin account, so there is no username.
"""
from __future__ import annotations

import argparse
import secrets
import string
from pathlib import Path

from _common import ROOT  # noqa: F401  (adds the project to sys.path)

from backend import auth
from backend.central.db import ServerDB
from backend.config import DEFAULT_CONFIG_PATH, load_config


def generate_password() -> str:
    """Easy to read aloud: e.g. Pas-7kmq-4xtd-92 (no look-alike characters)."""
    alphabet = "".join(c for c in string.ascii_lowercase + string.digits if c not in "0o1lI")
    chunk = lambda n: "".join(secrets.choice(alphabet) for _ in range(n))  # noqa: E731
    return f"Pas-{chunk(4)}-{chunk(4)}-{secrets.randbelow(90) + 10}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--password", help="the password to set (default: generate one)")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if cfg.role != "server":
        raise SystemExit("Run this on the server (its config must have role: server).")

    password = args.password or generate_password()
    db = ServerDB(cfg.database)
    try:
        auth.set_admin_password(db, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    db.log_admin("local", "seed_admin_password", "admin password set from the server PC")
    db.close()
    print(f"Admin password set: {password}")
    print("Log in to the admin portal with this password (there is no username).")


if __name__ == "__main__":
    main()
