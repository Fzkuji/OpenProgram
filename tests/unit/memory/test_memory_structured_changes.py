"""Structured memory changes share one atomic transaction contract."""

from __future__ import annotations

from contextlib import closing
import json

import pytest

NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
)
SECOND = (
    "# Second\n"
    "\n"
    "A removable fact.[^e-2f4c7a2b91] ^def45678\n"
    "\n"
    "[^e-2f4c7a2b91]: Time: `2026-01-02`; Sources: [D1:1](/s/s1#d1-1)\n"
)
LINKING = (
    "# Other\n"
    "\n"
    "See [the note](note.md#^abc12345).[^e-3f4c7a2b92] ^fed87654\n"
    "\n"
    "[^e-3f4c7a2b92]: Time: `2026-01-03`; Sources: [D1:1](/s/s1#d1-1)\n"
)
MERGED = (
    "# Merged\n"
    "\n"
    "Merged fact.[^e-4f4c7a2b93] ^abc12345 ^def45678\n"
    "\n"
    "[^e-4f4c7a2b93]: Time: `2026-01-04`; Sources: [D1:1](/s/s1#d1-1)\n"
)
HTML_NOTE = (
    "# Note\n"
    "\n"
    "Fact\n"
    "<em>detail</em>[^e-5f4c7a2b94] ^abc12345\n"
    "\n"
    "[^e-5f4c7a2b94]: Time: `2026-01-05`; Sources: [D1:1](/s/s1#d1-1)\n"
)
LEGACY_NOTE = (
    "# Legacy\n"
    "\n"
    "First legacy fact.[^mem_first] Second legacy fact.[^mem_second]\n"
    "\n"
    "[^mem_first]: Time: `2026-01-06`; Sources: [D1:1](/s/s1#d1-1)\n"
    "[^mem_second]: Time: `2026-01-07`; Sources: [D1:1](/s/s1#d1-1)\n"
)


@pytest.fixture(autouse=True)
def _conversation(chats):
    """The chat message every record here cites."""
    chats.say("s1", "d1-1", "remember this")


def _root(tmp_path):
    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    (root / "topics/second.md").write_text(SECOND, encoding="utf-8")
    return root


