"""Queryable status for the background memory writer."""
from __future__ import annotations

import atexit
import json
import sys
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from openprogram.memory.backend import MemoryWriteFailureCode


def _close_store(store) -> None:
    store._flush_index()
    atexit.unregister(store._flush_index)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    import openprogram.paths as paths
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import set_backend, store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    db = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    try:
        yield db, store.ensure()
    finally:
        set_backend(None)
        _close_store(db)


def _append(db, session_id: str, node_id: str, predecessor: str | None = None,
            *, role: str = "user", content: str | None = None, **metadata):
    from openprogram.agent.authority import local_owner_authority

    message = {
        "id": node_id,
        "role": role,
        "content": content if content is not None else node_id,
        **local_owner_authority(),
        **metadata,
    }
    if predecessor is not None:
        message["predecessor"] = predecessor
    db.append_message(session_id, message)


def test_status_counts_all_eligible_unmarked_session_turns_read_only(
    environment, monkeypatch,
):
    from openprogram.memory import store
    from openprogram.memory.management.transaction import (
        workspace_revision,
    )
    from openprogram.memory.retrieval import inspect
    from openprogram.memory import writing

    db, root = environment
    _append(db, "one", "u1")
    _append(db, "one", "a1", "u1", role="assistant")
    _append(db, "two", "u2")
    _append(db, "two", "runtime", "u2", display="runtime")
    db.merge_node_metadata("one", "u1", {
        writing.WRITTEN_NODE_MARKER: store.workspace_id(),
    })
    before_rows = {
        sid: db.get_messages(sid) for sid in ("one", "two")
    }
    before_revision = workspace_revision(root)
    monkeypatch.setattr(
        writing, "_agent",
        lambda *_a, **_kw: pytest.fail("status must not create a writer agent"),
    )

    result = inspect.status(root)

    assert result["writer"] == {
        "last_outcome": None,
        "last_success_at": None,
        "last_failure": None,
        "pending_turns": 2,
    }
    assert {sid: db.get_messages(sid) for sid in ("one", "two")} == before_rows
    assert workspace_revision(root) == before_revision


def test_success_and_per_turn_failure_are_persisted_without_sensitive_text(
    environment, monkeypatch,
):
    from openprogram.agent import dispatcher
    from openprogram.memory import store
    from openprogram.memory import writing
    from openprogram.memory.management.transaction import (
        workspace_revision,
    )
    from openprogram.memory.retrieval import inspect

    db, root = environment
    _append(db, "written", "u1", content="remember this")
    monkeypatch.setattr(writing, "_counter", lambda: len)
    monkeypatch.setattr(writing, "_agent", lambda *_a, **_kw: object())
    monkeypatch.setattr(writing, "organize_topics", lambda *_a, **_kw: [])
    monkeypatch.setattr(writing, "_run_agent", lambda *_a, **_kw: [{
        "tool": "commit", "status": "ok",
        "topic_paths": ["topics/note.md"],
    }])

    assert writing.write_session(
        "written", db.get_branch("written"), token_threshold=1, force=True,
    ) is True
    after_success = inspect.status(root)
    stamp = after_success["writer"]["last_success_at"]
    assert datetime.fromisoformat(stamp).tzinfo is not None
    assert after_success["writer"]["pending_turns"] == 0

    revision = workspace_revision(root)
    secret = "private prompt text api_key=sk-must-not-be-stored"
    failure = SimpleNamespace(
        reason=secret,
        retryable=True,
        reason_code=MemoryWriteFailureCode.MODEL_TRANSPORT,
    )
    monkeypatch.setattr(
        "openprogram.memory.get_backend",
        lambda: type("UnavailableProvider", (), {
            "write": lambda self, **_kwargs: failure,
        })(),
    )
    dispatcher._memory_write("written")

    result = inspect.status(root)
    assert result["writer"]["last_success_at"] == stamp
    assert (
        result["writer"]["last_failure"]["reason_code"]
        == MemoryWriteFailureCode.MODEL_TRANSPORT
    )
    assert result["writer"]["last_failure"]["retryable"] is True
    status_path = store.state_dir() / "writer-status.json"
    serialized = status_path.read_text(encoding="utf-8")
    assert secret not in serialized
    assert "sk-must-not-be-stored" not in serialized
    assert list(status_path.parent.glob("writer-status.json.*.tmp")) == []
    assert workspace_revision(root) == revision


