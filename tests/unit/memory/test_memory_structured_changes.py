"""Structured memory changes share one atomic transaction contract."""

from __future__ import annotations

from contextlib import closing
import json
import os

import pytest

SOURCE = '# Conversation 1\n\n<a id="d1-1"></a>\n\nuser: remember this\n'
NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
SECOND = (
    "# Second\n"
    "\n"
    "A removable fact.[^e-2f4c7a2b91] ^def45678\n"
    "\n"
    "[^e-2f4c7a2b91]: Time: `2026-01-02`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
LINKING = (
    "# Other\n"
    "\n"
    "See [the note](note.md#^abc12345).[^e-3f4c7a2b92] ^fed87654\n"
    "\n"
    "[^e-3f4c7a2b92]: Time: `2026-01-03`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
MERGED = (
    "# Merged\n"
    "\n"
    "Merged fact.[^e-4f4c7a2b93] ^abc12345 ^def45678\n"
    "\n"
    "[^e-4f4c7a2b93]: Time: `2026-01-04`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
HTML_NOTE = (
    "# Note\n"
    "\n"
    "Fact\n"
    "<em>detail</em>[^e-5f4c7a2b94] ^abc12345\n"
    "\n"
    "[^e-5f4c7a2b94]: Time: `2026-01-05`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
LEGACY_NOTE = (
    "# Legacy\n"
    "\n"
    "First legacy fact.[^mem_first] Second legacy fact.[^mem_second]\n"
    "\n"
    "[^mem_first]: Time: `2026-01-06`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
    "[^mem_second]: Time: `2026-01-07`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)


def _root(tmp_path):
    root = tmp_path / "memory"
    (root / "sources").mkdir(parents=True)
    (root / "topics").mkdir()
    (root / "sources/D1.md").write_text(SOURCE, encoding="utf-8")
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    (root / "topics/second.md").write_text(SECOND, encoding="utf-8")
    return root


def test_structured_changes_rewrite_and_delete_in_one_transaction(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        result = workspace.update(
            base_revision=workspace.revision(),
            changes=[
                {
                    "path": "topics/note.md",
                    "action": "write",
                    "content": NOTE.replace("worth keeping", "worth remembering"),
                },
                {"path": "topics/second.md", "action": "delete"},
            ],
            git_commit="off",
        )

    assert "worth remembering" in (root / "topics/note.md").read_text()
    assert not (root / "topics/second.md").exists()
    assert set(result.changed_files) >= {
        "topics/note.md",
        "topics/second.md",
    }


def test_invalid_change_rolls_back_every_change(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    before_note = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        before_revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=before_revision,
                changes=[
                    {
                        "path": "topics/note.md",
                        "action": "write",
                        "content": NOTE.replace("worth keeping", "changed"),
                    },
                    {"path": "../outside.md", "action": "delete"},
                ],
                git_commit="off",
            )
        assert workspace.revision() == before_revision

    assert caught.value.code == "PATH_OUTSIDE_WORKSPACE"
    assert (root / "topics/note.md").read_bytes() == before_note


def test_delete_is_atomic_when_another_topic_links_to_removed_block(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        before_revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=before_revision,
                changes=[{"path": "topics/note.md", "action": "delete"}],
                git_commit="off",
            )

    assert caught.value.code == "INVALID_TOPIC_FORMAT"
    assert "abc12345" in caught.value.message
    assert (root / "topics/note.md").read_text(encoding="utf-8") == NOTE


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [{"path": "topics/note.md", "action": "rename"}],
        [
            {"path": "topics/note.md", "action": "delete"},
            {"path": "topics/note.md", "action": "delete"},
        ],
    ],
)
def test_structured_changes_reject_invalid_shapes(tmp_path, changes):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=workspace.revision(),
                changes=changes,
                git_commit="off",
            )
    assert caught.value.code == "INVALID_ARGUMENT"


def test_structured_changes_reject_normalized_duplicate_paths(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        with pytest.raises(TransactionError, match="duplicate change path"):
            workspace.update(
                base_revision=workspace.revision(),
                changes=[
                    {"path": "topics/note.md", "action": "delete"},
                    {"path": "topics/./note.md", "action": "delete"},
                ],
                git_commit="off",
            )


def test_patch_and_changes_are_mutually_exclusive(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        with pytest.raises(TransactionError, match="exactly one"):
            workspace.update(
                base_revision=workspace.revision(),
                patch="not empty",
                changes=[{"path": "topics/second.md", "action": "delete"}],
                git_commit="off",
            )


def test_legacy_patch_can_delete_memory(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            patch=(
                "--- a/topics/second.md\n"
                "+++ /dev/null\n"
                "@@ -1,5 +0,0 @@\n"
                "-# Second\n"
            ),
            git_commit="off",
        )
    assert not (root / "topics/second.md").exists()


def test_record_changes_cannot_be_mixed_with_file_changes(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        with pytest.raises(TransactionError, match="exactly one"):
            workspace.update(
                base_revision=workspace.revision(),
                changes=[{"path": "topics/second.md", "action": "delete"}],
                memory_changes=[{"op": "delete", "memory_id": "def45678"}],
                git_commit="off",
            )


def test_memory_agent_commit_can_delete_current_memory(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").unlink()
        workspace.commit_edits(*baseline)

    assert not (root / "topics/second.md").exists()


def test_direct_file_edit_rebuilds_derived_views(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        path = workspace.stage_dir / "topics/note.md"
        path.write_text(
            NOTE.replace("worth keeping", "changed directly"),
            encoding="utf-8",
        )
        workspace.commit_edits(*baseline)

    recent = [
        json.loads(line)
        for line in (root / "recent_events.jsonl").read_text().splitlines()
    ]
    assert recent[0]["memory_id"] == "abc12345"
    assert recent[0]["content"] == "A fact changed directly."
    assert (root / "timeline/2026/01/01.md").is_file()
    assert (root / "relations.json").is_file()


def test_direct_file_edit_prunes_a_topic_with_no_remaining_records(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/note.md").write_text(
            "# Note\n", encoding="utf-8"
        )
        workspace.commit_edits(*baseline)

    assert not (root / "topics/note.md").exists()


def test_first_direct_file_reorder_preserves_recent_creation_order(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    before = {
        unit.memory_id: unit.created_order
        for unit in parse_topic_tree(root / "topics")
    }
    reordered = (
        "# Reordered\n\n"
        + SECOND.split("\n\n", 1)[1].strip()
        + "\n\n"
        + NOTE.split("\n\n", 1)[1].strip()
        + "\n"
    )
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/note.md").unlink()
        (workspace.stage_dir / "topics/second.md").unlink()
        (workspace.stage_dir / "topics/reordered.md").write_text(
            reordered, encoding="utf-8"
        )
        workspace.commit_edits(*baseline)

    after = {
        row["memory_id"]: row["created_order"]
        for row in map(
            json.loads,
            (root / "recent_events.jsonl").read_text().splitlines(),
        )
    }
    assert after == before


def test_record_changes_create_update_delete_and_rebuild_views(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        result = workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[
                {
                    "op": "update",
                    "memory_id": "abc12345",
                    "content": "The corrected fact.",
                    "time": "2026-02-01",
                    "source_refs": ["D1:1"],
                },
                {"op": "delete", "memory_id": "def45678"},
                {
                    "op": "create",
                    "topic_path": "topics/api.md",
                    "headings": ["API"],
                    "content": "A record created through the API.",
                    "time": "2026-02-02",
                    "source_refs": ["D1:1"],
                },
            ],
            git_commit="off",
        )

    units = parse_topic_tree(root / "topics")
    by_id = {unit.memory_id: unit for unit in units}
    assert by_id["abc12345"].content == "The corrected fact."
    assert by_id["abc12345"].when == "2026-02-01"
    assert "def45678" not in by_id
    created = [unit for unit in units if unit.topic_path == "api.md"]
    assert len(created) == 1
    assert created[0].content == "A record created through the API."
    assert created[0].memory_id in result.block_ids.values()
    assert "[D1:1](../sources/D1.md#d1-1)" in (
        root / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert "[D1:1](../sources/D1.md#d1-1)" in (
        root / "topics/api.md"
    ).read_text(encoding="utf-8")
    recent = [
        json.loads(line)
        for line in (root / "recent_events.jsonl").read_text().splitlines()
    ]
    assert {row["memory_id"] for row in recent} == {
        "abc12345",
        created[0].memory_id,
    }
    assert (root / "timeline/2026/02/01.md").is_file()
    assert (root / "timeline/2026/02/02.md").is_file()


def test_record_sources_preserve_agent_labels_and_v2_identity(
    tmp_path, monkeypatch,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree
    from openprogram.memory.markdown import syntax
    from openprogram.memory.runtime.state import SourceRecord

    root = tmp_path / "memory"
    records = [
        SourceRecord(
            "openprogram", "thread-1", "m1", 1, "user", "scope evidence"
        ),
        SourceRecord(
            "openprogram", "thread-1", "m2", 2, "assistant", "method evidence"
        ),
        SourceRecord(
            "openprogram", "thread-1", "m3", 3, "assistant", "result evidence"
        ),
    ]
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records(records)
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "create_record",
                "content": "A labelled memory record.",
                "time": "2026-08-20",
                "sources": [
                    {
                        "source": records[0].source_id,
                        "label": "这是一个超过八个字的标签",
                    },
                    {
                        "source": records[1].source_id,
                        "label": "one two three four five six seven",
                    },
                    {
                        "source": records[2].source_id,
                        "label": records[2].source_id,
                    },
                ],
                "destination": {
                    "topic_path": "topics/labelled.md",
                    "headings": [],
                    "position": "end",
                },
            }],
            git_commit="off",
        )

    topic = (root / "topics/labelled.md").read_text(encoding="utf-8")
    assert "[这是一个超过八个字的标签](" in topic
    assert "[one two three four five six seven](" in topic
    assert f"[{records[2].source_id}](" in topic
    scans = 0
    original_scan = syntax.scan_source_archive

    def counted_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(syntax, "scan_source_archive", counted_scan)
    unit = parse_topic_tree(root / "topics")[0]
    assert scans == 1
    assert unit.source_refs == tuple(record.source_id for record in records)
    assert unit.source_labels == (
        "这是一个超过八个字的标签",
        "one two three four five six seven",
        records[2].source_id,
    )
    from openprogram.memory.retrieval.bm25 import MemoryBM25Index

    indexed = next(
        event
        for event in MemoryBM25Index(root, persist=False).events
        if event.path == "topics/labelled.md"
    )
    assert indexed.refs == list(unit.source_refs)
    assert indexed.trust_state == "trusted"
    timeline = (root / "timeline/2026/08/20.md").read_text(encoding="utf-8")
    assert "[这是一个超过八个字的标签](" in timeline
    assert "[one two three four five six seven](" in timeline
    assert f"[{records[2].source_id}](" in timeline

    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "move_records",
                "memory_ids": [unit.memory_id],
                "destination": {
                    "topic_path": "topics/archive/labelled.md",
                    "headings": ["Archive"],
                    "position": "end",
                },
            }],
            git_commit="off",
        )

    moved = parse_topic_tree(root / "topics")[0]
    assert moved.source_refs == unit.source_refs
    assert moved.source_labels == unit.source_labels
    assert moved.topic_path == "archive/labelled.md"


def test_source_labels_support_encoded_and_legacy_provider_archives(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree
    from openprogram.memory.runtime.state import SourceRecord
    from openprogram.memory.source_format import provider_source_location

    root = tmp_path / "memory"
    encoded = SourceRecord(
        "openprogram", "encoded id", "m1", 1, "user", "encoded source"
    )
    legacy_ref = "openprogram/legacy-thread/m1"
    legacy_path, legacy_anchor = provider_source_location(legacy_ref)
    path = root / legacy_path
    path.parent.mkdir(parents=True)
    path.write_text(
        "# legacy-thread\n\n"
        f'<a id="{legacy_anchor}"></a>\n'
        f"<!-- source-id:{legacy_ref} -->\n"
        "[2026-08-20] user: legacy source\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records([encoded])
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "create_record",
                "content": "Encoded and legacy sources remain addressable.",
                "time": "2026-08-20",
                "sources": [
                    {"source": encoded.source_id, "label": "编码来源"},
                    {"source": legacy_ref, "label": "旧版来源"},
                ],
                "destination": {
                    "topic_path": "topics/compatibility.md",
                    "headings": [],
                    "position": "end",
                },
            }],
            git_commit="off",
        )
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/direct-encoded.md").write_text(
            "# Direct encoded\n\n"
            "A direct Writer record.[^e1]\n\n"
            "[^e1]: Time: `2026-08-20`; Sources: "
            f"[编码来源]({encoded.source_id})\n",
            encoding="utf-8",
        )
        workspace.commit_edits(*baseline)

    units = parse_topic_tree(root / "topics")
    assert {ref for unit in units for ref in unit.source_refs} == {
        encoded.source_id,
        legacy_ref,
    }
    compatibility = (root / "topics/compatibility.md").read_text(
        encoding="utf-8"
    )
    assert "encoded%20id.md#" in compatibility
    assert "legacy-thread.md#" in compatibility


def test_direct_writer_preserves_agent_source_labels(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree
    from openprogram.memory.runtime.state import SourceRecord

    root = tmp_path / "memory"
    record = SourceRecord(
        "openprogram", "thread-1", "m1", 1, "user", "research scope"
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records([record])
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics").mkdir(exist_ok=True)
        (workspace.stage_dir / "topics/direct.md").write_text(
            "# Direct\n\n"
            "A direct writer record.[^e1]\n\n"
            "A direct record with a sequence label.[^e2]\n\n"
            "A direct record with a date label.[^e3]\n\n"
            "A direct record with a URL label.[^e4]\n\n"
            "[^e1]: Time: `2026-08-20`; Sources: "
            f"[Owner 1]({record.source_id})\n"
            "[^e2]: Time: `2026-08-20`; Sources: "
            f"[S1]({record.source_id})\n"
            "[^e3]: Time: `2026-08-20`; Sources: "
            f"[2026-08-20]({record.source_id})\n"
            "[^e4]: Time: `2026-08-20`; Sources: "
            f"[https://example.com/source]({record.source_id})\n",
            encoding="utf-8",
        )
        workspace.commit_edits(*baseline)

    topic = (root / "topics/direct.md").read_text(encoding="utf-8")
    for label in ("Owner 1", "S1", "2026-08-20", "https://example.com/source"):
        assert f"[{label}](../sources/openprogram/_v2/thread-1.md#source-" in topic
    units = parse_topic_tree(root / "topics")
    assert [unit.source_refs for unit in units] == [
        (record.source_id,),
        (record.source_id,),
        (record.source_id,),
        (record.source_id,),
    ]
    assert [unit.source_labels for unit in units] == [
        ("Owner 1",),
        ("S1",),
        ("2026-08-20",),
        ("https://example.com/source",),
    ]


def test_direct_writer_cannot_replace_an_invalid_target_with_its_label(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import TopicFormatError
    from openprogram.memory.runtime.state import SourceRecord

    root = tmp_path / "memory"
    record = SourceRecord(
        "openprogram", "thread-real", "m1", 1, "user", "real source"
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records([record])
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics").mkdir(exist_ok=True)
        (workspace.stage_dir / "topics/forged.md").write_text(
            "Forged source target.[^e1]\n\n"
            "[^e1]: Time: `2026-08-20`; Sources: "
            f"[{record.source_id}](openprogram/thread-missing/m9)\n",
            encoding="utf-8",
        )
        with pytest.raises(TopicFormatError, match="invalid source target"):
            workspace.commit_edits(*baseline)

    assert not (root / "topics/forged.md").exists()


def test_structured_source_label_must_not_be_empty(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError
    from openprogram.memory.runtime.state import SourceRecord

    root = tmp_path / "memory"
    record = SourceRecord(
        "openprogram", "thread-1", "m1", 1, "user", "source text"
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records([record])
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="label is required"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "create_record",
                    "content": "A record with an empty display label.",
                    "time": "2026-08-20",
                    "sources": [{"source": record.source_id, "label": "   "}],
                    "destination": {
                        "topic_path": "topics/empty-label.md",
                        "headings": [],
                        "position": "end",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert not (root / "topics/empty-label.md").exists()


@pytest.mark.skipif(
    os.name == "nt",
    reason="the mocked invocation deliberately exercises POSIX shell commands",
)
def test_restricted_writer_shell_uses_the_committed_source_baseline(
    tmp_path, monkeypatch,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.runtime.state import SourceRecord

    root = tmp_path / "memory"
    record = SourceRecord(
        "openprogram", "thread-1", "m1", 1, "user", "source text"
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.archive_source_records([record])
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "create_record",
                "content": "A movable record.",
                "time": "2026-08-20",
                "sources": [{"source": record.source_id, "label": "move"}],
                "destination": {
                    "topic_path": "topics/original.md",
                    "headings": [],
                    "position": "end",
                },
            }],
            git_commit="off",
        )

    from openprogram.memory.source_format import provider_source_location

    location = provider_source_location(record.source_id, v2=True)
    assert location is not None
    source_path, source_anchor = location
    (root / "topics/legacy.md").write_text(
        "# Legacy\n\nLegacy record.[^mem_old]\n\n"
        "[^mem_old]: Time: `2026-08-20`; Sources: "
        f"[legacy](../{source_path.as_posix()}#{source_anchor})\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "openprogram.memory.management.workspace._sandbox.resolve_policy",
        lambda *, required: object(),
    )
    monkeypatch.setattr(
        "openprogram.backend.local._invocation",
        lambda command, _cwd, **_kwargs: (
            ["/bin/sh", "-c", command], False, None, True,
        ),
    )
    with closing(MemoryWorkspace(
        root, allowed_new_source_refs={record.source_id},
    )) as workspace:
        assert workspace.shell("true").returncode == 0
        moved = workspace.shell(
            "mkdir -p topics/moved && "
            "mv topics/original.md topics/moved/renamed.md && "
            "mv topics/legacy.md topics/moved/legacy.md"
        )
        assert moved.returncode == 0

    assert not (root / "topics/original.md").exists()
    assert (root / "topics/moved/renamed.md").is_file()
    assert not (root / "topics/legacy.md").exists()
    assert (root / "topics/moved/legacy.md").is_file()


def test_invalid_record_change_rolls_back_every_record(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).parts[0].startswith(".")
    }
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=revision,
                memory_changes=[
                    {
                        "op": "update",
                        "memory_id": "abc12345",
                        "content": "Would otherwise change.",
                        "time": "2026-02-01",
                        "source_refs": ["D1:1"],
                    },
                    {"op": "delete", "memory_id": "missing-id"},
                ],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert caught.value.code == "MEMORY_NOT_FOUND"
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).parts[0].startswith(".")
    }
    assert after == before


def test_record_delete_rolls_back_when_a_block_link_would_dangle(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=revision,
                memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
                git_commit="off",
            )

    assert caught.value.code == "INVALID_TOPIC_FORMAT"
    assert "abc12345" in caught.value.message
    assert (root / "topics/note.md").read_text(encoding="utf-8") == NOTE


def test_record_changes_manage_merged_block_aliases(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated merged fact.",
                "time": "2026-02-03",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
            git_commit="off",
        )

    units = parse_topic_tree(root / "topics")
    assert [unit.memory_id for unit in units] == ["def45678"]
    assert units[0].content == "Updated merged fact."
    assert "^abc12345" not in (root / "topics/note.md").read_text()


def test_record_change_limit_counts_headings_before_writing(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import (
        TransactionError,
        TransactionLimits,
    )

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="exceed"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "create",
                    "topic_path": "topics/large.md",
                    "headings": ["H" * 2000],
                    "content": "Small fact.",
                    "time": "2026-02-04",
                    "source_refs": ["D1:1"],
                }],
                limits=TransactionLimits(max_patch_bytes=100),
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert not (root / "topics/large.md").exists()


def test_record_api_locates_the_same_html_paragraph_as_the_topic_parser(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(HTML_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated HTML-backed record.",
                "time": "2026-02-05",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
            git_commit="off",
        )

    assert all(
        unit.memory_id != "abc12345"
        for unit in parse_topic_tree(root / "topics")
    )


def test_two_updates_for_merged_aliases_are_rejected_atomically(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="same merged block"):
            workspace.update(
                base_revision=revision,
                memory_changes=[
                    {
                        "op": "update",
                        "memory_id": "abc12345",
                        "content": "First final value.",
                        "time": "2026-02-06",
                        "source_refs": ["D1:1"],
                    },
                    {
                        "op": "update",
                        "memory_id": "def45678",
                        "content": "Second final value.",
                        "time": "2026-02-06",
                        "source_refs": ["D1:1"],
                    },
                ],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert (root / "topics/note.md").read_bytes() == before


def test_record_api_updates_and_deletes_one_legacy_unit_without_the_other(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-07",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )
        units = {
            unit.memory_id: unit
            for unit in parse_topic_tree(root / "topics")
        }
        assert units["mem_first"].content == "Updated first legacy fact."
        assert units["mem_second"].content == "Second legacy fact."
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{"op": "delete", "memory_id": "mem_first"}],
            git_commit="off",
        )

    units = {
        unit.memory_id: unit
        for unit in parse_topic_tree(root / "topics")
    }
    assert "mem_first" not in units
    assert units["mem_second"].content == "Second legacy fact."


def test_record_delete_rejects_link_to_migrated_legacy_alias(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-08",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    linked = LINKING.replace("note.md#^abc12345", "note.md#^mem_first")
    (root / "topics/other.md").write_text(linked, encoding="utf-8")
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=revision,
                memory_changes=[{"op": "delete", "memory_id": "mem_first"}],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert caught.value.code == "INVALID_TOPIC_FORMAT"
    assert "mem_first" in caught.value.message
    assert (root / "topics/note.md").read_bytes() == before


def test_record_update_does_not_change_an_unrelated_legacy_id(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/legacy.md").write_text(
        "# Legacy\n\nLegacy.[^mem_old]\n\n"
        "[^mem_old]: Time: `2026-01-09`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated modern fact.",
                "time": "2026-02-09",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["mem_old"].content == "Legacy."


def test_record_update_preserves_unassigned_legacy_trailing_prose(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    note = LEGACY_NOTE.replace(
        "Second legacy fact.[^mem_second]",
        "Second legacy fact.[^mem_second] trailing prose",
    )
    (root / "topics/note.md").write_text(note, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-10",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["mem_second"].content == "Second legacy fact."
    assert "trailing prose" in (root / "topics/note.md").read_text()


def test_record_update_and_delete_support_legacy_definition_before_paragraph(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(
        "# Legacy\n\n"
        "[^mem_first]: Time: `2026-01-10`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n\n"
        "Legacy.[^mem_first]\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated legacy.",
                "time": "2026-02-11",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )
        assert {
            unit.memory_id: unit.content
            for unit in parse_topic_tree(root / "topics")
        }["mem_first"] == "Updated legacy."
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{"op": "delete", "memory_id": "mem_first"}],
            git_commit="off",
        )

    assert "mem_first" not in {
        unit.memory_id for unit in parse_topic_tree(root / "topics")
    }


def test_record_update_legacy_ignores_existing_legacy_record_block_id(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    (root / "topics/collision.md").write_text(
        "# Existing\n\nExisting.[^e-existing] ^legacy-record-1\n\n"
        "[^e-existing]: Time: `2026-01-11`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated without a temporary ID.",
                "time": "2026-02-12",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    ids = {unit.memory_id for unit in parse_topic_tree(root / "topics")}
    assert {"mem_first", "mem_second", "legacy-record-1"} <= ids


def test_record_update_can_link_to_a_legacy_memory_id(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/legacy.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "See [legacy](legacy.md#^mem_first).",
                "time": "2026-02-13",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["abc12345"].relation_targets == ("mem_first",)


def test_record_delete_removes_last_legacy_unit_with_unbound_trailing_prose(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    legacy = (
        "# Legacy\n\nLegacy.[^mem_old] trailing prose\n\n"
        "[^mem_old]: Time: `2026-01-12`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/note.md").write_text(legacy, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{"op": "delete", "memory_id": "mem_old"}],
            git_commit="off",
        )

    assert not (root / "topics/note.md").exists()


def test_standard_record_operations_keep_pre_release_aliases_compatible(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Legacy alias input.",
                "time": "2026-08-15",
                "source_refs": ["D1:1"],
            }],
            git_commit="off",
        )

    unit = {
        unit.memory_id: unit for unit in parse_topic_tree(root / "topics")
    }["abc12345"]
    assert unit.content == "Legacy alias input."


def test_create_record_inserts_before_an_anchor_in_an_existing_section(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "create_record",
                "content": "Inserted before the existing record.",
                "time": "2026-08-15",
                "source_refs": ["D1:1"],
                "destination": {
                    "topic_path": "topics/note.md",
                    "headings": ["Note"],
                    "position": "before",
                    "anchor_memory_id": "abc12345",
                },
            }],
            git_commit="off",
        )

    units = [
        unit for unit in parse_topic_tree(root / "topics")
        if unit.topic_path == "note.md"
    ]
    assert [unit.content for unit in units] == [
        "Inserted before the existing record.",
        "A fact worth keeping.",
    ]
    assert units[0].headings == ("Note",)


@pytest.mark.parametrize("missing", ["headings", "position"])
def test_standard_destination_requires_every_schema_field(tmp_path, missing):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    destination = {
        "topic_path": "topics/new.md",
        "headings": [],
        "position": "end",
    }
    destination.pop(missing)
    with closing(MemoryWorkspace(root)) as workspace:
        with pytest.raises(TransactionError, match=missing):
            workspace.update(
                base_revision=workspace.revision(),
                memory_changes=[{
                    "op": "create_record",
                    "content": "Incomplete destination.",
                    "time": "2026-08-15",
                    "source_refs": ["D1:1"],
                    "destination": destination,
                }],
                git_commit="off",
            )


def test_move_records_moves_a_batch_in_declared_order_and_prunes_sources(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    before = {
        unit.memory_id: unit.created_order
        for unit in parse_topic_tree(root / "topics")
    }
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "move_records",
                "memory_ids": ["def45678", "abc12345"],
                "destination": {
                    "topic_path": "topics/archive/grouped.md",
                    "headings": ["Grouped", "Selected"],
                    "position": "end",
                },
            }],
            git_commit="off",
        )

    units = [
        unit for unit in parse_topic_tree(root / "topics")
        if unit.topic_path == "archive/grouped.md"
    ]
    assert [unit.memory_id for unit in units] == ["def45678", "abc12345"]
    assert all(unit.headings == ("Grouped", "Selected") for unit in units)
    assert not (root / "topics/note.md").exists()
    assert not (root / "topics/second.md").exists()
    grouped = (root / "topics/archive/grouped.md").read_text(encoding="utf-8")
    assert "../../sources/D1.md#d1-1" in grouped
    assert "archive/grouped.md#^abc12345" in (
        root / "topics/other.md"
    ).read_text(encoding="utf-8")
    after = {
        row["memory_id"]: row["created_order"]
        for row in map(
            json.loads,
            (root / "recent_events.jsonl").read_text().splitlines(),
        )
    }
    assert after == before


def test_update_and_move_can_target_the_same_record(tmp_path):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[
                {
                    "op": "update_record",
                    "memory_id": "abc12345",
                    "content": "Updated and moved.",
                    "time": "2026-08-15",
                    "source_refs": ["D1:1"],
                },
                {
                    "op": "move_records",
                    "memory_ids": ["abc12345"],
                    "destination": {
                        "topic_path": "topics/destination.md",
                        "headings": ["Destination"],
                        "position": "start",
                    },
                },
            ],
            git_commit="off",
        )

    unit = {
        unit.memory_id: unit for unit in parse_topic_tree(root / "topics")
    }["abc12345"]
    assert unit.topic_path == "destination.md"
    assert unit.content == "Updated and moved."
    assert unit.when == "2026-08-15"


def test_move_records_rejects_part_of_a_shared_physical_block_atomically(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="physical block"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "move_records",
                    "memory_ids": ["abc12345"],
                    "destination": {
                        "topic_path": "topics/destination.md",
                        "headings": ["Destination"],
                        "position": "end",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert (root / "topics/note.md").read_bytes() == before
    assert not (root / "topics/destination.md").exists()


def test_move_records_rejects_reversing_ids_in_a_shared_physical_block(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="physical block order"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "move_records",
                    "memory_ids": ["def45678", "abc12345"],
                    "destination": {
                        "topic_path": "topics/destination.md",
                        "headings": ["Destination"],
                        "position": "end",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision


def test_delete_alias_then_move_the_surviving_record_in_one_transaction(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[
                {"op": "delete_record", "memory_id": "abc12345"},
                {
                    "op": "move_records",
                    "memory_ids": ["def45678"],
                    "destination": {
                        "topic_path": "topics/destination.md",
                        "headings": ["Destination"],
                        "position": "end",
                    },
                },
            ],
            git_commit="off",
        )

    units = parse_topic_tree(root / "topics")
    assert [unit.memory_id for unit in units] == ["def45678"]
    assert units[0].topic_path == "destination.md"


def test_move_records_rejects_a_conflicting_destination_evidence_definition(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    first = (
        "# A\n\nAlpha.[^e-collision] ^aaaaaaaa\n\n"
        "[^e-collision]: Time: `2026-01-01`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    second = (
        "# B\n\nBeta.[^e-collision] ^bbbbbbbb\n\n"
        "[^e-collision]: Time: `2026-02-02`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/a.md").write_text(first, encoding="utf-8")
    (root / "topics/b.md").write_text(second, encoding="utf-8")
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".scriptorium" not in path.parts
    }
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="evidence definition conflicts"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "move_records",
                    "memory_ids": ["aaaaaaaa"],
                    "destination": {
                        "topic_path": "topics/b.md",
                        "headings": ["B"],
                        "position": "end",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".scriptorium" not in path.parts
    }
    assert after == before


def test_move_records_preserves_a_referenced_definition_in_an_empty_section(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    centralized = (
        "# A\n\nOne.[^e-one] ^aaaaaaaa\n\n"
        "# B\n\nTwo.[^e-two] ^bbbbbbbb\n\n"
        "# References\n\n"
        "[^e-one]: Time: `2026-01-01`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
        "[^e-two]: Time: `2026-01-02`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/a.md").write_text(centralized, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "move_records",
                "memory_ids": ["bbbbbbbb"],
                "destination": {
                    "topic_path": "topics/d.md",
                    "headings": ["D"],
                    "position": "end",
                },
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["aaaaaaaa"].topic_path == "a.md"
    assert units["bbbbbbbb"].topic_path == "d.md"
    source = (root / "topics/a.md").read_text(encoding="utf-8")
    assert "[^e-one]:" in source
    assert "# References" not in source


def test_move_records_rejects_conflicting_evidence_across_source_topics(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    first = (
        "# A\n\nAlpha.[^e-collision] ^aaaaaaaa\n\n"
        "[^e-collision]: Time: `2026-01-01`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    second = (
        "# B\n\nBeta.[^e-collision] ^bbbbbbbb\n\n"
        "[^e-collision]: Time: `2026-02-02`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/a.md").write_text(first, encoding="utf-8")
    (root / "topics/b.md").write_text(second, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="conflicts across sources"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "move_records",
                    "memory_ids": ["aaaaaaaa", "bbbbbbbb"],
                    "destination": {
                        "topic_path": "topics/c.md",
                        "headings": ["C"],
                        "position": "end",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert not (root / "topics/c.md").exists()


def test_delete_record_removes_a_topic_when_its_last_record_is_deleted(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    legacy = (
        "# Legacy\n\nLegacy.[^mem_old] trailing prose\n\n"
        "[^mem_old]: Time: `2026-01-12`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/note.md").write_text(legacy, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "delete_record",
                "memory_id": "mem_old",
            }],
            git_commit="off",
        )

    assert not (root / "topics/note.md").exists()


def test_move_records_reorders_records_after_an_anchor_in_the_same_section(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    same_section = (
        "# Note\n\n"
        "First.[^e-first] ^abc12345\n\n"
        "Second.[^e-second] ^def45678\n\n"
        "[^e-first]: Time: `2026-01-01`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
        "[^e-second]: Time: `2026-01-02`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/note.md").write_text(same_section, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "move_records",
                "memory_ids": ["abc12345"],
                "destination": {
                    "topic_path": "topics/note.md",
                    "headings": ["Note"],
                    "position": "after",
                    "anchor_memory_id": "def45678",
                },
            }],
            git_commit="off",
        )

    assert [
        unit.memory_id for unit in parse_topic_tree(root / "topics")
    ] == ["def45678", "abc12345"]


def test_move_records_rejects_an_anchor_outside_the_destination_section(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="destination section"):
            workspace.update(
                base_revision=revision,
                memory_changes=[{
                    "op": "move_records",
                    "memory_ids": ["abc12345"],
                    "destination": {
                        "topic_path": "topics/second.md",
                        "headings": ["Wrong heading"],
                        "position": "before",
                        "anchor_memory_id": "def45678",
                    },
                }],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert (root / "topics/note.md").read_bytes() == before


def test_delete_record_removes_the_stale_derived_core_view(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    core_topic = NOTE.replace("# Note", "# Core").replace(
        "abc12345", "core12345"
    )
    (root / "topics/core.md").write_text(core_topic, encoding="utf-8")
    (root / "core.md").write_text("stale core\n", encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            base_revision=workspace.revision(),
            memory_changes=[{
                "op": "delete_record",
                "memory_id": "core12345",
            }],
            git_commit="off",
        )

    assert not (root / "topics/core.md").exists()
    assert not (root / "core.md").exists()
