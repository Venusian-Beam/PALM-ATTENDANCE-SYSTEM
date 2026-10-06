"""Admin (IT officer) password, login tokens and brute-force lockout."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable

PASSWORD_KEY = "admin_password_hash"
MIN_PASSWORD_LENGTH = 8
_PBKDF2_ITERATIONS = 310_000


def hash_password(password: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex),
                                     int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def set_admin_password(db, password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    db.set_setting(PASSWORD_KEY, hash_password(password))


def has_admin_password(db) -> bool:
    return db.get_setting(PASSWORD_KEY) is not None


def check_admin_password(db, password: str) -> bool:
    return verify_password(password, db.get_setting(PASSWORD_KEY))


class TokenStore:
    """In-memory login tokens that expire after a period of inactivity."""

    def __init__(self, idle_seconds: float = 30 * 60,
                 clock: Callable[[], float] = time.monotonic):
        self._idle = idle_seconds
        self._clock = clock
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = self._clock()
        return token

    def validate(self, token: str | None) -> bool:
        """True if the token is valid; also refreshes its idle timer."""
        if not token:
            return False
        now = self._clock()
        with self._lock:
            last = self._tokens.get(token)
            if last is None or now - last > self._idle:
                self._tokens.pop(token, None)
                return False
            self._tokens[token] = now
            return True

    def revoke(self, token: str | None) -> None:
        with self._lock:
            self._tokens.pop(token or "", None)

    def revoke_all(self) -> None:
        with self._lock:
            self._tokens.clear()


class LoginLimiter:
    """Locks login for an address after too many wrong passwords."""

    def __init__(self, max_failures: int = 5, window_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._max = max_failures
        self._window = window_s
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str) -> list[float]:
        now = self._clock()
        recent = [t for t in self._failures.get(ip, []) if now - t < self._window]
        self._failures[ip] = recent
        return recent

    def locked(self, ip: str) -> bool:
        with self._lock:
            return len(self._recent(ip)) >= self._max

    def failed(self, ip: str) -> None:
        with self._lock:
            self._recent(ip).append(self._clock())

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
