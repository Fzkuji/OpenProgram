from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from openprogram.store.session.session_store import SessionStore


@pytest.mark.parametrize("recover_threads", [False, True])
def test_thread_start_failure_falls_back_to_synchronous_index_write(
    tmp_path: Path, monkeypatch, recover_threads: bool,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial")
    store._update_index_entry("s1", title="saved without a thread")
    original_timer = threading.Timer

    def unavailable_timer(delay, callback):
        timer = original_timer(delay, callback)

        def fail_start():
            raise RuntimeError("can't start new thread")

        timer.start = fail_start
        return timer

    monkeypatch.setattr(threading, "Timer", unavailable_timer)
    try:
        store._schedule_index_flush()
        assert store._index_timer is None
        assert not store._index_flush_threads
        assert not store._index_dirty
        assert json.loads(store._index_path().read_text())["s1"]["title"] == "saved without a thread"
        if recover_threads:
            monkeypatch.setattr(threading, "Timer", original_timer)
            store._update_index_entry("s1", title="background work resumed")
            store._schedule_index_flush()
            timer = store._index_timer
            assert timer is not None and timer.is_alive()
        store.close()  # Must never attempt to join a thread that did not start.
        if recover_threads:
            assert not timer.is_alive()
            assert json.loads(store._index_path().read_text())["s1"]["title"] == "background work resumed"
    finally:
        # Keep cleanup valid when exercising the pre-fix implementation too.
        with store._index_lock:
            timers = tuple(store._index_flush_threads)
            store._index_timer = None
            store._index_flush_threads.clear()
        for timer in timers:
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=5)
        store.close()


def test_close_flushes_pending_index_and_joins_timer(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial")
    store._update_index_entry("s1", title="pending")
    store._schedule_index_flush()
    timer = store._index_timer
    assert timer is not None and timer.is_alive()
    try:
        store.close()
        assert not timer.is_alive()
        assert json.loads(store._index_path().read_text())["s1"]["title"] == "pending"
        store.close()
        store._update_index_entry("s1", title="reused synchronously")
        store._schedule_index_flush()
        assert store._index_timer is None
        assert not store._index_flush_threads
        assert json.loads(store._index_path().read_text())["s1"]["title"] == "reused synchronously"
    finally:
        timer.cancel()
        timer.join(timeout=5)
        store._flush_index()


def test_close_retains_dirty_data_for_retry_after_io_failure(tmp_path: Path, monkeypatch) -> None:
    import openprogram.store.session.session_store as module

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial")
    store._update_index_entry("s1", title="must survive")
    original_write = module.atomic_write_text
    unregistered = []
    original_unregister = module.atexit.unregister

    def unavailable(*_args, **_kwargs):
        raise OSError("injected write failure")

    def unregister(callback):
        unregistered.append(callback)
        original_unregister(callback)

    monkeypatch.setattr(module.atexit, "unregister", unregister)
    monkeypatch.setattr(module, "atomic_write_text", unavailable)
    try:
        store.close()
        assert store._index_dirty
        assert unregistered == []
    finally:
        monkeypatch.setattr(module, "atomic_write_text", original_write)
        store.close()
    assert not store._index_dirty
    assert unregistered == [store._flush_index]
    assert json.loads(store._index_path().read_text())["s1"]["title"] == "must survive"


def test_close_waits_for_inflight_flush_and_persists_newer_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial")
    store._update_index_entry("s1", title="old snapshot")
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    closed = threading.Event()
    errors = []
    timers = []
    original_timer = threading.Timer
    original_save = store._save_index

    def immediate_timer(_delay, callback):
        timer = original_timer(0, callback)
        original_cancel = timer.cancel

        def cancel():
            original_cancel()
            cancelled.set()

        timer.cancel = cancel
        timers.append(timer)
        return timer

    def paused_save():
        if threading.current_thread() in timers:
            entered.set()
            assert release.wait(10)
        original_save()

    def close():
        try:
            store.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            closed.set()

    monkeypatch.setattr(threading, "Timer", immediate_timer)
    monkeypatch.setattr(store, "_save_index", paused_save)
    store._schedule_index_flush()
    closer = threading.Thread(target=close)
    try:
        assert entered.wait(5)
        store._update_index_entry("s1", title="new snapshot")
        closer.start()
        assert cancelled.wait(5)
        assert not closed.is_set(), "close returned while its writer was still running"
        release.set()
        closer.join(timeout=5)
        assert not closer.is_alive()
        assert not errors
        assert all(not timer.is_alive() for timer in timers)
        assert json.loads(store._index_path().read_text())["s1"]["title"] == "new snapshot"
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(timeout=5)
        for timer in timers:
            timer.cancel()
            timer.join(timeout=5)
        store._flush_index()
