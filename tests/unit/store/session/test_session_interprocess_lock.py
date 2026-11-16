"""Session-id flock is exclusive across overlapping acquire attempts."""
from __future__ import annotations

from openprogram.state.store.session.files.session_lock import (
    session_interprocess_lock,
    session_lock_available,
)


def test_lock_is_reported_held_and_reentrant_in_its_thread(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(tmp_path))
    with session_interprocess_lock("sid-1"):
        assert session_lock_available("sid-1") is False
        # The holder nests on its one flock instead of opening a second
        # descriptor that would wait on the first.
        with session_interprocess_lock("sid-1", blocking=False):
            with session_interprocess_lock("sid-1", timeout=0.0):
                assert session_lock_available("sid-1") is False
    assert session_lock_available("sid-1") is True


def test_another_process_holding_the_flock_times_out_naming_the_conversation(tmp_path, monkeypatch):
    from openprogram.host._platform import locking
    from openprogram.state.store.session.files.session_lock import (
        SessionLockTimeout, session_lock_path,
    )

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(tmp_path))
    # A second open file description stands in for another process.
    with session_lock_path("sid-2").open("a+") as other:
        locking.flock(other.fileno(), locking.LOCK_EX)
        try:
            with session_interprocess_lock("sid-2", timeout=0.0):
                raise AssertionError("the lock is held elsewhere")
        except SessionLockTimeout as exc:
            assert "sid-2" in str(exc) and "another process" in str(exc)
        try:
            with session_interprocess_lock("sid-2", blocking=False):
                raise AssertionError("the lock is held elsewhere")
        except BlockingIOError:
            pass
        locking.flock(other.fileno(), locking.LOCK_UN)
    with session_interprocess_lock("sid-2", timeout=0.0):
        pass


def test_an_error_inside_releases_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: str(tmp_path))
    try:
        with session_interprocess_lock("sid-3"):
            with session_interprocess_lock("sid-3"):
                raise RuntimeError("409")
    except RuntimeError:
        pass
    assert session_lock_available("sid-3") is True


def test_reentrant_key_includes_active_profile(tmp_path, monkeypatch):
    active = {"root": tmp_path / "profile-a"}
    monkeypatch.setattr("openprogram.state.paths.get_state_dir",
                        lambda: active["root"])
    with session_interprocess_lock("sid-profile", reentrant=True):
        active["root"] = tmp_path / "profile-b"
        with session_interprocess_lock("sid-profile", reentrant=True):
            assert (active["root"] / "projects" / ".locks" /
                    "sid-profile.lock").exists()


def test_reentrant_same_profile_does_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    with session_interprocess_lock("sid-nested", reentrant=True):
        with session_interprocess_lock("sid-nested", reentrant=True):
            assert session_lock_available("sid-nested") is False


def test_a_long_hold_is_logged_with_where_it_ended(tmp_path, monkeypatch, caplog):
    """A conversation held for seconds makes every request on it wait; the
    log names the conversation, the thread and the code that held it."""
    import logging

    from openprogram.state.store.session.files import session_lock

    clock = [100.0]
    monkeypatch.setattr(session_lock.time, "monotonic", lambda: clock[0])
    lock = session_lock.ConversationLock("held-long", str(tmp_path / "held-long.lock"))
    with caplog.at_level(logging.WARNING, logger=session_lock.__name__):
        with lock:
            pass
        assert not caplog.records, "a short hold is not logged"
        with lock:
            clock[0] += 4.2
    (record,) = caplog.records
    message = record.getMessage()
    assert "held-long" in message and "was locked for 4.2s" in message
    assert "test_a_long_hold_is_logged_with_where_it_ended" in message


def test_a_hold_spent_waiting_for_another_process_says_so(tmp_path, monkeypatch, caplog):
    """A thread keeps this process's half of the lock while another process
    holds the file, so the log separates that wait from its own work."""
    import logging

    from openprogram.state.store.session.files import session_lock

    clock = [100.0]
    monkeypatch.setattr(session_lock.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(session_lock.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 3.5))
    real_flock, refused = session_lock.locking.flock, []

    def flock(descriptor, operation):
        if operation & session_lock.locking.LOCK_NB and not refused:
            refused.append(True)
            raise BlockingIOError
        return real_flock(descriptor, operation)

    monkeypatch.setattr(session_lock.locking, "flock", flock)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    with caplog.at_level(logging.WARNING, logger=session_lock.__name__):
        with session_interprocess_lock("held-elsewhere"):
            clock[0] += 0.5
    (record,) = caplog.records
    assert ("was locked for 4.0s by thread 'MainThread', "
            "3.5s of it waiting for another process to release it") in record.getMessage()
