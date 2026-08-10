"""Node-level Scriptorium write markers and branch-local ingestion."""
from __future__ import annotations

import atexit
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MARKER = "memory_written_scriptorium"


def _close_store(store) -> None:
    """Flush and release the real SessionStore used by a test."""
    store._flush_index()
    atexit.unregister(store._flush_index)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openprogram.paths as paths
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    db = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    try:
        yield db, store.ensure()
    finally:
        _close_store(db)


def _append(
    db,
    session_id: str,
    node_id: str,
    predecessor: str | None,
    *,
    role: str = "user",
    content: str | None = None,
    timestamp: float = 1_700_000_000.0,
) -> str:
    message = {
        "id": node_id,
        "role": role,
        "content": content if content is not None else node_id,
        "timestamp": timestamp,
    }
    if predecessor is not None:
        message["predecessor"] = predecessor
    db.append_message(session_id, message)
    return node_id


def _audit(path: str = "topics/note.md") -> list[dict]:
    return [{"tool": "commit", "status": "ok", "topic_paths": [path]}]


def _stub_writer(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    from openprogram.memory.scriptorium import writing

    def run(memory_dir, *, agent, task, stage=None, **kwargs):
        calls.append(task)
        return _audit()

    monkeypatch.setattr(writing, "_counter", lambda: len)
    monkeypatch.setattr(writing, "_agent", lambda model=None: object())
    monkeypatch.setattr(writing, "_run_agent", run)
    monkeypatch.setattr(writing, "organize_topics", lambda *args, **kwargs: [])


def test_workspace_id_is_one_durable_value_under_concurrent_first_use(
    environment,
):
    from openprogram.memory import store

    _db, _memory = environment
    barrier = threading.Barrier(8)

    def read_id() -> str:
        barrier.wait()
        return store.workspace_id()

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: read_id(), range(8)))

    assert len(set(values)) == 1
    assert re.fullmatch(r"w-[0-9a-f]{8}", values[0])
    assert store.workspace_id() == values[0]
    assert values[0] in [
        path.read_text(encoding="utf-8").strip()
        for path in store.state_dir().iterdir()
        if path.is_file()
    ]


def test_fork_suffixes_after_a_marked_m3_are_returned_in_full(environment):
    from openprogram.memory import store
    from openprogram.memory.scriptorium import writing

    db, _memory = environment
    session_id = "forked"
    predecessor = None
    for node_id in ("m1", "m2", "m3"):
        predecessor = _append(db, session_id, node_id, predecessor)

    short_head = "m3"
    for index in range(1, 6):
        short_head = _append(db, session_id, f"short-{index}", short_head)
    long_head = "m3"
    for index in range(1, 12):
        long_head = _append(db, session_id, f"long-{index}", long_head)

    db.merge_node_metadata(session_id, "m3", {MARKER: store.workspace_id()})

    short = writing._pending(session_id, db.get_branch(session_id, short_head))
    long = writing._pending(session_id, db.get_branch(session_id, long_head))
    assert [record.message_id for record in short] == [
        f"short-{index}" for index in range(1, 6)
    ]
    assert [record.message_id for record in long] == [
        f"long-{index}" for index in range(1, 12)
    ]


def test_a_marker_from_another_workspace_is_ignored(environment):
    from openprogram.memory import store
    from openprogram.memory.scriptorium import writing

    db, _memory = environment
    _append(db, "foreign", "m1", None)
    db.merge_node_metadata("foreign", "m1", {MARKER: "w-deadbeef"})

    assert store.workspace_id() != "w-deadbeef"
    assert [
        record.message_id
        for record in writing._pending("foreign", db.get_branch("foreign"))
    ] == ["m1"]


def test_records_refuse_to_invent_an_id_for_a_source_message():
    from openprogram.memory.scriptorium import writing

    with pytest.raises(ValueError, match="stable.*id"):
        writing._records("missing-id", [{
            "role": "user", "content": "remember this", "timestamp": 1.0,
        }])


