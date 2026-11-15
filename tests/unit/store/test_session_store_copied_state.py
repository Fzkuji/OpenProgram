"""A copied or moved state directory keeps to its own conversations."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from openprogram.state.store.session.placement import LOCATIONS_FILE, rooted_location
from openprogram.state.store.session.session_store import SessionStore


def _files(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def test_a_copy_never_writes_the_conversation_it_was_copied_from(tmp_path):
    original_root = tmp_path / "original" / "projects"
    original = SessionStore(original_root)
    original.create_session("s1", "main", title="kept")
    folder = original._session_dir("s1")
    # What moving a conversation into a project records: its folder, absolute.
    original._record_location("s1", folder)
    assert json.loads((original_root / LOCATIONS_FILE).read_text())["s1"] == str(folder)
    copy_root = tmp_path / "copy" / "projects"
    shutil.copytree(original_root, copy_root)
    before = _files(folder)

    copy = SessionStore(copy_root)
    copied_folder = copy_root / folder.relative_to(original_root)
    assert copy._session_dir("s1") == copied_folder
    copy.append_message("s1", {"id": "m1", "role": "user", "content": "only in the copy"})
    copy.update_session("s1", title="renamed in the copy")

    assert _files(folder) == before
    assert SessionStore(original_root).get_session("s1")["title"] == "kept"
    assert SessionStore(copy_root).get_session("s1")["title"] == "renamed in the copy"


def test_a_workdir_location_outside_the_store_stays_where_it_is(tmp_path):
    root = tmp_path / "state" / "projects"
    workdir_copy = tmp_path / "paper" / ".openprogram" / "sessions" / "s1"
    assert rooted_location(root, str(workdir_copy)) == workdir_copy
    assert rooted_location(root, str(root / "default" / "chats" / "s1")) == root / "default" / "chats" / "s1"
    other = tmp_path / "elsewhere" / "projects" / "p1" / "chats" / "s1"
    assert rooted_location(root, str(other)) == root / "p1" / "chats" / "s1"
