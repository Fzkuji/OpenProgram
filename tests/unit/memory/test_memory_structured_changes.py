"""Structured memory changes share one atomic transaction contract."""

from __future__ import annotations

from contextlib import closing
import json

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


def test_record_delete_rejects_last_legacy_unit_with_unbound_trailing_prose(
    tmp_path,
):
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    legacy = (
        "# Legacy\n\nLegacy.[^mem_old] trailing prose\n\n"
        "[^mem_old]: Time: `2026-01-12`; "
        "Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    (root / "topics/note.md").write_text(legacy, encoding="utf-8")
    with closing(MemoryWorkspace(root)) as workspace:
        revision = workspace.revision()
        with pytest.raises(TransactionError) as caught:
            workspace.update(
                base_revision=revision,
                memory_changes=[{"op": "delete", "memory_id": "mem_old"}],
                git_commit="off",
            )
        assert workspace.revision() == revision

    assert caught.value.code == "INVALID_ARGUMENT"
    assert "direct file editing" in caught.value.message
    assert (root / "topics/note.md").read_text(encoding="utf-8") == legacy