@pytest.mark.parametrize("outcome", ["raised", "rejected", "unchanged"])
def test_failed_rejected_and_unchanged_batches_mark_nothing(
    tmp_path: Path, outcome: str,
):
    from openprogram.memory.scriptorium.management.transaction import (
        TransactionError,
    )
    from openprogram.memory.scriptorium.runtime.online import OnlineMemoryRuntime
    from openprogram.memory.scriptorium.runtime.state import SourceRecord

    record = SourceRecord(
        provider="openprogram",
        thread_id="s1",
        message_id="m1",
        ordinal=0,
        role="user",
        content="remember this",
    )
    marked: list[str] = []

    def writer(workspace, batch):
        if outcome == "raised":
            raise RuntimeError("writer failed")
        if outcome == "rejected":
            raise TransactionError("COMMIT_REJECTED", "edit rejected")
        return []

    runtime = OnlineMemoryRuntime(tmp_path / "memory", token_counter=len)
    if outcome == "raised":
        with pytest.raises(RuntimeError):
            runtime.process(
                [record], writer, mark=lambda batch: marked.extend(
                    item.message_id for item in batch
                ), force=True,
            )
    elif outcome == "rejected":
        with pytest.raises(TransactionError):
            runtime.process(
                [record], writer, mark=lambda batch: marked.extend(
                    item.message_id for item in batch
                ), force=True,
            )
    else:
        assert runtime.process(
            [record], writer, mark=lambda batch: marked.extend(
                item.message_id for item in batch
            ), force=True,
        ) is False

    assert marked == []


def test_process_archives_then_writes_then_marks(tmp_path: Path):
    from openprogram.memory.scriptorium.runtime.online import OnlineMemoryRuntime
    from openprogram.memory.scriptorium.runtime.state import SourceRecord

    memory = tmp_path / "memory"
    record = SourceRecord(
        provider="openprogram",
        thread_id="s1",
        message_id="m1",
        ordinal=0,
        role="user",
        content="remember this",
    )
    events: list[str] = []

    def writer(workspace, batch):
        archive = memory / "sources" / "openprogram" / "s1.md"
        assert "source-id:openprogram/s1/m1" in archive.read_text(
            encoding="utf-8"
        )
        events.append("write")
        return ["topics/note.md"]

    def mark(batch):
        assert events == ["write"]
        events.append("mark")

    assert OnlineMemoryRuntime(memory, token_counter=len).process(
        [record], writer, mark=mark, force=True,
    ) is True
    assert events == ["write", "mark"]


def test_write_session_marks_only_the_selected_oldest_batch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import store
    from openprogram.memory.scriptorium import writing

    db, _memory = environment
    calls: list[str] = []
    _stub_writer(monkeypatch, calls)
    predecessor = None
    for index in range(4):
        predecessor = _append(
            db, "batched", f"m{index}", predecessor, content="xxxx"
        )

    assert writing.write_session(
        "batched", db.get_branch("batched"), token_threshold=8, force=True
    ) is True

    marker = store.workspace_id()
    branch = db.get_branch("batched")
    assert [message.get(MARKER) == marker for message in branch] == [
        True, True, False, False,
    ]
    assert len(calls) == 1


def test_shared_prefix_is_written_once_across_live_branches(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory.scriptorium import writing

    db, _memory = environment
    calls: list[str] = []
    _stub_writer(monkeypatch, calls)
    predecessor = None
    for node_id in ("shared-1", "shared-2", "shared-3"):
        predecessor = _append(db, "shared", node_id, predecessor)

    head = "shared-3"
    for index in range(2):
        head = _append(db, "shared", f"head-{index}", head)
    sibling = "shared-3"
    for index in range(3):
        sibling = _append(db, "shared", f"sibling-{index}", sibling)
    db.set_head("shared", head)

    assert writing.write(
        "shared", token_threshold=1, force=True
    ) is None

    sent = "\n".join(calls)
    for node_id in ("shared-1", "shared-2", "shared-3"):
        assert sent.count(f"openprogram/shared/{node_id}") == 1
    for node_id in ("head-0", "head-1", "sibling-0", "sibling-1", "sibling-2"):
        assert sent.count(f"openprogram/shared/{node_id}") == 1


def test_legacy_archive_migration_marks_exact_nodes_once(environment):
    from openprogram.memory import store
    from openprogram.memory.scriptorium.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "legacy", "m1", None)
    _append(db, "legacy", "m2", "m1")
    _append(db, "legacy", "m3", "m2")
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"legacy": {"message_id": "m2", "ordinal": 1}},
        "local_tokens": 17,
    }), encoding="utf-8")
    archive = memory / "sources" / "openprogram" / "legacy.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "# legacy\n\n"
        "<!-- source-id:openprogram/legacy/m1 -->\n"
        "[2026-01-01] user: one\n\n"
        "<!-- source-id:openprogram/legacy/m2 -->\n"
        "[2026-01-01] assistant: two\n",
        encoding="utf-8",
    )

    from openprogram.memory.scriptorium.runtime import mark_archived_turns

    assert mark_archived_turns.migrate(memory, db, store.workspace_id()) is True
    marker = store.workspace_id()
    branch = db.get_branch("legacy")
    assert [message.get(MARKER) for message in branch] == [
        marker, marker, None,
    ]
    payload = json.loads(state_store.path.read_text(encoding="utf-8"))
    assert "cursors" not in payload
    assert payload["local_tokens"] == 17

    history_before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (db.root_path / "legacy" / "history").glob("*.json")
    }
    assert mark_archived_turns.migrate(memory, db, marker) is False
    history_after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (db.root_path / "legacy" / "history").glob("*.json")
    }
    assert history_after == history_before