def test_failure_before_agent_creation_is_reported_by_per_turn_path(
    environment, monkeypatch,
):
    from openprogram.agent import dispatcher
    from openprogram.memory import store
    from openprogram.memory import writing
    from openprogram.memory.local_backend import LocalMemoryBackend
    from openprogram.memory.retrieval import inspect

    db, root = environment
    private_turn = "private conversation body " + "x" * 17_000
    _append(db, "agent-error", "u1", content=private_turn)
    monkeypatch.setattr(writing, "_counter", lambda: len)

    def fail_before_agent():
        raise RuntimeError(
            "prompt=" + private_turn + " credential=sk-private-credential"
        )

    monkeypatch.setattr(writing, "_agent", lambda *_a, **_kw: fail_before_agent())
    monkeypatch.setattr(
        "openprogram.memory.get_backend", lambda: LocalMemoryBackend(),
    )

    dispatcher._memory_write("agent-error")

    writer = inspect.status(root)["writer"]
    assert writer["last_failure"] == {
        "at": writer["last_failure"]["at"],
        "reason_code": MemoryWriteFailureCode.WRITER_FAILURE_UNKNOWN,
        "retryable": False,
    }
    serialized = (store.state_dir() / "writer-status.json").read_text(
        encoding="utf-8"
    )
    assert private_turn not in serialized
    assert "sk-private-credential" not in serialized
    assert writer["pending_turns"] == 1
    assert db.get_branch("agent-error")[0].get(writing.WRITTEN_NODE_MARKER) is None


def test_idle_watcher_records_retryable_failure(environment, monkeypatch):
    from openprogram.memory.session_watcher import _process_session
    from openprogram.memory.retrieval import inspect

    db, root = environment
    _append(db, "idle", "u1")
    left = SimpleNamespace(
        reason="model temporarily unavailable", retryable=True,
        reason_code=None,
    )
    monkeypatch.setattr(
        "openprogram.memory.get_backend",
        lambda: type("IdleProvider", (), {
            "write": lambda self, *args, **kwargs: left,
        })(),
    )

    assert _process_session("idle", db.get_branch("idle")) is left
    failure = inspect.status(root)["writer"]["last_failure"]
    assert failure["reason_code"] == MemoryWriteFailureCode.WRITER_FAILURE_UNKNOWN
    assert failure["retryable"] is True


@pytest.mark.parametrize("entrypoint", ["per-turn", "idle"])
def test_status_root_failure_never_escapes_memory_hooks(
    environment, monkeypatch, entrypoint,
):
    from openprogram.agent import dispatcher
    from openprogram.memory import store
    from openprogram.memory.session_watcher import _process_session

    db, _root = environment
    _append(db, "status-io", "u1")
    left = SimpleNamespace(
        reason="details stay outside status",
        retryable=False,
        reason_code=MemoryWriteFailureCode.MODEL_TRANSPORT,
    )
    monkeypatch.setattr(
        "openprogram.memory.get_backend",
        lambda: type("FailingProvider", (), {
            "write": lambda self, *args, **kwargs: left,
        })(),
    )

    def inaccessible_root():
        raise OSError("state directory is read-only")

    monkeypatch.setattr(store, "root", inaccessible_root)
    if entrypoint == "per-turn":
        assert dispatcher._memory_write("status-io") is None
    else:
        assert _process_session("status-io", db.get_branch("status-io")) is left


