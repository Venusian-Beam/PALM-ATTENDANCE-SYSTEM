import pytest

from backend import auth
from backend.central.db import ServerDB


def test_admin_password():
    db = ServerDB(":memory:")
    assert not auth.has_admin_password(db)
    with pytest.raises(ValueError):
        auth.set_admin_password(db, "short")
    auth.set_admin_password(db, "correct horse")
    assert auth.check_admin_password(db, "correct horse")
    assert not auth.check_admin_password(db, "wrong password")
    assert not auth.verify_password("x", "garbage")


def test_tokens_expire_when_idle():
    now = [0.0]
    store = auth.TokenStore(idle_seconds=60, clock=lambda: now[0])
    token = store.issue()
    now[0] = 50
    assert store.validate(token)
    now[0] = 100
    assert store.validate(token)
    now[0] = 200
    assert not store.validate(token)
    assert not store.validate(None) and not store.validate("made-up")


def test_login_limiter():
    now = [0.0]
    limiter = auth.LoginLimiter(max_failures=3, window_s=60, clock=lambda: now[0])
    for _ in range(3):
        limiter.failed("1.2.3.4")
    assert limiter.locked("1.2.3.4") and not limiter.locked("5.6.7.8")
    now[0] = 61
    assert not limiter.locked("1.2.3.4")