def test_memory_agent_commit_can_delete_current_memory(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").unlink()
        workspace.commit_edits(*baseline)

    assert not (root / "topics/second.md").exists()


def test_direct_file_edit_rebuilds_derived_views(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace

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
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/note.md").write_text(
            "# Note\n", encoding="utf-8"
        )
        workspace.commit_edits(*baseline)

    assert not (root / "topics/note.md").exists()


def test_first_direct_file_reorder_preserves_recent_creation_order(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        result = workspace.update(
            memory_changes=[
                {
                    "op": "update",
                    "memory_id": "abc12345",
                    "content": "The corrected fact.",
                    "time": "2026-02-01",
                    "source_refs": ["/s/s1#d1-1"],
                },
                {"op": "delete", "memory_id": "def45678"},
                {
                    "op": "create",
                    "topic_path": "topics/api.md",
                    "headings": ["API"],
                    "content": "A record created through the API.",
                    "time": "2026-02-02",
                    "source_refs": ["/s/s1#d1-1"],
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
    assert "[chat](/s/s1#d1-1)" in (
        root / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert "[chat](/s/s1#d1-1)" in (
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


def test_record_sources_keep_agent_labels_and_cite_the_chat(tmp_path, chats):
    from openprogram.state.memory.chat_refs import link
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = tmp_path / "memory"
    records = [
        chats.say("thread-1", "m1", "scope evidence"),
        chats.say("thread-1", "m2", "method evidence", role="assistant"),
        chats.say("thread-1", "m3", "result evidence", role="assistant"),
    ]
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "create_record",
                "content": "A labelled memory record.",
                "time": "2026-08-20",
                "sources": [
                    {
                        "source": link(records[0]),
                        "label": "这是一个超过八个字的标签",
                    },
                    {
                        "source": link(records[1]),
                        "label": "one two three four five six seven",
                    },
                    {
                        "source": records[2],
                        "label": "result",
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
    assert "[result](/s/thread-1#m3)" in topic
    unit = parse_topic_tree(root / "topics")[0]
    assert unit.source_refs == tuple(records)
    assert unit.source_labels == (
        "这是一个超过八个字的标签",
        "one two three four five six seven",
        "result",
    )
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    indexed = next(
        event
        for event in MemoryBM25Index(root).events
        if event.path == "topics/labelled.md"
    )
    assert indexed.refs == list(unit.source_refs)
    timeline = (root / "timeline/2026/08/20.md").read_text(encoding="utf-8")
    assert "[这是一个超过八个字的标签](" in timeline
    assert "[one two three four five six seven](" in timeline
    assert "[result](/s/thread-1#m3)" in timeline

    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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


def test_direct_writer_preserves_agent_source_labels(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = tmp_path / "memory"
    ref = chats.say("thread-1", "m1", "research scope")
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics").mkdir(exist_ok=True)
        (workspace.stage_dir / "topics/direct.md").write_text(
            "# Direct\n\n"
            "A direct writer record.[^e1]\n\n"
            "A direct record with a sequence label.[^e2]\n\n"
            "A direct record with a date label.[^e3]\n\n"
            "A direct record with a URL label.[^e4]\n\n"
            "[^e1]: Time: `2026-08-20`; Sources: "
            f"[Owner 1]({ref})\n"
            "[^e2]: Time: `2026-08-20`; Sources: "
            f"[S1]({ref})\n"
            "[^e3]: Time: `2026-08-20`; Sources: "
            f"[2026-08-20]({ref})\n"
            "[^e4]: Time: `2026-08-20`; Sources: "
            f"[https://example.com/source]({ref})\n",
            encoding="utf-8",
        )
        workspace.commit_edits(*baseline)

    topic = (root / "topics/direct.md").read_text(encoding="utf-8")
    for label in ("Owner 1", "S1", "2026-08-20", "https://example.com/source"):
        assert f"[{label}](/s/thread-1#m1)" in topic
    units = parse_topic_tree(root / "topics")
    assert [unit.source_refs for unit in units] == [
        (ref,), (ref,), (ref,), (ref,),
    ]
    assert [unit.source_labels for unit in units] == [
        ("Owner 1",),
        ("S1",),
        ("2026-08-20",),
        ("https://example.com/source",),
    ]


def test_direct_writer_cannot_cite_a_message_no_chat_holds(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace

    root = tmp_path / "memory"
    real = chats.say("thread-real", "m1", "real source")
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics").mkdir(exist_ok=True)
        (workspace.stage_dir / "topics/forged.md").write_text(
            "Forged source target.[^e1]\n\n"
            "[^e1]: Time: `2026-08-20`; Sources: "
            f"[{real}](/s/thread-missing#m9)\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception, match="missing source reference"):
            workspace.commit_edits(*baseline)

    assert not (root / "topics/forged.md").exists()


def test_structured_source_label_must_not_be_empty(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = tmp_path / "memory"
    ref = chats.say("thread-1", "m1", "source text")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="label is required"):
            workspace.update(
                memory_changes=[{
                    "op": "create_record",
                    "content": "A record with an empty display label.",
                    "time": "2026-08-20",
                    "sources": [{"source": ref, "label": "   "}],
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


def test_invalid_record_change_rolls_back_every_record(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

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
                memory_changes=[
                    {
                        "op": "update",
                        "memory_id": "abc12345",
                        "content": "Would otherwise change.",
                        "time": "2026-02-01",
                        "source_refs": ["/s/s1#d1-1"],
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import (
        TransactionError,
        workspace_revision,
    )

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
                git_commit="off",
            )

    assert caught.value.code == "INVALID_TOPIC_FORMAT"
    assert "abc12345" in caught.value.message
    assert (root / "topics/note.md").read_text(encoding="utf-8") == NOTE
    assert workspace_revision(root) == revision


def test_record_changes_manage_merged_block_aliases(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated merged fact.",
                "time": "2026-02-03",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )
        workspace.update(
            memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
            git_commit="off",
        )

    units = parse_topic_tree(root / "topics")
    assert [unit.memory_id for unit in units] == ["def45678"]
    assert units[0].content == "Updated merged fact."
    assert "^abc12345" not in (root / "topics/note.md").read_text()


def test_record_change_limit_counts_headings_before_writing(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import (
        TransactionError,
        TransactionLimits,
    )

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="exceed"):
            workspace.update(
                memory_changes=[{
                    "op": "create",
                    "topic_path": "topics/large.md",
                    "headings": ["H" * 2000],
                    "content": "Small fact.",
                    "time": "2026-02-04",
                    "source_refs": ["/s/s1#d1-1"],
                }],
                limits=TransactionLimits(max_bytes=100),
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert not (root / "topics/large.md").exists()


def test_record_api_locates_the_same_html_paragraph_as_the_topic_parser(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(HTML_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated HTML-backed record.",
                "time": "2026-02-05",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )
        workspace.update(
            memory_changes=[{"op": "delete", "memory_id": "abc12345"}],
            git_commit="off",
        )

    assert all(
        unit.memory_id != "abc12345"
        for unit in parse_topic_tree(root / "topics")
    )


def test_two_updates_for_merged_aliases_are_rejected_atomically(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="same merged block"):
            workspace.update(
                memory_changes=[
                    {
                        "op": "update",
                        "memory_id": "abc12345",
                        "content": "First final value.",
                        "time": "2026-02-06",
                        "source_refs": ["/s/s1#d1-1"],
                    },
                    {
                        "op": "update",
                        "memory_id": "def45678",
                        "content": "Second final value.",
                        "time": "2026-02-06",
                        "source_refs": ["/s/s1#d1-1"],
                    },
                ],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert (root / "topics/note.md").read_bytes() == before


def test_record_api_updates_and_deletes_one_legacy_unit_without_the_other(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-07",
                "source_refs": ["/s/s1#d1-1"],
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-08",
                "source_refs": ["/s/s1#d1-1"],
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
                memory_changes=[{"op": "delete", "memory_id": "mem_first"}],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert caught.value.code == "INVALID_TOPIC_FORMAT"
    assert "mem_first" in caught.value.message
    assert (root / "topics/note.md").read_bytes() == before


def test_record_update_does_not_change_an_unrelated_legacy_id(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/legacy.md").write_text(
        "# Legacy\n\nLegacy.[^mem_old]\n\n"
        "[^mem_old]: Time: `2026-01-09`; "
        "Sources: [D1:1](/s/s1#d1-1)\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Updated modern fact.",
                "time": "2026-02-09",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["mem_old"].content == "Legacy."


def test_record_update_preserves_unassigned_legacy_trailing_prose(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    note = LEGACY_NOTE.replace(
        "Second legacy fact.[^mem_second]",
        "Second legacy fact.[^mem_second] trailing prose",
    )
    (root / "topics/note.md").write_text(note, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated first legacy fact.",
                "time": "2026-02-10",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["mem_second"].content == "Second legacy fact."
    assert "trailing prose" in (root / "topics/note.md").read_text()


def test_record_update_and_delete_support_legacy_definition_before_paragraph(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(
        "# Legacy\n\n"
        "[^mem_first]: Time: `2026-01-10`; "
        "Sources: [D1:1](/s/s1#d1-1)\n\n"
        "Legacy.[^mem_first]\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated legacy.",
                "time": "2026-02-11",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )
        assert {
            unit.memory_id: unit.content
            for unit in parse_topic_tree(root / "topics")
        }["mem_first"] == "Updated legacy."
        workspace.update(
            memory_changes=[{"op": "delete", "memory_id": "mem_first"}],
            git_commit="off",
        )

    assert "mem_first" not in {
        unit.memory_id for unit in parse_topic_tree(root / "topics")
    }


def test_record_update_legacy_ignores_existing_legacy_record_block_id(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(LEGACY_NOTE, encoding="utf-8")
    (root / "topics/collision.md").write_text(
        "# Existing\n\nExisting.[^e-existing] ^legacy-record-1\n\n"
        "[^e-existing]: Time: `2026-01-11`; "
        "Sources: [D1:1](/s/s1#d1-1)\n",
        encoding="utf-8",
    )
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "mem_first",
                "content": "Updated without a temporary ID.",
                "time": "2026-02-12",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )

    ids = {unit.memory_id for unit in parse_topic_tree(root / "topics")}
    assert {"mem_first", "mem_second", "legacy-record-1"} <= ids


def test_record_update_can_link_to_a_legacy_memory_id(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/legacy.md").write_text(LEGACY_NOTE, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "See [legacy](legacy.md#^mem_first).",
                "time": "2026-02-13",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )

    units = {unit.memory_id: unit for unit in parse_topic_tree(root / "topics")}
    assert units["abc12345"].relation_targets == ("mem_first",)


def test_record_delete_removes_last_legacy_unit_with_unbound_trailing_prose(
    tmp_path,
):
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    legacy = (
        "# Legacy\n\nLegacy.[^mem_old] trailing prose\n\n"
        "[^mem_old]: Time: `2026-01-12`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    (root / "topics/note.md").write_text(legacy, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{"op": "delete", "memory_id": "mem_old"}],
            git_commit="off",
        )

    assert not (root / "topics/note.md").exists()


def test_standard_record_operations_keep_pre_release_aliases_compatible(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "update",
                "memory_id": "abc12345",
                "content": "Legacy alias input.",
                "time": "2026-08-15",
                "source_refs": ["/s/s1#d1-1"],
            }],
            git_commit="off",
        )

    unit = {
        unit.memory_id: unit for unit in parse_topic_tree(root / "topics")
    }["abc12345"]
    assert unit.content == "Legacy alias input."


def test_create_record_inserts_before_an_anchor_in_an_existing_section(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "create_record",
                "content": "Inserted before the existing record.",
                "time": "2026-08-15",
                "source_refs": ["/s/s1#d1-1"],
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

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
                memory_changes=[{
                    "op": "create_record",
                    "content": "Incomplete destination.",
                    "time": "2026-08-15",
                    "source_refs": ["/s/s1#d1-1"],
                    "destination": destination,
                }],
                git_commit="off",
            )


def test_move_records_moves_a_batch_in_declared_order_and_prunes_sources(
    tmp_path,
):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    before = {
        unit.memory_id: unit.created_order
        for unit in parse_topic_tree(root / "topics")
    }
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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
    assert "(/s/s1#d1-1)" in grouped
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[
                {
                    "op": "update_record",
                    "memory_id": "abc12345",
                    "content": "Updated and moved.",
                    "time": "2026-08-15",
                    "source_refs": ["/s/s1#d1-1"],
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="physical block"):
            workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="physical block order"):
            workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").write_text(MERGED, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    first = (
        "# A\n\nAlpha.[^e-collision] ^aaaaaaaa\n\n"
        "[^e-collision]: Time: `2026-01-01`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    second = (
        "# B\n\nBeta.[^e-collision] ^bbbbbbbb\n\n"
        "[^e-collision]: Time: `2026-02-02`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    centralized = (
        "# A\n\nOne.[^e-one] ^aaaaaaaa\n\n"
        "# B\n\nTwo.[^e-two] ^bbbbbbbb\n\n"
        "# References\n\n"
        "[^e-one]: Time: `2026-01-01`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
        "[^e-two]: Time: `2026-01-02`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    (root / "topics/a.md").write_text(centralized, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    (root / "topics/note.md").unlink()
    (root / "topics/second.md").unlink()
    first = (
        "# A\n\nAlpha.[^e-collision] ^aaaaaaaa\n\n"
        "[^e-collision]: Time: `2026-01-01`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    second = (
        "# B\n\nBeta.[^e-collision] ^bbbbbbbb\n\n"
        "[^e-collision]: Time: `2026-02-02`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    (root / "topics/a.md").write_text(first, encoding="utf-8")
    (root / "topics/b.md").write_text(second, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="conflicts across sources"):
            workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    legacy = (
        "# Legacy\n\nLegacy.[^mem_old] trailing prose\n\n"
        "[^mem_old]: Time: `2026-01-12`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    (root / "topics/note.md").write_text(legacy, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.markdown import parse_topic_tree

    root = _root(tmp_path)
    same_section = (
        "# Note\n\n"
        "First.[^e-first] ^abc12345\n\n"
        "Second.[^e-second] ^def45678\n\n"
        "[^e-first]: Time: `2026-01-01`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
        "[^e-second]: Time: `2026-01-02`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )
    (root / "topics/note.md").write_text(same_section, encoding="utf-8")
    (root / "topics/second.md").unlink()
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    before = (root / "topics/note.md").read_bytes()
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError, match="destination section"):
            workspace.update(
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
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    core_topic = NOTE.replace("# Note", "# Core").replace(
        "abc12345", "core12345"
    )
    (root / "topics/core.md").write_text(core_topic, encoding="utf-8")
    (root / "core.md").write_text("stale core\n", encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(
            memory_changes=[{
                "op": "delete_record",
                "memory_id": "core12345",
            }],
            git_commit="off",
        )

    assert not (root / "topics/core.md").exists()
    assert not (root / "core.md").exists()


def test_a_writer_may_delete_a_block_but_not_one_still_linked(tmp_path):
    """The writer may remove memory (Git keeps it); what it may not do is
    leave a link pointing at nothing."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    (root / "topics/other.md").write_text(LINKING, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/note.md").unlink()
        with pytest.raises(ValueError, match="dangling block link: abc12345"):
            workspace.commit_edits(*baseline)
        assert workspace.check_edits(baseline) is None  # the stage was reset
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/note.md").unlink()
        assert "dangling block link" in workspace.check_edits(baseline)
    assert (root / "topics/note.md").is_file()


def test_a_person_deletes_a_topic_and_keeps_every_other_block(tmp_path):
    from openprogram.state.memory.management.transaction import staged_edit

    root = _root(tmp_path)
    ok, message = staged_edit(
        root, lambda stage: (stage / "topics/second.md").unlink(),
        deleting="second.md", record_history=False,
    )
    assert ok, message
    assert not (root / "topics/second.md").exists()

    def drop_note_block(stage):
        (stage / "topics/second.md").write_text(SECOND, encoding="utf-8")
        (stage / "topics/note.md").unlink()

    ok, message = staged_edit(root, drop_note_block, record_history=False)
    assert not ok and "block ID must not be removed: abc12345" in message


def test_a_file_changed_beside_a_running_write_is_taken_in(tmp_path):
    """Another tool writing a file this write never touched is not a
    conflict: the write installs on top of it and keeps its bytes."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    outside = NOTE.replace("worth keeping", "edited elsewhere")
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").write_text(
            SECOND.replace("removable", "rewritten"), encoding="utf-8",
        )
        (root / "topics/note.md").write_text(outside, encoding="utf-8")
        (root / "topics/added.md").write_text(
            LINKING.replace("^fed87654", "^aaa11111").replace("e-3f", "e-6f"),
            encoding="utf-8",
        )
        (root / "index.sqlite").write_bytes(b"a cache a reader rewrote")
        workspace.commit_edits(*baseline)

    assert (root / "topics/note.md").read_text(encoding="utf-8") == outside
    assert "rewritten" in (root / "topics/second.md").read_text(encoding="utf-8")
    assert (root / "topics/added.md").is_file()
    recent = (root / "recent_events.jsonl").read_text(encoding="utf-8")
    assert "edited elsewhere" in recent and "aaa11111" in recent


def test_a_file_this_write_changed_and_someone_else_did_too_conflicts(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    outside = SECOND.replace("removable", "edited elsewhere")
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").write_text(
            SECOND.replace("removable", "rewritten"), encoding="utf-8",
        )
        (root / "topics/second.md").write_text(outside, encoding="utf-8")
        with pytest.raises(TransactionError) as caught:
            workspace.commit_edits(*baseline)
    assert caught.value.code == "CONCURRENT_UPDATE"
    assert (root / "topics/second.md").read_text(encoding="utf-8") == outside


def test_a_file_written_beside_a_write_that_does_not_parse_is_kept_as_it_is(tmp_path):
    """An outside tool writing plain Markdown beside a running write is no
    conflict: its file is taken in as it is, read as memory, and the
    write's own edit lands."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    plain = "# Plain\n\nNo ID here.\n"
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").write_text(
            SECOND.replace("removable", "rewritten"), encoding="utf-8",
        )
        (root / "topics/plain.md").write_text(plain, encoding="utf-8")
        workspace.commit_edits(*baseline)
    assert "rewritten" in (root / "topics/second.md").read_text(encoding="utf-8")
    assert (root / "topics/plain.md").read_text(encoding="utf-8") == plain
    assert "No ID here" in (root / "recent_events.jsonl").read_text(encoding="utf-8")


def test_an_outside_file_rewritten_beside_a_write_is_kept_as_rewritten(tmp_path):
    """An outside file changed by its own tool while a write runs goes back
    as the tool left it, not as the write first saw it."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    (root / "topics/plain.md").write_text("# Plain\n\nFirst.\n", encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        assert "plain.md" in workspace.outside
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").write_text(
            SECOND.replace("removable", "rewritten"), encoding="utf-8",
        )
        (root / "topics/plain.md").write_text("# Plain\n\nSecond.\n", encoding="utf-8")
        workspace.commit_edits(*baseline)
    assert "rewritten" in (root / "topics/second.md").read_text(encoding="utf-8")
    assert (root / "topics/plain.md").read_text(encoding="utf-8") == "# Plain\n\nSecond.\n"