def test_status_store_failure_never_changes_per_turn_return(
    environment, monkeypatch,
):
    from openprogram.agent import dispatcher
    from openprogram.memory.runtime import writer_status

    _db, _root = environment
    left = SimpleNamespace(
        reason="details stay outside status",
        retryable=True,
        reason_code=MemoryWriteFailureCode.MODEL_TRANSPORT,
    )
    monkeypatch.setattr(
        "openprogram.memory.get_backend",
        lambda: type("FailingProvider", (), {
            "write": lambda self, **kwargs: left,
        })(),
    )
    monkeypatch.setattr(
        writer_status._WriterStatusStore,
        "record_failure",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("status down")),
    )

    assert dispatcher._memory_write("status-store-error") is None


def test_concurrent_success_and_failure_preserve_both_fields(
    environment, monkeypatch,
):
    from openprogram.memory.runtime import writer_status

    _db, root = environment
    original_load = writer_status._WriterStatusStore.load

    def slow_load(self):
        payload = original_load(self)
        time.sleep(0.05)
        return payload

    monkeypatch.setattr(writer_status._WriterStatusStore, "load", slow_load)
    start = threading.Barrier(3)
    threads = [
        threading.Thread(
            target=lambda: (
                start.wait(), writer_status.record_success(root)
            ),
        ),
        threading.Thread(
            target=lambda: (
                start.wait(), writer_status.record_failure(
                    root, MemoryWriteFailureCode.MODEL_TRANSPORT, retryable=True,
                )
            ),
        ),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    result = writer_status._WriterStatusStore(root).load()
    assert result["last_success_at"] is not None
    assert result["last_failure"] is not None
    assert (
        result["last_failure"]["reason_code"]
        == MemoryWriteFailureCode.MODEL_TRANSPORT
    )
    assert result["last_failure"]["retryable"] is True


def test_memory_status_tool_exposes_the_same_writer_contract(environment):
    from openprogram.programs.functions.vanilla.memory import memory as memory_tools

    _db, _root = environment
    result = json.loads(memory_tools.memory_status())
    assert result["writer"] == {
        "last_outcome": None,
        "last_success_at": None,
        "last_failure": None,
        "pending_turns": 0,
    }


def test_cli_memory_status_exposes_the_same_writer_contract(
    environment, monkeypatch, capsys,
):
    from openprogram import cli

    _db, _root = environment
    monkeypatch.setattr(sys, "argv", ["openprogram", "memory", "status"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    result = json.loads(output[output.index("{"):])
    assert result["writer"] == {
        "last_outcome": None,
        "last_success_at": None,
        "last_failure": None,
        "pending_turns": 0,
    }


def test_persisted_status_carries_a_schema_version(environment):
    from openprogram.memory.runtime import writer_status

    _db, root = environment
    writer_status.record_failure(root, MemoryWriteFailureCode.MODEL_TRANSPORT, retryable=True)
    path = writer_status.runtime_dir(root) / writer_status.STATUS_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == writer_status.STATUS_SCHEMA_VERSION
    assert payload["last_outcome"] == "failure"

    # A file from another schema version is not half-interpreted.
    path.write_text(
        json.dumps({**payload, "version": 999}), encoding="utf-8",
    )
    assert writer_status._WriterStatusStore(root).load() == {
        "last_outcome": None,
        "last_success_at": None,
        "last_failure": None,
    }


def test_last_outcome_orders_two_writes_inside_one_timestamp(
    environment, monkeypatch,
):
    from openprogram.memory.runtime import writer_status

    _db, root = environment
    monkeypatch.setattr(
        writer_status, "_now", lambda: "2026-08-11T00:00:00+00:00",
    )
    writer_status.record_failure(root, MemoryWriteFailureCode.MODEL_TRANSPORT, retryable=True)
    writer_status.record_success(root)
    assert writer_status._WriterStatusStore(root).load()["last_outcome"] == (
        "success"
    )
    writer_status.record_failure(root, "COMMIT_REJECTED", retryable=False)
    stored = writer_status._WriterStatusStore(root).load()
    assert stored["last_outcome"] == "failure"
    assert stored["last_success_at"] == "2026-08-11T00:00:00+00:00"
