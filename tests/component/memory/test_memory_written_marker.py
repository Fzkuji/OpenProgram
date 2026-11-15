"""Node-level memory write markers and branch-local ingestion."""
from __future__ import annotations

from openprogram.state.store.session.session_log import SessionLog
import atexit
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


from openprogram.state.memory.writing import (
    WRITTEN_NODE_MARKER as MARKER,
)
LARGE_OUTPUT = "large result line\n" * 4_000


def _close_store(store) -> None:
    """Flush and release the real SessionStore used by a test."""
    store._flush_index()
    atexit.unregister(store._flush_index)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.memory import store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    db = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
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
    from openprogram.runtime.permissions.authority import local_owner_authority

    message = {
        "id": node_id,
        "role": role,
        "content": content if content is not None else node_id,
        "timestamp": timestamp,
        **local_owner_authority(),
    }
    if predecessor is not None:
        message["predecessor"] = predecessor
    db.append_message(session_id, message)
    return node_id


def _audit(path: str = "topics/note.md") -> list[dict]:
    return [{"tool": "commit", "status": "ok", "topic_paths": [path]}]


def _stub_writer(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    from openprogram.state.memory import writing

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
    from openprogram.state.memory import store

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
    from openprogram.state.memory import store
    from openprogram.state.memory import writing

    db, _memory = environment
    session_id = "forked"
    predecessor = None
    for node_id in ("m1", "m2", "m3"):
        predecessor = _append(db, session_id, node_id, predecessor)

    short_head = "m3"
    for index in range(1, 3):
        short_head = _append(db, session_id, f"short-{index}", short_head)
    long_head = "m3"
    for index in range(1, 9):
        long_head = _append(db, session_id, f"long-{index}", long_head)

    db.merge_node_metadata(session_id, "m3", {MARKER: store.workspace_id()})

    short = writing._pending(session_id, db.get_branch(session_id, short_head))
    long = writing._pending(session_id, db.get_branch(session_id, long_head))
    assert [record.message_id for record in short] == [
        "short-1", "short-2",
    ]
    assert [record.message_id for record in long] == [
        f"long-{index}" for index in range(1, 9)
    ]


def test_a_marker_from_another_workspace_is_ignored(environment):
    from openprogram.state.memory import store
    from openprogram.state.memory import writing

    db, _memory = environment
    _append(db, "foreign", "m1", None)
    db.merge_node_metadata("foreign", "m1", {MARKER: "w-deadbeef"})

    assert store.workspace_id() != "w-deadbeef"
    assert [
        record.message_id
        for record in writing._pending("foreign", db.get_branch("foreign"))
    ] == ["m1"]


def test_records_refuse_to_invent_an_id_for_a_source_message():
    from openprogram.state.memory import writing

    with pytest.raises(ValueError, match="stable.*id"):
        writing._records("missing-id", [{
            "role": "user", "content": "remember this", "timestamp": 1.0,
        }])


@pytest.mark.parametrize("outcome", ["raised", "rejected", "unhandled"])
def test_failed_rejected_and_unhandled_batches_mark_nothing(
    tmp_path: Path, outcome: str,
):
    from openprogram.state.memory.management.transaction import (
        TransactionError,
    )
    from openprogram.state.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.state.memory.runtime.state import ChatTurn

    record = ChatTurn(
        session_id="s1", message_id="m1", ordinal=0, role="user",
        content="remember this",
    )
    marked: list[str] = []

    def writer(workspace, batch):
        if outcome == "raised":
            raise RuntimeError("writer failed")
        if outcome == "rejected":
            raise TransactionError("COMMIT_REJECTED", "edit rejected")
        return None

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


def test_a_batch_the_writer_found_nothing_in_is_marked(tmp_path: Path):
    from openprogram.state.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.state.memory.runtime.state import ChatTurn

    record = ChatTurn(
        session_id="s1", message_id="m1", ordinal=0, role="user",
        content="remember this",
    )
    marked: list[str] = []
    assert OnlineMemoryRuntime(tmp_path / "memory", token_counter=len).process(
        [record], lambda workspace, batch: [],
        mark=lambda batch: marked.extend(item.message_id for item in batch),
        force=True,
    ) is True
    assert marked == ["m1"]


@pytest.mark.parametrize("audit, declined", [
    ([{"tool": "Read", "status": "ok"}, {"tool": "agent", "status": "ok"}], True),
    # A Read of a missing file is not a failed edit.
    ([{"tool": "Read", "status": "error"}, {"tool": "agent", "status": "ok"}], True),
    ([{"tool": "Edit", "status": "error"}, {"tool": "agent", "status": "ok"}], False),
    ([{"tool": "commit", "status": "error"}, {"tool": "agent", "status": "ok"}], False),
    ([{"tool": "Write", "status": "ok"}], False),
])
def test_only_a_clean_finished_run_counts_as_nothing_to_record(audit, declined):
    from openprogram.state.memory import writing

    assert writing._declined(audit) is declined


def test_process_writes_then_marks(tmp_path: Path):
    from openprogram.state.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.state.memory.runtime.state import ChatTurn

    memory = tmp_path / "memory"
    record = ChatTurn(
        session_id="s1", message_id="m1", ordinal=0, role="user",
        content="remember this",
    )
    events: list[str] = []

    def writer(workspace, batch):
        # Nothing is copied out of the chat before the writer reads it.
        assert not (memory / "sources").exists()
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
    from openprogram.state.memory import store
    from openprogram.state.memory import writing

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
    from openprogram.state.memory import writing

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
        assert sent.count(f"/s/shared#{node_id}") == 1
    for node_id in ("head-0", "head-1", "sibling-0", "sibling-1", "sibling-2"):
        assert sent.count(f"/s/shared#{node_id}") == 1


def test_merge_marker_preserves_updated_at_and_refreshes_one_stale_index(
    environment,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.memory import store

    writer, _memory = environment
    _append(writer, "visible", "m1", None)
    reader = SessionDB(writer.root_path)
    request.addfinalizer(lambda: _close_store(reader))
    assert reader.get_branch("visible")[0].get(MARKER) is None
    _git, reader_index = reader._open("visible")
    rebuilds = 0
    original = reader_index.rebuild

    def count_rebuild(*args, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reader_index, "rebuild", count_rebuild)
    meta_log = SessionLog(writer.root_path / "default" / "chats" / "visible")
    before = meta_log.read_meta()["updated_at"]
    registry_before = writer.list_sessions(limit=10)[0]["updated_at"]

    writer.merge_node_metadata(
        "visible", "m1", {MARKER: store.workspace_id()}
    )

    assert meta_log.read_meta()["updated_at"] == before
    assert writer.get_session("visible")["updated_at"] == before
    assert writer.list_sessions(limit=10)[0]["updated_at"] == registry_before
    assert reader.get_branch("visible")[0][MARKER] == store.workspace_id()
    assert rebuilds == 1
    assert reader.get_branch("visible")[0][MARKER] == store.workspace_id()
    assert rebuilds == 1


def test_batch_metadata_merge_rebuilds_each_store_once_after_the_batch(
    environment,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.memory import store

    writer, _memory = environment
    predecessor = None
    for index in range(1, 5):
        node_id = f"m{index}"
        message = {
            "id": node_id,
            "role": "user",
            "content": f"content-{index}",
            "timestamp": 1_700_000_000.0 + index,
            "keep": f"original-{index}",
        }
        if predecessor is not None:
            message["predecessor"] = predecessor
        writer.append_message("batch-api", message)
        predecessor = node_id

    reader = SessionDB(writer.root_path)
    request.addfinalizer(lambda: _close_store(reader))
    assert len(reader.get_branch("batch-api")) == 4

    writer_git, writer_index = writer._open("batch-api")
    _reader_git, reader_index = reader._open("batch-api")
    writer_rebuilds = 0
    reader_rebuilds = 0
    mark_synced_calls = 0
    original_writer_rebuild = writer_index.rebuild
    original_reader_rebuild = reader_index.rebuild
    original_mark_synced = writer_git.mark_synced

    def count_writer_rebuild(*args, **kwargs):
        nonlocal writer_rebuilds
        writer_rebuilds += 1
        return original_writer_rebuild(*args, **kwargs)

    def count_reader_rebuild(*args, **kwargs):
        nonlocal reader_rebuilds
        reader_rebuilds += 1
        return original_reader_rebuild(*args, **kwargs)

    def count_mark_synced(*args, **kwargs):
        nonlocal mark_synced_calls
        mark_synced_calls += 1
        return original_mark_synced(*args, **kwargs)

    monkeypatch.setattr(writer_index, "rebuild", count_writer_rebuild)
    monkeypatch.setattr(reader_index, "rebuild", count_reader_rebuild)
    monkeypatch.setattr(writer_git, "mark_synced", count_mark_synced)
    meta_log = SessionLog(writer.root_path / "default" / "chats" / "batch-api")
    updated_at_before = meta_log.read_meta()["updated_at"]
    registry_before = writer.list_sessions(limit=10)[0]["updated_at"]
    workspace_id = store.workspace_id()

    writer.merge_node_metadata_batch("batch-api", {
        f"m{index}": {MARKER: workspace_id, "added": index}
        for index in range(1, 5)
    })

    assert writer_rebuilds == 0
    assert reader_rebuilds == 0
    assert meta_log.read_meta()["updated_at"] == (
        updated_at_before
    )
    assert writer.list_sessions(limit=10)[0]["updated_at"] == registry_before

    written = writer.get_branch("batch-api")
    assert writer_rebuilds == 0
    assert writer.get_branch("batch-api") == written
    assert writer_rebuilds == 0
    assert [row[MARKER] for row in written] == [workspace_id] * 4
    assert [row["keep"] for row in written] == [
        f"original-{index}" for index in range(1, 5)
    ]
    assert [row["added"] for row in written] == [1, 2, 3, 4]

    external = reader.get_branch("batch-api")
    assert reader_rebuilds == 1
    assert reader.get_branch("batch-api") == external
    assert reader_rebuilds == 1
    assert [row[MARKER] for row in external] == [workspace_id] * 4


def test_write_session_marks_a_batch_without_internal_index_rebuilds(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.state.memory import store
    from openprogram.state.memory import writing

    db, _memory = environment
    calls: list[str] = []
    _stub_writer(monkeypatch, calls)
    predecessor = None
    for index in range(4):
        predecessor = _append(
            db, "batch-write", f"m{index}", predecessor, content="x"
        )

    _git, index = db._open("batch-write")
    rebuilds = 0
    original = index.rebuild

    def count_rebuild(*args, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(index, "rebuild", count_rebuild)

    assert writing.write_session(
        "batch-write", db.get_branch("batch-write"),
        token_threshold=100, force=True,
    ) is True
    assert rebuilds == 0

    marker = store.workspace_id()
    assert [row.get(MARKER) for row in db.get_branch("batch-write")] == [
        marker, marker, marker, marker,
    ]
    assert rebuilds == 0


@pytest.mark.parametrize(
    ("update_fields", "expected_output", "expect_status"),
    [
        ({"output": "final output"}, "final output", False),
        ({"metadata": {"status": "completed"}}, "placeholder", True),
        (
            {"output": "final output", "metadata": {"status": "completed"}},
            "final output", True,
        ),
        (
            {"output": LARGE_OUTPUT, "metadata": {"status": "completed"}},
            LARGE_OUTPUT, True,
        ),
    ],
    ids=["output-only", "metadata-only", "output-and-metadata", "large-output"],
)
def test_graphstore_update_keeps_fields_across_an_interleaved_external_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    update_fields: dict,
    expected_output: str,
    expect_status: bool,
):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter

    root = tmp_path / "sessions"
    writer = SessionDB(root)
    other = SessionDB(root)
    request.addfinalizer(lambda: _close_store(writer))
    request.addfinalizer(lambda: _close_store(other))
    writer.append_message("interleaved", {
        "id": "pointer",
        "role": "assistant",
        "content": "placeholder",
        "timestamp": 1.0,
        "existing": "preserved",
    })
    assert other.get_branch("interleaved")[-1]["id"] == "pointer"

    original_open = writer._open
    appended = False

    def open_then_append(session_id, *args, **kwargs):
        nonlocal appended
        pair = original_open(session_id, *args, **kwargs)
        if session_id == "interleaved" and not appended:
            appended = True
            other.append_message("interleaved", {
                "id": "unrelated",
                "role": "user",
                "content": "concurrent append",
                "predecessor": "pointer",
                "timestamp": 2.0,
            })
        return pair

    monkeypatch.setattr(writer, "_open", open_then_append)
    SessionNodeWriter(writer, "interleaved").update("pointer", **update_fields)

    reader = SessionDB(root)
    request.addfinalizer(lambda: _close_store(reader))
    nodes = {row["id"]: row for row in reader.get_messages("interleaved")}
    assert set(nodes) == {"pointer", "unrelated"}
    assert nodes["pointer"]["content"] == expected_output
    assert nodes["pointer"]["existing"] == "preserved"
    if expect_status:
        assert nodes["pointer"]["status"] == "completed"
    # An assistant reply is never spilled at write time, however long: it
    # is kept whole and sent whole.
    assert nodes["pointer"].get("spilled") is None


def test_force_processes_head_first_and_skips_a_short_abandoned_branch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.state.memory import writing

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

    assert "/s/force#current" in calls[0]
    sent = "\n".join(calls)
    assert "/s/force#long" in sent
    assert "/s/force#short" not in sent
    assert db.get_branch("force", long_abandoned)[-1]["id"] == "long"
    assert db.get_branch("force", short_abandoned)[-1]["id"] == "short"


def test_force_uses_explicit_current_head_and_writes_its_short_final_batch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.state.memory import writing

    db, _memory = environment
    calls: list[str] = []
    _stub_writer(monkeypatch, calls)
    root = _append(
        db, "force-order", "root", None, content="r" * 10, timestamp=1.0
    )
    current = root
    for index, content in enumerate(("a" * 6, "b" * 6, "c" * 6,
                                     "d" * 6, "tail"), start=1):
        current = _append(
            db, "force-order", f"current-{index}", current,
            content=content, timestamp=1.0 + index,
        )
    abandoned = _append(
        db, "force-order", "abandoned", root,
        content="s", timestamp=100.0,
    )
    db.set_head("force-order", current)

    tips = db.list_branches("force-order")
    assert [tip["head_msg_id"] for tip in tips[:2]] == [abandoned, current]
    assert db.get_session("force-order")["head_id"] == current

    assert writing.write(
        "force-order", token_threshold=10, force=True
    ) is None

    sent = "\n".join(calls)
    assert "/s/force-order#abandoned" not in sent
    for index in range(1, 6):
        assert f"/s/force-order#current-{index}" in sent
    assert "/s/force-order#current-5" in calls[-1]
    assert writing._pending(
        "force-order", db.get_branch("force-order", current)
    ) == []
    assert [
        record.message_id for record in writing._pending(
            "force-order", db.get_branch("force-order", abandoned)
        )
    ] == ["abandoned"]


def test_runtime_state_loads_a_clean_default_from_malformed_json(tmp_path: Path):
    from openprogram.state.memory.runtime.state import RuntimeStateStore

    store = RuntimeStateStore(tmp_path / "memory")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not-json", encoding="utf-8")

    state = store.load()
    assert state.creation_order == {}
    assert state.local_batches == 0
    assert not hasattr(state, "cursors")
