"""Concurrent replay of one rewind request executes one transaction."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore


def test_concurrent_same_key_executes_one_rewind(tmp_path, monkeypatch):
    from openprogram.agent._rewind import plan_rewind, rewind_to

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", store,
        raising=False,
    )
    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
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
    plan = plan_rewind(session_id, "u2")

    def apply():
        return rewind_to(
            session_id, "u2",
            idempotency_key=plan["idempotency_key"],
            expected_plan_hash=plan["plan_hash"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: apply(), range(2)))

    assert [result["status"] for result in results] == ["committed", "committed"]
    assert sum(not result["replayed"] for result in results) == 1
    assert target.read_text(encoding="utf-8") == "v1\n"
    assert store.get_session(session_id)["head_id"] == "a1"


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
        assert release_write.wait(timeout=5)
        return original_write(meta)

    monkeypatch.setattr(first_git, "write_meta", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        cas = executor.submit(
            first.compare_and_set_head, session_id, "u2", "a1",
        )
        assert entered_write.wait(timeout=5)
        ordinary = executor.submit(second.set_head, session_id, "u1")
        release_write.set()
        assert cas.result(timeout=5) is True
        ordinary.result(timeout=5)

    fresh = SessionStore(root)
    assert fresh.get_session(session_id)["head_id"] == "u1"
