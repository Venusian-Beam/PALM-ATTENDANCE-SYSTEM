"""Load and validate config.yaml.

The same program runs in two roles:
  role: server   the school server PC (database + admin portal)
  role: kiosk    a camera kiosk that syncs with the server
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


class ConfigError(Exception):
    pass


def parse_hhmm(value: str) -> time:
    try:
        hours, minutes = str(value).strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise ConfigError(f"Not a valid time (expected HH:MM): {value!r}") from None


# School-wide settings. These live in the server database (edited in the admin
# portal) and are sent to every kiosk; they are not read from config.yaml.

@dataclass
class WifiConfig:
    ssid: str = ""
    allowed_bssids: list[str] = field(default_factory=list)


@dataclass
class GeofenceConfig:
    lat: float | None = None
    lon: float | None = None
    radius_m: float = 200.0
    accuracy_buffer_m: float = 100.0
    max_accuracy_m: float = 500.0


# ---- config.yaml sections ---------------------------------------------------------

@dataclass
class ServerConfig:
    port: int = 8443
    cert_file: Path = Path("data/cert.pem")
    key_file: Path = Path("data/key.pem")
    # Optional IP ranges, e.g. ["192.168.1.0/24"]. Empty = any address.
    admin_allowed_networks: list[str] = field(default_factory=list)
    device_allowed_networks: list[str] = field(default_factory=list)
    backups_keep_days: int = 30


@dataclass
class KioskConfig:
    server_url: str = ""
    server_cert: Path = Path("data/server_cert.pem")
    local_port: int = 8000
    open_browser: bool = True
    max_offline_hours: float = 12.0
    sync_interval_s: float = 5.0


@dataclass
class SecurityConfig:
    recheck_minutes: float = 5.0
    location_timeout_s: float = 20.0


@dataclass
class AttendanceConfig:
    clock_out_after: str = "17:30"   # initial value; the admin portal can change it
    min_session_minutes: float = 2.0
    cooldown_s: float = 30.0
    signout_blinks: int = 3
    blink_timeout_s: float = 8.0
    question_timeout_s: float = 8.0


@dataclass
class FaceConfig:
    match_threshold: float = 0.40
    min_face_px: int = 110
    enroll_samples: int = 5


@dataclass
class LivenessConfig:
    blink_closed: float = 0.5
    blink_open: float = 0.25
    face_lost_s: float = 1.5


@dataclass
class GestureConfig:
    nod_threshold: float = 0.10
    shake_threshold: float = 0.25


@dataclass
class StabilityConfig:
    """Keeps sign-in fast when the kiosk is bumped or shaking."""
    max_shift_frac: float = 0.02   # whole-image move per frame (share of width) still "steady"
    sharpness_ratio: float = 0.5   # face must be this sharp compared with recent sharp faces
    min_sharpness: float = 15.0    # absolute floor for "sharp"
    hint_after_s: float = 1.0      # show "Hold still…" after this long without a usable frame
    max_grace_s: float = 4.0       # most time a shake may add to one check's deadlines
    reverify_window_s: float = 2.0  # after a jolt, time to re-confirm the same face


@dataclass
class LightingConfig:
    """Bright light behind students: expose for the face, and brighten dark faces."""
    dark_face: float = 70.0          # face brightness (0-255) below which it is "dark"
    backlit_gap: float = 60.0        # background at least this much brighter = backlit
    target_face: float = 130.0       # brighten dark faces to about this level
    hint_after_s: float = 1.5        # show "Too much light behind you" after this long


@dataclass
class AntiSpoofConfig:
    """Blocks photos, phones, screens and other objects held up to the camera."""
    threshold: float = 0.3       # anti-spoof "real" score needed (kiosk tuning can change it)
    smooth_frames: int = 10      # the score is judged over this many frames
    min_scores: int = 8          # never judge the face on fewer frames than this
    low_share: float = 0.7       # block only if this share of those frames scored low
    min_face_px: int = 60        # faces narrower than this are too small to score
    object_score: float = 0.55   # object detector confidence that counts
    object_every_n: int = 3      # run the object detector on every Nth frame
    frame_edges: bool = True     # look for the edges of a photo or phone around the face
    planarity: bool = True       # a face that moves like a flat sheet is a photo
    no_blink_s: float = 5.0      # a face asked to blink that never really blinks ...
    suspect_score: float = 0.6   # ... and scores below this is a photo or screen
    blink_shape_change: float = 0.05  # a real blink barely changes the rest of the face
    clean_for_s: float = 1.0     # stay blocked until the picture is clean for this long


@dataclass
class Config:
    role: str = "kiosk"
    database: Path = Path("data/pas.db")          # server
    cache_database: Path = Path("data/kiosk.db")  # kiosk
    models_dir: Path = Path("models")
    camera_index: int = 0
    server: ServerConfig = field(default_factory=ServerConfig)
    kiosk: KioskConfig = field(default_factory=KioskConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    attendance: AttendanceConfig = field(default_factory=AttendanceConfig)
    face: FaceConfig = field(default_factory=FaceConfig)
    liveness: LivenessConfig = field(default_factory=LivenessConfig)
    gestures: GestureConfig = field(default_factory=GestureConfig)
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    lighting: LightingConfig = field(default_factory=LightingConfig)
    antispoof: AntiSpoofConfig = field(default_factory=AntiSpoofConfig)
    # Filled at runtime on kiosks from the server's school settings.
    wifi: WifiConfig = field(default_factory=WifiConfig)
    geofence: GeofenceConfig = field(default_factory=GeofenceConfig)
    path: Path | None = None


_SECTIONS = {
    "server": ServerConfig,
    "kiosk": KioskConfig,
    "security": SecurityConfig,
    "attendance": AttendanceConfig,
    "face": FaceConfig,
    "liveness": LivenessConfig,
    "gestures": GestureConfig,
    "stability": StabilityConfig,
    "lighting": LightingConfig,
    "antispoof": AntiSpoofConfig,
}
_RUNTIME_ONLY = {"wifi", "geofence", "path"}


# Settings that no longer exist; older config files may still contain them.
_RETIRED = {"camera_control", "backlight_compensation", "max_exposure"}


def _build(cls, data: Any, where: str, skip: set[str] = frozenset()):
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"'{where}' must be a mapping.")
    if where == "lighting":
        data = {k: v for k, v in data.items() if k not in _RETIRED}
    allowed = {f.name for f in fields(cls)} - skip
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"Unknown setting(s) in '{where}': {', '.join(sorted(unknown))}")
    return cls(**data)


def _resolve(base: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path.name} not found. Run scripts/configure_server.py (on the "
                          "server PC) or scripts/configure_kiosk.py (on a kiosk) first.")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError("The config file must be a mapping.")

    sections = {name: _build(cls, raw.pop(name, None), name) for name, cls in _SECTIONS.items()}
    cfg = _build(Config, raw, "top level", skip=set(_SECTIONS) | _RUNTIME_ONLY)
    for name, value in sections.items():
        setattr(cfg, name, value)
    if cfg.role not in ("server", "kiosk"):
        raise ConfigError("role must be 'server' or 'kiosk'.")

    base = path.parent
    cfg.path = path
    for attr in ("database", "cache_database", "models_dir"):
        setattr(cfg, attr, _resolve(base, getattr(cfg, attr)))
    cfg.server.cert_file = _resolve(base, cfg.server.cert_file)
    cfg.server.key_file = _resolve(base, cfg.server.key_file)
    cfg.kiosk.server_cert = _resolve(base, cfg.kiosk.server_cert)
    cfg.kiosk.server_url = str(cfg.kiosk.server_url or "").rstrip("/")
    try:
        for net in cfg.server.admin_allowed_networks + cfg.server.device_allowed_networks:
            ipaddress.ip_network(net, strict=False)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    parse_hhmm(cfg.attendance.clock_out_after)
    if not 0 < cfg.face.match_threshold < 1:
        raise ConfigError("face.match_threshold must be between 0 and 1.")
    if cfg.attendance.signout_blinks < 2:
        raise ConfigError("attendance.signout_blinks must be at least 2.")
    return cfg


def missing_kiosk_setup(cfg: Config) -> list[str]:
    missing = []
    if not cfg.kiosk.server_url.startswith("https://"):
        missing.append("kiosk.server_url (must start with https://)")
    if not cfg.kiosk.server_cert.exists():
        missing.append(f"the server certificate ({cfg.kiosk.server_cert.name})")
    return missing
