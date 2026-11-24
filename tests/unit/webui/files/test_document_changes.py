"""A window showing a file is told when the file changes on disk, by its key."""
from __future__ import annotations

import pytest

from openprogram.host._platform import changes
from openprogram.interfaces.server.routes.files import document_changes


@pytest.fixture(autouse=True)
def forget():
    document_changes._forget_all()
    yield
    document_changes._forget_all()


def _anchor():
    # Anchored as the watcher threads would, which unit tests do not start.
    for entry in changes._watched:
        entry.anchor = changes._anchor(entry.path)


def test_a_followed_file_changing_publishes_its_key_and_its_neighbours_do_not(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("one", encoding="utf-8")
    key = document_changes.follow(report)
    assert key == document_changes.key_of(report)
    _anchor()
    folder = changes._real(tmp_path)
    with changes.listen(document_changes.TOPIC) as listener:
        changes._dispatch([folder / "notes.md"])
        assert listener.wait(0) == set()
        changes._dispatch([folder / "report.md"])
        assert listener.wait(0) == {f"{document_changes.TOPIC}:{key}"}


def test_two_files_in_one_folder_are_told_apart(tmp_path):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    for path in (first, second):
        path.write_text("x", encoding="utf-8")
    keys = document_changes.follow(first), document_changes.follow(second)
    assert document_changes.follow(first) == keys[0], "following again is the same key"
    _anchor()
    with changes.listen(document_changes.TOPIC) as listener:
        changes._dispatch([changes._real(tmp_path) / "b.txt"])
        assert listener.wait(0) == {f"{document_changes.TOPIC}:{keys[1]}"}


def test_only_the_newest_files_stay_followed(tmp_path, monkeypatch):
    monkeypatch.setattr(document_changes, "LIMIT", 2)
    paths = [tmp_path / f"{n}.txt" for n in range(3)]
    for path in paths:
        path.write_text("x", encoding="utf-8")
        document_changes.follow(path)
    _anchor()
    with changes.listen(document_changes.TOPIC) as listener:
        changes._dispatch([changes._real(tmp_path) / "0.txt"])
        assert listener.wait(0) == set(), "the oldest file is let go"
        changes._dispatch([changes._real(tmp_path) / "2.txt"])
        assert listener.wait(0) == {f"{document_changes.TOPIC}:{document_changes.key_of(paths[2])}"}
