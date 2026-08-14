"""Structured memory changes share one atomic transaction contract."""

from __future__ import annotations

from contextlib import closing

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


def test_memory_agent_commit_can_delete_current_memory(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        (workspace.stage_dir / "topics/second.md").unlink()
        workspace.commit_edits(*baseline)

    assert not (root / "topics/second.md").exists()
