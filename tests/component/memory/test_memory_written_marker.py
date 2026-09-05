"""Node-level memory write markers and branch-local ingestion."""
from __future__ import annotations

import atexit
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


from openprogram.memory.writing import (
    WRITTEN_NODE_MARKER as MARKER,
)
LARGE_OUTPUT = "large result line\n" * 4_000


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
    from openprogram.agent.authority import local_owner_authority

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


def _v2_frame(source_id: str, lines: list[str]) -> str:
    anchor = "source-" + hashlib.sha256(source_id.encode()).hexdigest()[:16]
    return (
        f'<a id="{anchor}"></a>\n'
        f"<!-- source-id:{source_id} -->\n"
        f"<!-- record-lines:{len(lines)} -->\n"
        + "\n".join(lines)
        + "\n\n"
    )


def _write_v2_archive(path: Path, *frames: str, tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!-- openprogram-source-archive:v2 -->\n\n"
        + "".join(frames)
        + tail,
        encoding="utf-8",
        newline="\n",
    )


def _stub_writer(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    from openprogram.memory import writing

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
    from openprogram.memory import writing

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
    from openprogram.memory import store
    from openprogram.memory import writing

    db, _memory = environment
    _append(db, "foreign", "m1", None)
    db.merge_node_metadata("foreign", "m1", {MARKER: "w-deadbeef"})

    assert store.workspace_id() != "w-deadbeef"
    assert [
        record.message_id
        for record in writing._pending("foreign", db.get_branch("foreign"))
    ] == ["m1"]


def test_records_refuse_to_invent_an_id_for_a_source_message():
    from openprogram.memory import writing

    with pytest.raises(ValueError, match="stable.*id"):
        writing._records("missing-id", [{
            "role": "user", "content": "remember this", "timestamp": 1.0,
        }])


@pytest.mark.parametrize("outcome", ["raised", "rejected", "unchanged"])
def test_failed_rejected_and_unchanged_batches_mark_nothing(
    tmp_path: Path, outcome: str,
):
    from openprogram.memory.management.transaction import (
        TransactionError,
    )
    from openprogram.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.memory.runtime.state import SourceRecord

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
    from openprogram.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.memory.runtime.state import SourceRecord

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
        archive = memory / "sources" / "openprogram" / "_v2" / "s1.md"
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
    from openprogram.memory import writing

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
    from openprogram.memory import writing

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


def test_legacy_archive_migration_marks_only_its_first_header_once(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory.runtime.state import RuntimeStateStore

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
        '<a id="source-4f0500bc7d19020d"></a>\n'
        "<!-- source-id:openprogram/legacy/m1 -->\n"
        "[2026-01-01] user: one\n\n"
        '<a id="source-b8c11e0f575d4e52"></a>\n'
        "<!-- source-id:openprogram/legacy/m2 -->\n"
        "[2026-01-01] assistant: two\n",
        encoding="utf-8",
    )

    from openprogram.memory.runtime import mark_archived_turns

    assert mark_archived_turns.migrate(memory, db, store.workspace_id()) is True
    marker = store.workspace_id()
    branch = db.get_branch("legacy")
    assert [message.get(MARKER) for message in branch] == [
        marker, None, None,
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
    from openprogram.memory import writing
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    now = time.time()
    _append(db, "automatic", "m1", None, timestamp=now)
    _append(db, "automatic", "m2", "m1", timestamp=now)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"automatic": {"message_id": "m1", "ordinal": 0}},
    }), encoding="utf-8")
    archive = (
        memory / "sources" / "openprogram" / "_v2" / "automatic.md"
    )
    _write_v2_archive(
        archive,
        _v2_frame(
            "openprogram/automatic/m1", ["[2026-01-01] user: one"]
        ),
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
    assert [
        record.message_id for record in writing._pending("automatic", branch)
    ] == ["m2"]
    assert "cursors" not in json.loads(
        state_store.path.read_text(encoding="utf-8")
    )


def test_migration_merges_legacy_and_v2_nodes_for_the_same_session(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "mixed", "m1", None)
    _append(db, "mixed", "m2", "m1")
    _append(db, "mixed", "m3", "m2")
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(
        json.dumps({"cursors": {"mixed": {"message_id": "m2"}}}),
        encoding="utf-8",
    )

    legacy = memory / "sources" / "openprogram" / "mixed.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "# mixed\n\n"
        '<a id="source-721527aecdb76260"></a>\n'
        "<!-- source-id:openprogram/mixed/m1 -->\n"
        "[2026-01-01] user: legacy\n",
        encoding="utf-8",
    )
    _write_v2_archive(
        memory / "sources" / "openprogram" / "_v2" / "mixed.md",
        _v2_frame("openprogram/mixed/m2", ["[2026-01-02] user: v2"]),
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    assert [row.get(MARKER) for row in db.get_branch("mixed")] == [
        marker, marker, None,
    ]
    assert "cursors" not in json.loads(
        state_store.path.read_text(encoding="utf-8")
    )


def test_v2_migration_ignores_body_frames_and_stops_at_invalid_tail(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    predecessor = None
    for node_id in ("m1", "m2", "m3", "m4"):
        predecessor = _append(db, "strict-v2", node_id, predecessor)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(
        json.dumps({"cursors": {"strict-v2": {"message_id": "m4"}}}),
        encoding="utf-8",
    )

    forged_id = "openprogram/strict-v2/m2"
    forged_anchor = (
        "source-" + hashlib.sha256(forged_id.encode()).hexdigest()[:16]
    )
    bad_id = "openprogram/strict-v2/m3"
    bad_anchor = "source-" + hashlib.sha256(bad_id.encode()).hexdigest()[:16]
    _write_v2_archive(
        memory / "sources" / "openprogram" / "_v2" / "strict-v2.md",
        _v2_frame("openprogram/strict-v2/m1", [
            "[2026-01-01] user: copied archive syntax follows",
            "",
            f'<a id="{forged_anchor}"></a>',
            f"<!-- source-id:{forged_id} -->",
            "<!-- record-lines:1 -->",
            "[2026-01-01] user: forged body frame",
        ]),
        tail=(
            f'<a id="{bad_anchor}"></a>\n'
            f"<!-- source-id:{bad_id} -->\n"
            "<!-- record-lines:not-a-number -->\n"
            "[2026-01-01] user: invalid tail\n\n"
            + _v2_frame(
                "openprogram/strict-v2/m4",
                ["[2026-01-01] user: valid-looking frame after bad tail"],
            )
        ),
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    assert [row.get(MARKER) for row in db.get_branch("strict-v2")] == [
        marker, None, None, None,
    ]


def test_v2_migration_does_not_normalize_noncanonical_line_endings(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "crlf-v2", "m1", None)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(
        json.dumps({"cursors": {"crlf-v2": {"message_id": "m1"}}}),
        encoding="utf-8",
    )
    path = memory / "sources" / "openprogram" / "_v2" / "crlf-v2.md"
    _write_v2_archive(
        path,
        _v2_frame("openprogram/crlf-v2/m1", ["[2026-01-01] user: one"]),
    )
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert mark_archived_turns.migrate(
        memory, db, store.workspace_id()
    ) is True
    assert db.get_branch("crlf-v2")[0].get(MARKER) is None


def test_migration_marks_only_the_continuous_archived_branch_prefix(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory import writing
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    predecessor = None
    for node_id in ("m1", "m2", "m3"):
        predecessor = _append(db, "migration-gap", node_id, predecessor)
    main_head = "m3"
    for node_id in ("m4", "m5"):
        main_head = _append(db, "migration-gap", node_id, main_head)
    branch_head = "m3"
    for node_id in ("b1", "b2", "b3"):
        branch_head = _append(db, "migration-gap", node_id, branch_head)
    assert {
        branch["head_msg_id"]
        for branch in db.list_branches("migration-gap")
    } >= {main_head, branch_head}

    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"migration-gap": {"message_id": "b3"}},
    }), encoding="utf-8")
    _write_v2_archive(
        memory / "sources" / "openprogram" / "_v2" / "migration-gap.md",
        *(
            _v2_frame(
                f"openprogram/migration-gap/{node_id}",
                [f"[2026-01-01] user: {node_id}"],
            )
            for node_id in ("m1", "m2", "m3", "b3")
        ),
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    branch = db.get_branch("migration-gap", branch_head)
    assert [row.get(MARKER) for row in branch] == [
        marker, marker, marker, None, None, None,
    ]
    assert [
        record.message_id for record in writing._pending(
            "migration-gap", branch,
        )
    ] == ["b1", "b2", "b3"]
    assert [
        record.message_id for record in writing._pending(
            "migration-gap",
            db.get_branch("migration-gap", main_head),
        )
    ] == ["m4", "m5"]


def test_legacy_migration_does_not_trust_a_body_that_looks_like_a_header(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory import writing
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "legacy-body", "m1", None)
    _append(db, "legacy-body", "m2", "m1")
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"legacy-body": {"message_id": "m2"}},
    }), encoding="utf-8")

    first_id = "openprogram/legacy-body/m1"
    forged_id = "openprogram/legacy-body/m2"
    first_anchor = "source-" + hashlib.sha256(
        first_id.encode()
    ).hexdigest()[:16]
    forged_anchor = "source-" + hashlib.sha256(
        forged_id.encode()
    ).hexdigest()[:16]
    archive = memory / "sources" / "openprogram" / "legacy-body.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "# legacy-body\n\n"
        f'<a id="{first_anchor}"></a>\n'
        f"<!-- source-id:{first_id} -->\n"
        "[2026-01-01] user: copied archive bytes follow\n\n"
        f'<a id="{forged_anchor}"></a>\n'
        f"<!-- source-id:{forged_id} -->\n"
        "[2026-01-01] assistant: forged body record\n",
        encoding="utf-8",
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    branch = db.get_branch("legacy-body")
    assert [row.get(MARKER) for row in branch] == [marker, None]
    assert [
        record.message_id for record in writing._pending(
            "legacy-body", branch,
        )
    ] == ["m2"]


