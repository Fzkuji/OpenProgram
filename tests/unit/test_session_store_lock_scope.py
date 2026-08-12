from __future__ import annotations

import threading

from openprogram.store.session.git_session import GitSession
from openprogram.store.session.session_store import SessionStore


def _assert_completes_while_blocked(call, blocked: threading.Event) -> None:
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            call()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert done.wait(0.5), "an unrelated session waited for filesystem I/O"
        assert not errors
    finally:
        blocked.set()
        thread.join(2)


def test_slow_session_rebuild_does_not_block_cached_session(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.create_session("slow", "main")
    store.create_session("fast", "main")
    store.invalidate_cache("slow")

    entered = threading.Event()
    release = threading.Event()
    original = GitSession.read_meta

    def blocked_read_meta(git: GitSession):
        if git.path.name == "slow":
            entered.set()
            assert release.wait(2)
        return original(git)

    monkeypatch.setattr(GitSession, "read_meta", blocked_read_meta)
    slow = threading.Thread(target=store.get_session, args=("slow",))
    slow.start()
    assert entered.wait(1)
    try:
        _assert_completes_while_blocked(
            lambda: store.get_session("fast"), release,
        )
    finally:
        release.set()
        slow.join(2)
    assert not slow.is_alive()


def test_slow_session_delete_does_not_block_cached_session(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.create_session("slow", "main")
    store.create_session("fast", "main")

    entered = threading.Event()
    release = threading.Event()
    original = GitSession.destroy

    def blocked_destroy(git: GitSession):
        if git.path.name == "slow":
            entered.set()
            assert release.wait(2)
        return original(git)

    monkeypatch.setattr(GitSession, "destroy", blocked_destroy)
    slow = threading.Thread(target=store.delete_session, args=("slow",))
    slow.start()
    assert entered.wait(1)
    try:
        _assert_completes_while_blocked(
            lambda: store.get_session("fast"), release,
        )
    finally:
        release.set()
        slow.join(2)
    assert not slow.is_alive()


def test_slow_location_publish_does_not_block_cached_session(
    tmp_path, monkeypatch,
):
    store = SessionStore(tmp_path / "sessions")
    store.create_session("fast", "main")

    entered = threading.Event()
    release = threading.Event()
    from openprogram.store.session import session_store as store_module
    original = store_module.atomic_write_text

    def blocked_write(path, text):
        if path == store._locations_path():
            entered.set()
            assert release.wait(2)
        return original(path, text)

    monkeypatch.setattr(store_module, "atomic_write_text", blocked_write)
    slow = threading.Thread(
        target=store._record_location,
        args=("slow", tmp_path / "project" / "slow"),
    )
    slow.start()
    assert entered.wait(1)
    try:
        _assert_completes_while_blocked(
            lambda: store.get_session("fast"), release,
        )
    finally:
        release.set()
        slow.join(2)
    assert not slow.is_alive()
