"""``openprogram memory edit`` — containment, transaction, cleanup.

The CLI is the second surface where a hand edit reaches memory outside the
structured transaction (the topic editor in the web UI is the first), and it
runs the same staged edit. Three things are checked here: the path named on
the command line cannot reach outside the writable surface, a rejected edit
leaves the committed file byte-for-byte alone, and neither outcome leaves a
staged copy of memory behind in the temp directory.

The fixture builds a minimal but *valid* workspace — one topic paragraph
carrying a block ID and a footnote that resolves to an archived source —
because anything less is refused by the parser before the edit is reached.
"""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

SOURCE = '# Conversation 1\n\n<a id="d1-1"></a>\n\nuser: remember this\n'
NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)


def _stage_dirs() -> set[str]:
    """The workspace staging trees currently sitting in the temp directory."""
    return set(glob.glob(
        os.path.join(tempfile.gettempdir(), "scriptorium-topics-*")
    ))


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """A memory workspace holding one valid topic. Returns its root."""
    import openprogram.paths as paths
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)

    from openprogram.memory import store
    root = store.ensure()
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "D1.md").write_text(SOURCE, encoding="utf-8")
    (root / "topics" / "note.md").write_text(NOTE, encoding="utf-8")
    return root


class _Editor:
    """Stands in for $EDITOR: writes ``content`` into the file it is given."""

    def __init__(self, monkeypatch):
        self.opened: list[Path] = []
        self.content: str | None = None
        monkeypatch.setattr(subprocess, "call", self._call)

    def _call(self, argv, *_args, **_kwargs):
        self.opened.append(Path(argv[-1]))
        if self.content is not None:
            self.opened[-1].write_text(self.content, encoding="utf-8")
        return 0


@pytest.fixture
def editor(monkeypatch):
    return _Editor(monkeypatch)


def _edit(root, path):
    from openprogram.cli import _memory_edit
    return _memory_edit(root, path)


# ---- what may be named on the command line ----------------------------


@pytest.mark.parametrize("path", [
    "../escape",
    "../../escape",
    "topics/../../escape",
    "/etc/passwd",
])
def test_edit_refuses_a_path_outside_the_workspace(memory, editor, path):
    assert _edit(memory, path) == 1
    assert not editor.opened, "the editor must not open a file we refuse"
    assert not (memory.parent / "escape.md").exists()


@pytest.mark.parametrize("path", [
    "sources/D1",          # the append-only evidence record
    "timeline/2026/01/01",  # a derived view, rebuilt from topics
    "recent_events.jsonl",
])
def test_edit_refuses_a_file_that_is_not_hand_written(memory, editor, path):
    assert _edit(memory, path) == 1
    assert not editor.opened


def test_edit_reports_a_topic_that_does_not_exist(memory, editor):
    assert _edit(memory, "topics/missing") == 1
    assert not editor.opened


# ---- an edit either lands whole or not at all -------------------------


def test_edit_dropping_a_block_id_is_refused_and_changes_nothing(
    memory, editor, capsys
):
    editor.content = "# Note\n"

    assert _edit(memory, "topics/note.md") == 1
    out = capsys.readouterr().out
    assert "abc12345" in out
    assert (memory / "topics/note.md").read_text(encoding="utf-8") == NOTE
    # The rejected text is the user's typing; it is kept, and where.
    kept = Path(out.rsplit("kept at ", 1)[1].strip())
    assert kept.read_text(encoding="utf-8") == "# Note\n"


def test_edit_keeping_the_block_id_lands(memory, editor):
    editor.content = NOTE.replace("worth keeping", "worth remembering")

    assert _edit(memory, "topics/note") == 0
    assert "worth remembering" in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")


def test_edit_that_changes_nothing_is_not_a_write(memory, editor, capsys):
    assert _edit(memory, "topics/note.md") == 0
    assert "unchanged" in capsys.readouterr().out
    assert (memory / "topics/note.md").read_text(encoding="utf-8") == NOTE


def test_edit_gives_up_while_the_workspace_lock_is_held(memory, editor):
    from openprogram.memory.scriptorium.management.transaction import (
        workspace_write_lock,
    )
    editor.content = NOTE.replace("worth keeping", "worth remembering")

    with workspace_write_lock(memory):
        assert _edit(memory, "topics/note.md") == 1
    assert (memory / "topics/note.md").read_text(encoding="utf-8") == NOTE


# ---- staging leaves nothing behind ------------------------------------


def test_stage_directories_are_cleaned_up_on_both_paths(memory, editor):
    before = _stage_dirs()

    editor.content = "# Note\n"
    assert _edit(memory, "topics/note.md") == 1
    editor.content = NOTE.replace("worth keeping", "worth remembering")
    assert _edit(memory, "topics/note.md") == 0

    assert _stage_dirs() == before