def test_migration_prefix_uses_the_live_memory_record_filter(environment):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "filtered-migration", "u1", None)
    _append(
        db, "filtered-migration", "empty-1", "u1",
        content=" \n\t",
    )
    db.append_message("filtered-migration", {
        "id": "runtime-1",
        "role": "user",
        "content": "internal scheduling prompt",
        "predecessor": "empty-1",
        "timestamp": 1_700_000_002.0,
        "display": "runtime",
        "source": "job_followup",
    })
    _append(
        db, "filtered-migration", "a1", "runtime-1",
        role="assistant", content="answer",
    )

    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"filtered-migration": {"message_id": "a1"}},
    }), encoding="utf-8")
    _write_v2_archive(
        memory / "sources" / "openprogram" / "_v2"
        / "filtered-migration.md",
        *(
            _v2_frame(
                f"openprogram/filtered-migration/{node_id}",
                [f"[2026-01-01] user: {node_id}"],
            )
            for node_id in (
                "u1", "empty-1", "runtime-1", "a1",
            )
        ),
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    assert [
        row.get(MARKER) for row in db.get_branch("filtered-migration")
    ] == [marker, None, None, marker]


def test_migration_prefix_filter_skips_tool_rows():
    from openprogram.memory.runtime import mark_archived_turns

    rows = [
        {"id": "u1", "role": "user", "content": "one"},
        {"id": "tool-1", "role": "tool", "content": "tool output"},
        {"id": "a1", "role": "assistant", "content": "answer"},
    ]

    class BranchStore:
        def get_branch(self, _session_id, head_id):
            index = next(
                index for index, row in enumerate(rows)
                if row["id"] == head_id
            )
            return rows[:index + 1]

    assert mark_archived_turns._continuous_archived_prefixes(
        BranchStore(), {"filtered": {"u1", "tool-1", "a1"}},
    ) == {"filtered": {"u1", "a1"}}


def test_migration_keeps_cursors_when_marker_writes_fail(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "a-successful-migration", "m1", None)
    _append(db, "z-failed-migration", "m1", None)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {
            "a-successful-migration": {"message_id": "m1"},
            "z-failed-migration": {"message_id": "m1"},
        },
    }), encoding="utf-8")
    for session_id in ("a-successful-migration", "z-failed-migration"):
        _write_v2_archive(
            memory / "sources" / "openprogram" / "_v2"
            / f"{session_id}.md",
            _v2_frame(
                f"openprogram/{session_id}/m1",
                ["[2026-01-01] user: one"],
            ),
        )

    merge = db.merge_node_metadata_batch

    def fail(session_id, patches):
        if session_id == "z-failed-migration":
            raise RuntimeError("marker write failed")
        merge(session_id, patches)

    monkeypatch.setattr(db, "merge_node_metadata_batch", fail)
    with pytest.raises(RuntimeError, match="marker write failed"):
        mark_archived_turns.migrate(memory, db, store.workspace_id())

    payload = json.loads(state_store.path.read_text(encoding="utf-8"))
    assert "cursors" in payload
    assert db.get_branch("a-successful-migration")[0].get(MARKER) == (
        store.workspace_id()
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


def test_batch_metadata_merge_rebuilds_each_store_once_after_the_batch(
    environment,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import store

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
    original_writer_rebuild = writer_index.rebuild_from_paths
    original_reader_rebuild = reader_index.rebuild_from_paths
    original_mark_synced = writer_git.mark_synced

    def count_writer_rebuild(*args, **kwargs):
        nonlocal writer_rebuilds
        writer_rebuilds += 1
        return original_writer_rebuild(*args, **kwargs)

    def count_reader_rebuild(*args, **kwargs):
        nonlocal reader_rebuilds
        reader_rebuilds += 1
        return original_reader_rebuild(*args, **kwargs)

    def count_mark_synced():
        nonlocal mark_synced_calls
        mark_synced_calls += 1
        return original_mark_synced()

    monkeypatch.setattr(writer_index, "rebuild_from_paths", count_writer_rebuild)
    monkeypatch.setattr(reader_index, "rebuild_from_paths", count_reader_rebuild)
    monkeypatch.setattr(writer_git, "mark_synced", count_mark_synced)
    meta_path = writer.root_path / "batch-api" / "meta.json"
    updated_at_before = json.loads(
        meta_path.read_text(encoding="utf-8")
    )["updated_at"]
    registry_before = writer.list_sessions(limit=10)[0]["updated_at"]
    workspace_id = store.workspace_id()

    writer.merge_node_metadata_batch("batch-api", {
        f"m{index}": {MARKER: workspace_id, "added": index}
        for index in range(1, 5)
    })

    assert writer_rebuilds == 0
    assert reader_rebuilds == 0
    assert mark_synced_calls == 0
    assert json.loads(meta_path.read_text(encoding="utf-8"))["updated_at"] == (
        updated_at_before
    )
    assert writer.list_sessions(limit=10)[0]["updated_at"] == registry_before

    written = writer.get_branch("batch-api")
    assert writer_rebuilds == 1
    assert writer.get_branch("batch-api") == written
    assert writer_rebuilds == 1
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
    from openprogram.memory import store
    from openprogram.memory import writing

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
    original = index.rebuild_from_paths

    def count_rebuild(*args, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(index, "rebuild_from_paths", count_rebuild)

    assert writing.write_session(
        "batch-write", db.get_branch("batch-write"),
        token_threshold=100, force=True,
    ) is True
    assert rebuilds == 0

    marker = store.workspace_id()
    assert [row.get(MARKER) for row in db.get_branch("batch-write")] == [
        marker, marker, marker, marker,
    ]
    assert rebuilds == 1


@pytest.mark.parametrize(
    ("update_fields", "expected_output", "expect_status", "expect_spill"),
    [
        ({"output": "final output"}, "final output", False, False),
        ({"metadata": {"status": "completed"}}, "placeholder", True, False),
        (
            {"output": "final output", "metadata": {"status": "completed"}},
            "final output", True, False,
        ),
        (
            {"output": LARGE_OUTPUT, "metadata": {"status": "completed"}},
            LARGE_OUTPUT, True, True,
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
    expect_spill: bool,
):
    from openprogram.agent.session_db import SessionDB
    from openprogram.store.session.session_node_writer import SessionNodeWriter

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
    if expect_spill:
        stamp = nodes["pointer"].get("spilled")
        assert stamp is not None
        assert Path(stamp["path"]).read_text(encoding="utf-8") == LARGE_OUTPUT
    else:
        assert nodes["pointer"].get("spilled") is None


def test_migration_ignores_body_comments_and_wrong_archive_sessions(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory import writing
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "attacker", "a1", None)
    _append(db, "victim", "v1", None)
    _append(db, "victim", "v2", "v1")
    _append(db, "other", "o1", None)
    _append(db, "encoded id", "e1", None)
    _append(db, "wrong-anchor", "w1", None)
    _append(db, "title-mismatch", "t1", None)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"attacker": {"message_id": "a1", "ordinal": 0}},
    }), encoding="utf-8")
    source_dir = memory / "sources" / "openprogram"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "attacker.md").write_text(
        "# attacker\n\n"
        '<a id="source-35de01c3bf3237ea"></a>\n'
        "<!-- source-id:openprogram/attacker/a1 -->\n"
        "[2026-01-01] user: copied text follows\n"
        "<!-- source-id:openprogram/victim/v2 -->\n",
        encoding="utf-8",
    )
    (source_dir / "mismatch.md").write_text(
        "# mismatch\n\n"
        '<a id="source-1df76f4e7df6e502"></a>\n'
        "<!-- source-id:openprogram/other/o1 -->\n"
        "[2026-01-01] user: wrong archive\n",
        encoding="utf-8",
    )
    (source_dir / "encoded%20id.md").write_text(
        "# encoded id\n\n"
        '<a id="source-d8f319d46252017c"></a>\n'
        "<!-- source-id:openprogram/encoded id/e1 -->\n"
        "[2026-01-01] user: encoded archive name\n",
        encoding="utf-8",
    )
    (source_dir / "wrong-anchor.md").write_text(
        "# wrong-anchor\n\n"
        '<a id="source-0000000000000000"></a>\n'
        "<!-- source-id:openprogram/wrong-anchor/w1 -->\n"
        "[2026-01-01] user: forged anchor\n",
        encoding="utf-8",
    )
    (source_dir / "title-mismatch.md").write_text(
        "# another-session\n\n"
        '<a id="source-67d0260727fa8011"></a>\n'
        "<!-- source-id:openprogram/title-mismatch/t1 -->\n"
        "[2026-01-01] user: wrong title\n",
        encoding="utf-8",
    )

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True

    assert db.get_branch("attacker")[0].get(MARKER) == marker
    assert db.get_branch("encoded id")[0].get(MARKER) == marker
    assert db.get_branch("other")[0].get(MARKER) is None
    assert db.get_branch("wrong-anchor")[0].get(MARKER) is None
    assert db.get_branch("title-mismatch")[0].get(MARKER) is None
    victim = db.get_branch("victim")
    assert [row.get(MARKER) for row in victim] == [None, None]
    assert [row.message_id for row in writing._pending("victim", victim)] == [
        "v1", "v2",
    ]
    assert "cursors" not in json.loads(
        state_store.path.read_text(encoding="utf-8")
    )