def test_normal_write_migrates_archived_markers_before_pending(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import store
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.runtime.state import RuntimeStateStore

    db, memory = environment
    now = time.time()
    _append(db, "automatic", "m1", None, timestamp=now)
    _append(db, "automatic", "m2", "m1", timestamp=now)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"automatic": {"message_id": "m1", "ordinal": 0}},
    }), encoding="utf-8")
    archive = memory / "sources" / "openprogram" / "automatic.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "<!-- source-id:openprogram/automatic/m1 -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(writing, "_counter", lambda: len)
    monkeypatch.setattr(writing, "_agent", lambda model=None: object())
    monkeypatch.setattr(
        writing, "_run_agent",
        lambda *args, **kwargs: pytest.fail("below-threshold pending must not write"),
    )

    assert writing.write(
        "automatic", token_threshold=10_000, force=False,
    ) is None

    marker = store.workspace_id()
    branch = db.get_branch("automatic")
    assert [message.get(MARKER) for message in branch] == [marker, None]
    assert "cursors" not in json.loads(
        state_store.path.read_text(encoding="utf-8")
    )


def test_merge_marker_preserves_updated_at_and_refreshes_one_stale_index(
    environment,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import store

    writer, _memory = environment
    _append(writer, "visible", "m1", None)
    reader = SessionDB(writer.root_path)
    request.addfinalizer(lambda: _close_store(reader))
    assert reader.get_branch("visible")[0].get(MARKER) is None
    _git, reader_index = reader._open("visible")
    rebuilds = 0
    original = reader_index.rebuild_from_paths

    def count_rebuild(*args, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reader_index, "rebuild_from_paths", count_rebuild)
    meta_path = writer.root_path / "visible" / "meta.json"
    before = json.loads(meta_path.read_text(encoding="utf-8"))["updated_at"]
    registry_before = writer.list_sessions(limit=10)[0]["updated_at"]

    writer.merge_node_metadata(
        "visible", "m1", {MARKER: store.workspace_id()}
    )

    assert json.loads(meta_path.read_text(encoding="utf-8"))["updated_at"] == before
    assert writer.get_session("visible")["updated_at"] == before
    assert writer.list_sessions(limit=10)[0]["updated_at"] == registry_before
    assert reader.get_branch("visible")[0][MARKER] == store.workspace_id()
    assert rebuilds == 1
    assert reader.get_branch("visible")[0][MARKER] == store.workspace_id()
    assert rebuilds == 1


def test_force_processes_head_first_and_skips_a_short_abandoned_branch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory.scriptorium import writing

    db, _memory = environment
    calls: list[str] = []
    _stub_writer(monkeypatch, calls)
    _append(db, "force", "root", None, content="root")
    current = _append(db, "force", "current", "root", content="h")
    long_abandoned = _append(
        db, "force", "long", "root", content="x" * 20,
        timestamp=1.0,
    )
    short_abandoned = _append(
        db, "force", "short", "root", content="s", timestamp=1.0,
    )
    db.set_head("force", current)

    assert writing.write("force", token_threshold=10, force=True) is None

    assert "openprogram/force/current" in calls[0]
    sent = "\n".join(calls)
    assert "openprogram/force/long" in sent
    assert "openprogram/force/short" not in sent
    assert db.get_branch("force", long_abandoned)[-1]["id"] == "long"
    assert db.get_branch("force", short_abandoned)[-1]["id"] == "short"


def test_runtime_state_loads_a_clean_default_from_malformed_json(tmp_path: Path):
    from openprogram.memory.scriptorium.runtime.state import RuntimeStateStore

    store = RuntimeStateStore(tmp_path / "memory")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not-json", encoding="utf-8")

    state = store.load()
    assert state.creation_order == {}
    assert state.local_batches == 0
    assert not hasattr(state, "cursors")
