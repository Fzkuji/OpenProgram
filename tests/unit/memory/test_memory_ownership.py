"""Every install records which Topic files are the Runtime's and which
came from outside. A record only: no write reads it yet."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing

import pytest

from tests.unit.memory.test_memory_structured_changes import NOTE, SECOND, _root


@pytest.fixture(autouse=True)
def _conversation(chats):
    """The chat message every record here cites."""
    chats.say("s1", "d1-1", "remember this")


def _record(root):
    from openprogram.state.memory.workspace_layout import runtime_dir

    return json.loads(
        (runtime_dir(root) / "installed.json").read_text(encoding="utf-8")
    )["files"]


def _commit(root, edit):
    from openprogram.state.memory.management import MemoryWorkspace

    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        edit(workspace.stage_dir)
        workspace.commit_edits(*baseline)


def _hashes_match(root):
    files = _record(root)
    on_disk = {
        path.relative_to(root / "topics").as_posix():
            hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "topics").rglob("*.md")
    }
    assert {name: entry["sha256"] for name, entry in files.items()} == on_disk


def test_every_install_records_each_file_and_its_origin(tmp_path):
    root = _root(tmp_path)
    (root / "topics/plain.md").write_text("# Plain\n\nNo ID here.\n", encoding="utf-8")
    from openprogram.state.memory.management.transaction import staged_edit

    # A person's save installs; the uncited file is outside and the save
    # did not touch it, so it stays someone else's.
    ok, message = staged_edit(
        root, lambda stage: (stage / "topics/second.md").write_text(
            SECOND.replace("removable", "kept"), encoding="utf-8"),
        record_history=False,
    )
    assert ok, message
    _hashes_match(root)
    files = _record(root)
    assert files["second.md"]["origin"] == "runtime"
    assert files["note.md"]["origin"] == "runtime"
    assert files["plain.md"]["origin"] == "outside"
    assert files["note.md"]["ids"] == ["abc12345"]

    # Edited elsewhere keeping its ID: still ours. Losing it: outside.
    (root / "topics/note.md").write_text(
        NOTE.replace("worth keeping", "edited by hand"), encoding="utf-8")
    (root / "topics/second.md").write_text(
        SECOND.replace("^def45678", "^fff99999"), encoding="utf-8")
    ok, message = staged_edit(
        root, lambda stage: (stage / "topics/new.md").write_text(
            NOTE.replace("^abc12345", "").replace("A fact", "New fact")
            .replace("e-1f", "e-9f"), encoding="utf-8"),
        record_history=False,
    )
    assert ok, message
    _hashes_match(root)
    files = _record(root)
    assert files["note.md"]["origin"] == "runtime"
    assert files["second.md"]["origin"] == "outside"
    assert files["new.md"]["origin"] == "runtime"
    assert files["plain.md"]["origin"] == "outside"


def test_a_writer_install_keeps_the_record_in_step(tmp_path):
    root = _root(tmp_path)
    _commit(root, lambda stage: (stage / "topics/second.md").unlink())
    _hashes_match(root)
    assert set(_record(root)) == {"note.md"}