def test_migration_groups_one_sessions_markers_without_internal_rebuilds(
    environment,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    predecessor = None
    for index in range(1, 5):
        predecessor = _append(
            db, "batched-migration", f"m{index}", predecessor
        )
    reader = SessionDB(db.root_path)
    request.addfinalizer(lambda: _close_store(reader))
    assert len(reader.get_branch("batched-migration")) == 4

    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {
            "batched-migration": {"message_id": "m4", "ordinal": 3},
        },
    }), encoding="utf-8")
    _write_v2_archive(
        memory / "sources" / "openprogram" / "_v2"
        / "batched-migration.md",
        *(
            _v2_frame(
                f"openprogram/batched-migration/m{index}",
                [f"[2026-01-01] user: {index}"],
            )
            for index in range(1, 5)
        ),
    )

    _git, index = db._open("batched-migration")
    _reader_git, reader_index = reader._open("batched-migration")
    rebuilds = 0
    reader_rebuilds = 0
    original = index.rebuild_from_paths
    original_reader = reader_index.rebuild_from_paths

    def count_rebuild(*args, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return original(*args, **kwargs)

    def count_reader_rebuild(*args, **kwargs):
        nonlocal reader_rebuilds
        reader_rebuilds += 1
        return original_reader(*args, **kwargs)

    monkeypatch.setattr(index, "rebuild_from_paths", count_rebuild)
    monkeypatch.setattr(reader_index, "rebuild_from_paths", count_reader_rebuild)

    marker = store.workspace_id()
    assert mark_archived_turns.migrate(memory, db, marker) is True
    assert rebuilds == 0
    assert reader_rebuilds == 0

    assert [row.get(MARKER) for row in db.get_branch("batched-migration")] == [
        marker, marker, marker, marker,
    ]
    assert rebuilds == 1
    assert [
        row.get(MARKER) for row in reader.get_branch("batched-migration")
    ] == [marker, marker, marker, marker]
    assert reader_rebuilds == 1


def test_migration_without_an_archive_removes_cursor_and_marks_nothing(
    environment,
):
    from openprogram.memory import store
    from openprogram.memory.runtime import mark_archived_turns
    from openprogram.memory.runtime.state import RuntimeStateStore

    db, memory = environment
    _append(db, "no-archive", "m1", None)
    state_store = RuntimeStateStore(memory)
    state_store.path.parent.mkdir(parents=True, exist_ok=True)
    state_store.path.write_text(json.dumps({
        "cursors": {"no-archive": {"message_id": "m1", "ordinal": 0}},
    }), encoding="utf-8")

    assert mark_archived_turns.migrate(
        memory, db, store.workspace_id()
    ) is True
    assert db.get_branch("no-archive")[0].get(MARKER) is None
    assert "cursors" not in json.loads(
        state_store.path.read_text(encoding="utf-8")
    )


def test_force_processes_head_first_and_skips_a_short_abandoned_branch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

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


def test_force_uses_explicit_current_head_and_writes_its_short_final_batch(
    environment, monkeypatch: pytest.MonkeyPatch,
):
    from openprogram.memory import writing

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
    assert "openprogram/force-order/abandoned" not in sent
    for index in range(1, 6):
        assert f"openprogram/force-order/current-{index}" in sent
    assert "openprogram/force-order/current-5" in calls[-1]
    assert writing._pending(
        "force-order", db.get_branch("force-order", current)
    ) == []
    assert [
        record.message_id for record in writing._pending(
            "force-order", db.get_branch("force-order", abandoned)
        )
    ] == ["abandoned"]


def test_runtime_state_loads_a_clean_default_from_malformed_json(tmp_path: Path):
    from openprogram.memory.runtime.state import RuntimeStateStore

    store = RuntimeStateStore(tmp_path / "memory")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not-json", encoding="utf-8")

    state = store.load()
    assert state.creation_order == {}
    assert state.local_batches == 0
    assert not hasattr(state, "cursors")
