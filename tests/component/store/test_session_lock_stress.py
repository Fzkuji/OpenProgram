"""Many threads working one conversation never deadlock the store.

Mirrors what hung a Worker: one conversation with several branches, turns
appending on two branches, renames, a model change, a branch deleted,
concurrent checkouts, a rewind plan, the page load that reads settings
under the conversation lock, and a run admission, all at once. Every
action runs in a daemon thread; a watchdog fails the test (with the stuck
stacks) instead of hanging the suite. Runs on the real store.
"""
from __future__ import annotations

import random
import sys
import threading
import time
import traceback

import pytest

from openprogram.state.store.session.session_store import SessionStore


ROUNDS = 25
WATCHDOG = 60.0


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    s = SessionStore(tmp_path / "projects")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: s)
    import openprogram.state.store.session.session_store as ss_mod
    monkeypatch.setattr(ss_mod.shared, "_default_store", s)
    yield s
    s.close()


@pytest.fixture
def srv(monkeypatch):
    from openprogram.interfaces.server import server as _s
    monkeypatch.setattr(_s, "_sessions", {})
    monkeypatch.setattr(_s, "_msg_cache", type(_s._msg_cache)())
    return _s


def _branches(store, srv, sid):
    """Five branches off u0 -> a1; returns their tips."""
    store.create_session(sid, "main", title="t")
    store.append_message(sid, {"id": "u0", "role": "user", "content": "u0",
                               "predecessor": "", "agent_id": "main"})
    store.append_message(sid, {"id": "a1", "role": "assistant", "content": "a1",
                               "predecessor": "u0", "agent_id": "main"})
    tips = []
    for n in range(5):
        assert srv._set_active_head(sid, "a1")
        u, a = f"u{n}x", f"a{n}x"
        store.append_message(sid, {"id": u, "role": "user", "content": u,
                                   "predecessor": "a1", "agent_id": "main"})
        store.append_message(sid, {"id": a, "role": "assistant", "content": a,
                                   "predecessor": u, "agent_id": "main"})
        tips.append(a)
    return tips


def _stacks(threads) -> str:
    frames = sys._current_frames()
    out = []
    for thread in threads:
        if thread.is_alive() and thread.ident in frames:
            out.append(f"--- {thread.name}\n" + "".join(traceback.format_stack(frames[thread.ident])))
    return "\n".join(out)


def test_concurrent_branch_actions_finish(store, srv):
    from openprogram.interfaces.server.server_runtime import session_lifecycle
    from openprogram.interfaces.server.ws_actions.session import load
    from openprogram.runtime.conversation.rewind import plan_rewind

    sid = "stress"
    tips = _branches(store, srv, sid)
    errors: list[BaseException] = []
    counter = iter(range(10**6))
    tip_lock = threading.Lock()
    live = {0: tips[0], 1: tips[1]}

    def turn(slot):
        # A turn on one branch: append a user/assistant pair at its tip.
        with tip_lock:
            prev = live[slot]
            n = next(counter)
        u, a = f"t{slot}u{n}", f"t{slot}a{n}"
        store.append_message(sid, {"id": u, "role": "user", "content": u,
                                   "predecessor": prev, "agent_id": "main"})
        store.append_message(sid, {"id": a, "role": "assistant", "content": a,
                                   "predecessor": u, "agent_id": "main"})
        store.bump_branch_turns(sid, a)
        with tip_lock:
            live[slot] = a

    def rename():
        store.set_branch_name(sid, random.choice(tips[2:]), f"name {next(counter)}",
                              name_locked=True)

    def set_model():
        store.update_session(sid, model=f"model-{next(counter)}")

    def delete_branch():
        store.delete_branch_name(sid, tips[4])

    def checkout():
        target = random.choice(list(live.values()) + tips[2:4])
        srv._set_active_head(sid, target)

    def load_settings():
        load._capture_load_settings(store, sid, None, None)

    def admission():
        msg = f"m{next(counter)}"
        if session_lifecycle._try_reserve_run(sid, msg):
            session_lifecycle.state._release_run_slot(sid, msg) \
                if hasattr(session_lifecycle.state, "_release_run_slot") else None

    def rewind_plan():
        plan_rewind(sid, "u0")

    def drop_cache():
        # A cache miss makes the next open rebuild and run rewind recovery.
        store.invalidate_session_cache(sid) if hasattr(store, "invalidate_session_cache") \
            else store._sessions.pop(sid, None)

    actions = [lambda: turn(0), lambda: turn(1), rename, rename, set_model,
               delete_branch, checkout, checkout, checkout, checkout,
               rewind_plan, checkout, load_settings, load_settings, admission,
               drop_cache]

    done: list[int] = []
    refused: list[str] = []

    def run(action):
        try:
            for _ in range(ROUNDS):
                try:
                    action()
                    done.append(1)
                except (ValueError, KeyError, RuntimeError, OSError) as exc:
                    # A refusal from a lost race is fine; a lock wait that
                    # ran out is the bug this test is about.
                    if isinstance(exc, TimeoutError):
                        raise
                    refused.append(type(exc).__name__)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(a,), daemon=True, name=f"action-{i}")
               for i, a in enumerate(actions)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + WATCHDOG
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    stuck = [t for t in threads if t.is_alive()]
    assert not stuck, "deadlock:\n" + _stacks(stuck)
    assert not errors, "".join(traceback.format_exception(errors[0]))
    print(f"completed={len(done)} refused={len(refused)} {sorted(set(refused))}")
    assert len(done) > len(actions) * ROUNDS // 2
    # The conversation is still usable afterwards.
    assert store.get_session(sid)["id"] == sid


def test_a_stuck_conversation_errors_instead_of_hanging(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_lock import (
        SessionLockTimeout, session_interprocess_lock,
    )

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    store = SessionStore(tmp_path / "projects")
    held, done = threading.Event(), threading.Event()

    def holder():
        with session_interprocess_lock("stuck"):
            held.set()
            done.wait(30)

    thread = threading.Thread(target=holder, daemon=True, name="stuck-holder")
    thread.start()
    try:
        assert held.wait(10)
        with pytest.raises(SessionLockTimeout, match="conversation stuck.*stuck-holder"):
            with session_interprocess_lock("stuck", timeout=0.2):
                pass
        # The store's own lock for that conversation is the same lock.
        lock = store._session_lock("stuck")
        with pytest.raises(SessionLockTimeout):
            lock.acquire(timeout=0.1)
        # Other conversations are not held up.
        with session_interprocess_lock("other", timeout=0.2):
            pass
    finally:
        done.set()
        thread.join(10)
        store.close()
    with session_interprocess_lock("stuck", timeout=0.2):
        pass
