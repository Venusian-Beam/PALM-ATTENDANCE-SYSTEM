"""Start PAS.

On the school server PC:   python main.py server
On each kiosk laptop:      python main.py kiosk            (opens Edge full screen)
                           python main.py kiosk --no-browser
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from pathlib import Path

import uvicorn

from backend import auth
from backend.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, missing_kiosk_setup

log = logging.getLogger("pas")

EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def fail(message: str) -> None:
    print(f"\nPAS cannot start:\n  {message}\n", file=sys.stderr)
    sys.exit(1)


def setup_logging(data_dir: Path, name: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(data_dir / name, encoding="utf-8"),
                  logging.StreamHandler()])


# ---- server ----------------------------------------------------------------------------

def backup_loop(db, backup_dir: Path, keep_days: int, stop: threading.Event) -> None:
    """One backup per day, keeping `keep_days` days."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        today = date.today()
        target = backup_dir / f"pas-{today.isoformat()}.db"
        try:
            if not target.exists():
                db.backup_to(target)
                log.info("Database backed up to %s", target)
            for old in backup_dir.glob("pas-*.db"):
                try:
                    day = date.fromisoformat(old.stem[4:])
                except ValueError:
                    continue
                if day < today - timedelta(days=keep_days):
                    old.unlink()
        except Exception:
            log.exception("Backup failed")
        stop.wait(3600)


def housekeeping_loop(db, cfg, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            closed = db.close_stale_sessions(db.cutoff(cfg.attendance.clock_out_after),
                                             date.today())
            if closed:
                log.info("Marked %d session(s) 'No sign-out'", closed)
        except Exception:
            log.exception("Housekeeping failed")
        stop.wait(3600)


def run_server(cfg) -> None:
    from backend.central.app import FRONTEND_DIST, create_server_app
    from backend.central.db import ServerDB

    setup_logging(cfg.database.parent, "server.log")
    if not (cfg.server.cert_file.exists() and cfg.server.key_file.exists()):
        fail("No HTTPS certificate. Run `python scripts/configure_server.py`.")
    db = ServerDB(cfg.database)
    if not auth.has_admin_password(db):
        fail("No admin password is set. Run `python scripts/configure_server.py`.")
    if not (FRONTEND_DIST / "index.html").exists():
        fail("The screens are not built. Run `npm install` and `npm run build` in frontend/.")

    stop = threading.Event()
    for target, args in ((backup_loop, (db, cfg.database.parent / "backups",
                                        cfg.server.backups_keep_days, stop)),
                         (housekeeping_loop, (db, cfg, stop))):
        threading.Thread(target=target, args=args, daemon=True).start()

    app = create_server_app(cfg, db)
    log.info("PAS server running. Admin portal: https://<this PC's IP>:%d/admin  "
             "(Ctrl+C to stop)", cfg.server.port)
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.server.port, log_level="warning",
                    ssl_certfile=str(cfg.server.cert_file),
                    ssl_keyfile=str(cfg.server.key_file), timeout_graceful_shutdown=3)
    finally:
        stop.set()
        db.close()


# ---- kiosk -----------------------------------------------------------------------------

def open_kiosk_window(url: str, profile_dir: Path) -> None:
    edge = shutil.which("msedge") or next((str(p) for p in EDGE_PATHS if p.exists()), None)
    if not edge:
        log.warning("Microsoft Edge not found; opening the default browser instead.")
        webbrowser.open(url)
        return
    subprocess.Popen([edge, f"--kiosk={url}", "--edge-kiosk-type=fullscreen", "--no-first-run",
                      f"--user-data-dir={profile_dir}"])


def build_spoof_guard(cfg, store):
    """Photo / phone / object detection for a kiosk (models from scripts/download_models.py)."""
    from backend.face.antispoof import AntiSpoofNet
    from backend.face.objects import ObjectDetector
    from backend.face.spoof import ObjectWatcher, SpoofGuard

    def threshold() -> float:
        try:
            return float(store.get("spoof_threshold") or cfg.antispoof.threshold)
        except ValueError:
            return cfg.antispoof.threshold

    def object_threshold() -> float:
        try:
            return float(store.get("object_threshold") or cfg.antispoof.object_score)
        except ValueError:
            return cfg.antispoof.object_score

    # The detector keeps everything down to the lowest phone threshold training can choose;
    # SpoofGuard then applies this kiosk's own threshold.
    from backend.central.tuning import OBJECT_LOWEST
    objects = ObjectWatcher(ObjectDetector(cfg.models_dir,
                                           min(OBJECT_LOWEST, cfg.antispoof.object_score)))
    return SpoofGuard(cfg.antispoof, AntiSpoofNet(cfg.models_dir), objects, threshold,
                      blink_closed=cfg.liveness.blink_closed, blink_open=cfg.liveness.blink_open,
                      object_threshold=object_threshold)


def run_kiosk(cfg, open_browser: bool) -> None:
    from backend.central.app import FRONTEND_DIST
    from backend.face.engine import FaceEngine, ModelsMissingError
    from backend.face.signals import FaceSignalsExtractor
    from backend.kiosk.app import create_kiosk_app
    from backend.kiosk.runtime import KioskRuntime
    from backend.kiosk.store import KioskStore
    from backend.kiosk.sync import make_http_client

    data_dir = cfg.cache_database.parent
    setup_logging(data_dir, "kiosk.log")
    missing = missing_kiosk_setup(cfg)
    if missing:
        fail("Not set up yet: " + ", ".join(missing) +
             ". Run `python scripts/configure_kiosk.py`.")
    if not (FRONTEND_DIST / "index.html").exists():
        fail("The screens are not built. Run `npm install` and `npm run build` in frontend/.")
    try:
        engine = FaceEngine(cfg.models_dir)
        extractor = FaceSignalsExtractor(cfg.models_dir)
    except ModelsMissingError as exc:
        fail(str(exc))

    store = KioskStore(cfg.cache_database)
    runtime = KioskRuntime(cfg, store, engine, extractor,
                           make_http_client(cfg.kiosk.server_url, cfg.kiosk.server_cert),
                           spoof=build_spoof_guard(cfg, store))
    runtime.start()
    app = create_kiosk_app(runtime, make_http_client(cfg.kiosk.server_url, cfg.kiosk.server_cert))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cfg.kiosk.local_port,
                                           log_level="warning", timeout_graceful_shutdown=2))
    url = f"http://127.0.0.1:{cfg.kiosk.local_port}/"
    log.info("Kiosk screen: %s   (Ctrl+C here to stop)", url)
    if open_browser and cfg.kiosk.open_browser:
        def launch():
            while not server.started:
                time.sleep(0.2)
            open_kiosk_window(url, data_dir / "edge-kiosk-profile")
        threading.Thread(target=launch, daemon=True).start()
    try:
        server.run()
    finally:
        runtime.stop()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="PAS attendance")
    parser.add_argument("role", choices=["server", "kiosk"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--no-browser", action="store_true",
                        help="kiosk: do not open the full-screen browser")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        fail(str(exc))
    if cfg.role != args.role:
        fail(f"{Path(args.config).name} is set up for the '{cfg.role}' role, not "
             f"'{args.role}'. Run `python main.py {cfg.role}`.")
    if args.role == "server":
        run_server(cfg)
    else:
        run_kiosk(cfg, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
