"""Session-id flock is exclusive across overlapping acquire attempts."""
from __future__ import annotations

from openprogram.store.session.session_lock import (
    session_interprocess_lock,
    session_lock_available,
)


def test_nonblocking_acquire_fails_while_held(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: str(tmp_path))
    with session_interprocess_lock("sid-1"):
        assert session_lock_available("sid-1") is False
        raised = False
        try:
            with session_interprocess_lock("sid-1", blocking=False):
                pass
        except BlockingIOError:
            raised = True
        assert raised
    assert session_lock_available("sid-1") is True


def test_timeout_does_not_take_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: str(tmp_path))
    with session_interprocess_lock("sid-2"):
        try:
            with session_interprocess_lock("sid-2", timeout=0.0):
                assert False, "nested lock should time out"
        except TimeoutError:
            pass


def test_reentrant_key_includes_active_profile(tmp_path, monkeypatch):
    active = {"root": tmp_path / "profile-a"}
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: active["root"])
    with session_interprocess_lock("sid-profile", reentrant=True):
        active["root"] = tmp_path / "profile-b"
        with session_interprocess_lock("sid-profile", reentrant=True):
            assert (active["root"] / "sessions" / ".locks" /
                    "sid-profile.lock").exists()


def test_reentrant_same_profile_does_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    with session_interprocess_lock("sid-nested", reentrant=True):
        with session_interprocess_lock("sid-nested", reentrant=True):
            assert session_lock_available("sid-nested") is False
