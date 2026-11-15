"""Concurrent replay of one rewind request executes one transaction."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Event

import pytest

from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.snapshot.checkpoint import CheckpointStore


def _three_turn_session(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        'openprogram.state.store.session.session_store.shared._default_store', store,
        raising=False,
    )
    monkeypatch.setattr(
        "openprogram.state.paths.get_state_dir", lambda: tmp_path / "state",
    )
    session_id = "s-concurrent-key"
    store.create_session(session_id, "main", title="concurrent rewind")
    target = tmp_path / "work" / "same.py"
    target.parent.mkdir(parents=True)
    target.write_text("v0\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    predecessor = None
    for number in range(1, 4):
        user_id = f"u{number}"
        assistant_id = f"a{number}"
        store.append_message(session_id, {
            "id": user_id, "role": "user", "content": user_id,
            "predecessor": predecessor,
        })
        journal.backup_before_edit(assistant_id, str(target))
        target.write_text(f"v{number}\n", encoding="utf-8")
        journal.commit_after_edit(assistant_id, str(target), operation="edit")
        store.append_message(session_id, {
            "id": assistant_id, "role": "assistant", "content": assistant_id,
            "predecessor": user_id,
        })
        predecessor = assistant_id
    store.set_head(session_id, "a3")
    return store, session_id, target


def _apply(session_id, plan):
    from openprogram.runtime.conversation.rewind import rewind_to

    return rewind_to(
        session_id, "u2",
        idempotency_key=plan["idempotency_key"],
        expected_plan_hash=plan["plan_hash"],
    )


def _pause_first_call(monkeypatch, module, name):
    """Hold the first caller of ``module.name`` until released."""
    entered, release = Event(), Event()
    original = getattr(module, name)
    calls = []

    def paused(*args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=30)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    return entered, release


def _second_arrives(monkeypatch, store, session_id):
    """Signal when a second thread starts working on the conversation."""
    arrived = Event()
    first_thread = []
    original = store._open

    def tracked(sid, *args, **kwargs):
        if sid == session_id:
            if not first_thread:
                first_thread.append(threading.get_ident())
            elif threading.get_ident() != first_thread[0]:
                arrived.set()
        return original(sid, *args, **kwargs)

    monkeypatch.setattr(store, "_open", tracked)
    return arrived


@pytest.mark.parametrize("pause_at", ["_build_plan", "_cas_for_intent"])
def test_same_key_while_first_runs_waits_and_replays(tmp_path, monkeypatch, pause_at):
    """First paused before its intent is written, or after it while moving HEAD."""
    from openprogram.runtime.conversation import rewind
    from openprogram.runtime.conversation.rewind import plan_rewind

    store, session_id, target = _three_turn_session(tmp_path, monkeypatch)
    plan = plan_rewind(session_id, "u2")
    asked = _second_arrives(monkeypatch, store, session_id)
    entered, release = _pause_first_call(monkeypatch, rewind, pause_at)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_apply, session_id, plan)
        assert entered.wait(timeout=30)
        second = executor.submit(_apply, session_id, plan)
        assert asked.wait(timeout=30)
        # The second must wait for the first; given time it must not finish
        # on its own (a lost race would let it plan and apply by itself).
        assert not wait([second], timeout=0.5).done
        release.set()
        results = [first.result(timeout=30), second.result(timeout=30)]

    assert [result["status"] for result in results] == ["committed", "committed"]
    assert [result["replayed"] for result in results] == [False, True]
    assert target.read_text(encoding="utf-8") == "v1\n"
    assert store.get_session(session_id)["head_id"] == "a1"


def test_same_key_after_first_committed_replays(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.rewind import plan_rewind

    store, session_id, target = _three_turn_session(tmp_path, monkeypatch)
    plan = plan_rewind(session_id, "u2")
    first = _apply(session_id, plan)
    target.write_text("edited after the rewind\n", encoding="utf-8")
    second = _apply(session_id, plan)

    assert (first["status"], first["replayed"]) == ("committed", False)
    assert (second["status"], second["replayed"]) == ("committed", True)
    assert second["transaction_id"] == first["transaction_id"]
    assert target.read_text(encoding="utf-8") == "edited after the rewind\n"
    assert store.get_session(session_id)["head_id"] == "a1"


def test_same_key_after_first_failed_returns_its_outcome(tmp_path, monkeypatch):
    from openprogram.runtime.conversation import rewind
    from openprogram.runtime.conversation.rewind import plan_rewind

    store, session_id, target = _three_turn_session(tmp_path, monkeypatch)
    plan = plan_rewind(session_id, "u2")
    original = rewind._cas_for_intent
    refused = []

    def refuse_first(*args, **kwargs):
        if not refused:
            refused.append(None)
            return False
        return original(*args, **kwargs)

    monkeypatch.setattr(rewind, "_cas_for_intent", refuse_first)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: _apply(session_id, plan), range(2)))

    first, second = sorted(results, key=lambda result: bool(result["replayed"]))
    assert first["status"] not in {"committed", "error"}
    assert (second["status"], second["replayed"]) == (first["status"], True)
    assert second["transaction_id"] == first["transaction_id"]
    assert target.read_text(encoding="utf-8") == "v3\n"
    assert store.get_session(session_id)["head_id"] == "a3"


def test_concurrent_revert_with_one_key_waits_and_replays(tmp_path, monkeypatch):
    """A second revert with the key waits for the first, then replays it."""
    from openprogram.runtime.conversation.revert import revert_turn

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        'openprogram.state.store.session.session_store.shared._default_store', store,
        raising=False,
    )
    session_id, turn_id = "s-revert-race", "u1_reply"
    store.create_session(session_id, "main", title="revert race")
    store.append_message(session_id, {"id": "u1", "role": "user", "content": "edit"})
    store.append_message(session_id, {
        "id": turn_id, "role": "assistant", "content": "done", "predecessor": "u1",
    })
    target = tmp_path / "race.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(turn_id, str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(turn_id, str(target), operation="edit")
    entered, release = Event(), Event()
    original = CheckpointStore.plan_history_operation
    calls = []

    def paused(self, *args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=30)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CheckpointStore, "plan_history_operation", paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(revert_turn, session_id, turn_id, idempotency_key="undo-1")
        assert entered.wait(timeout=30)
        second = executor.submit(revert_turn, session_id, turn_id, idempotency_key="undo-1")
        assert not wait([second], timeout=0.5).done
        release.set()
        results = [first.result(timeout=30), second.result(timeout=30)]

    assert [result["status"] for result in results] == ["committed", "committed"]
    assert results[1]["transaction_id"] == results[0]["transaction_id"]
    assert target.read_text(encoding="utf-8") == "before\n"


def test_head_cas_and_set_head_share_cross_process_lock(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    first = SessionStore(root)
    session_id = "s-head-writers"
    first.create_session(session_id, "main", title="head writers")
    predecessor = None
    for node_id, role in (("u1", "user"), ("a1", "assistant"), ("u2", "user")):
        first.append_message(session_id, {
            "id": node_id, "role": role, "content": node_id,
            "predecessor": predecessor,
        })
        predecessor = node_id
    first.set_head(session_id, "u2")
    second = SessionStore(root)
    first_git, _first_index = first._open(session_id)
    entered_write = Event()
    release_write = Event()
    original_write = first_git.write_meta

    def delayed_write(meta):
        entered_write.set()
        assert release_write.wait(timeout=30)
        return original_write(meta)

    monkeypatch.setattr(first_git, "write_meta", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        cas = executor.submit(
            first.compare_and_set_head, session_id, "u2", "a1",
        )
        assert entered_write.wait(timeout=30)
        ordinary = executor.submit(second.set_head, session_id, "u1")
        release_write.set()
        assert cas.result(timeout=30) is True
        ordinary.result(timeout=30)

    fresh = SessionStore(root)
    assert fresh.get_session(session_id)["head_id"] == "u1"
