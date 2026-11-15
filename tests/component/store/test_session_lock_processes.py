"""The conversation lock across processes, and a revert that misses the cache.

Two spawned processes and two threads of this one append to one
conversation at once and every message lands; a process killed while it
holds the conversation's flock frees it for a writer already waiting; and a
revert whose conversation drops out of the store cache inside the journal's
workspace lock finishes instead of replaying its own intent (which took the
workspace lock a second time and hung the conversation for good). Every
wait has a watchdog, so a regression fails instead of hanging the suite.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest

from openprogram.state.store.session.session_store import SessionStore

WATCHDOG = 60.0
WRITES = 12


def _child_store(root: str) -> SessionStore:
    import openprogram.state.paths as paths
    paths.get_state_dir = lambda: Path(root).parent
    return SessionStore(Path(root))


def _child_append(root: str, sid: str, tag: str, queue) -> None:
    try:
        store = _child_store(root)
        for i in range(WRITES):
            head = (store.get_session(sid) or {}).get("head_id")
            mid = f"{tag}-{i}"
            store.append_message(sid, {"id": mid, "role": "user", "content": mid,
                                       "predecessor": head or "r0", "agent_id": "main"})
            store.commit_turn(sid, "turn")
            store.update_session(sid, model=mid)
        queue.put("ok")
    except BaseException:  # noqa: BLE001 - reported to the parent
        queue.put(traceback.format_exc())


def _child_hold(root: str, sid: str, ready) -> None:
    _child_store(root)
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(sid, root=root):
        ready.set()
        time.sleep(600)


def _stuck(thread: threading.Thread) -> str:
    frame = sys._current_frames().get(thread.ident)
    return "".join(traceback.format_stack(frame)) if frame else ""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    s = SessionStore(tmp_path / "projects")
    yield s
    s.close()


def test_two_processes_and_two_threads_append_to_one_conversation(store):
    sid = "shared"
    store.create_session(sid, "main", title="t")
    store.append_message(sid, {"id": "r0", "role": "user", "content": "r0",
                               "predecessor": "", "agent_id": "main"})
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_child_append, args=(str(store.root_path), sid, f"p{k}", queue))
             for k in range(2)]
    for proc in procs:
        proc.start()
    errors: list[str] = []

    def local(tag):
        try:
            for i in range(WRITES):
                head = (store.get_session(sid) or {}).get("head_id")
                mid = f"{tag}-{i}"
                store.append_message(sid, {"id": mid, "role": "user", "content": mid,
                                           "predecessor": head or "r0", "agent_id": "main"})
                store.commit_turn(sid, "turn")
                store.update_session(sid, model=mid)
        except BaseException:  # noqa: BLE001
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=local, args=(f"t{k}",), daemon=True) for k in range(2)]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join(WATCHDOG)
            assert not thread.is_alive(), _stuck(thread)
        for proc in procs:
            proc.join(WATCHDOG)
            assert not proc.is_alive(), "a writer process is stuck"
        results = [queue.get(timeout=5) for _ in procs]
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.kill()
    assert results == ["ok", "ok"], results
    assert not errors, errors[0]
    from openprogram.state.store.session.session_log import SessionLog
    ids = {node.get("id") if isinstance(node, dict) else getattr(node, "id", None)
           for node in SessionLog(store._session_dir(sid)).list_history()}
    missing = [f"{tag}-{i}" for tag in ("p0", "p1", "t0", "t1") for i in range(WRITES)
               if f"{tag}-{i}" not in ids]
    assert not missing


def test_a_killed_holder_frees_the_conversation(store):
    from openprogram.state.store.session.session_lock import SessionLockTimeout, conversation_lock
    sid = "held"
    store.create_session(sid, "main", title="t")
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    proc = ctx.Process(target=_child_hold, args=(str(store.root_path), sid, ready))
    proc.start()
    try:
        assert ready.wait(WATCHDOG)
        with pytest.raises(SessionLockTimeout):
            with store._conversation_write_lock(sid, timeout=0.3):
                pass
        assert not conversation_lock(sid, store.root_path).held()
        writer = threading.Thread(target=lambda: store.update_session(sid, model="after"), daemon=True)
        writer.start()
        time.sleep(0.2)
        assert writer.is_alive()  # waiting on the other process
        os.kill(proc.pid, signal.SIGKILL)
        writer.join(WATCHDOG)
        assert not writer.is_alive(), _stuck(writer)
        assert store.get_session(sid)["model"] == "after"
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(10)


def test_revert_finishes_when_its_conversation_leaves_the_cache(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.revert import revert_turn
    from openprogram.runtime.jobs.store import save_job
    from openprogram.runtime.jobs.types import Job, JobStatus
    from openprogram.state.store.snapshot.checkpoint import CheckpointStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.shared._default_store",
                        store, raising=False)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": "edit"})
    store.append_message("s", {"id": "a1", "role": "assistant", "content": "done", "predecessor": "u1"})
    store.spawn_branch("s", "a1", source="agent_spawn", node_id="child-u",
                       prompt="child", register_head=False)
    store.append_message("s", {"id": "child-a", "role": "assistant", "content": "child done",
                               "predecessor": "child-u"})
    journal = CheckpointStore(store._session_dir("s"))
    for turn_id, name in (("a1", "parent.py"), ("child-a", "child.py")):
        path = tmp_path / name
        path.write_text("before\n", encoding="utf-8")
        journal.backup_before_edit(turn_id, str(path))
        path.write_text("after\n", encoding="utf-8")
        journal.commit_after_edit(turn_id, str(path), operation="edit")
    save_job("s", Job(id="j_owned", parent_session_id="s", prompt="child", agent_id="child",
                      parent_msg_id="a1", caller_msg_id="a1", origin_turn_id="a1",
                      creates_agent=True, relation="owned", status=JobStatus.COMPLETED,
                      head_id="child-a"))
    workspace_lock = CheckpointStore._workspace_lock

    @contextmanager
    def evicting(self, paths):
        with workspace_lock(self, paths):
            # Other conversations' opens push this one out of the cache.
            with store._lock:
                store._sessions.pop("s", None)
            yield

    monkeypatch.setattr(CheckpointStore, "_workspace_lock", evicting)
    out: list[dict] = []
    worker = threading.Thread(target=lambda: out.append(revert_turn("s", "a1", idempotency_key="k")),
                              daemon=True)
    worker.start()
    worker.join(20)
    try:
        assert not worker.is_alive(), _stuck(worker)
        assert out[0]["status"] == "committed", out
        assert (tmp_path / "parent.py").read_text(encoding="utf-8") == "before\n"
    finally:
        store.close()
