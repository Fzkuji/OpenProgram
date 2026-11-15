"""What retention and a finished trial leave behind, and that they say so.

The Windows refusals come from a stand-in ``os.unlink``; this has not run on Windows.
"""
import logging
import os

import pytest

from openprogram.operations.self_update.trial import instance as trials
from openprogram.operations.self_update.versions import retention, store
from tests.support.sealed_versions import make_layout, make_version, point_at
from tests.support.windows_deletes import tree_with_read_only_objects, windows_unlink


@pytest.fixture
def layout(tmp_path):
    return make_layout(tmp_path / "state" / "runtime")


def _retire_one_version(layout, monkeypatch):
    names = [f"202611{day:02d}T000000Z-aaaaaaaaaaaa" for day in range(1, 7)]
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1])
    monkeypatch.setattr(store, "committed_versions", lambda _layout: set(names))
    return names[0]


def test_retention_removes_a_version_holding_read_only_files(layout, monkeypatch):
    retired = _retire_one_version(layout, monkeypatch)
    tree_with_read_only_objects(layout.version(retired) / "repo")
    windows_unlink(monkeypatch)
    done = retention.run(layout, layout.root.parent)
    assert done.versions == [retired]
    assert not any(layout.trash.iterdir()), "nothing is left in trash for ever"
    assert done.remaining == []


def test_retention_reports_what_a_running_process_still_holds(layout, monkeypatch, caplog):
    retired = _retire_one_version(layout, monkeypatch)
    held = layout.version(retired) / "venv" / "python312.dll"
    held.write_bytes(b"held")
    windows_unlink(monkeypatch, locked={"python312.dll"})
    with caplog.at_level(logging.WARNING, logger=retention.__name__):
        done = retention.run(layout, layout.root.parent)
    assert len(done.remaining) == 1
    assert done.remaining[0].startswith(f"trash/{retired}.") and done.remaining[0].endswith("/venv/python312.dll")
    assert "left 1 entries for the next pass" in caplog.text
    monkeypatch.undo()
    monkeypatch.setattr(store, "committed_versions", lambda _layout: set())
    assert retention.run(layout, layout.root.parent).remaining == [], "the next pass finishes it"
    assert not any(layout.trash.iterdir())


def test_retention_reports_a_version_it_could_not_move_aside(layout, monkeypatch):
    retired = _retire_one_version(layout, monkeypatch)
    real = os.replace

    def replace(source, target):
        if os.fspath(source).endswith(retired):
            raise PermissionError(13, "Access is denied", source)  # Windows, while a process runs from it.
        return real(source, target)

    monkeypatch.setattr(retention.os, "replace", replace)
    done = retention.run(layout, layout.root.parent)
    assert done.remaining == [f"versions/{retired}"]
    assert layout.version(retired).is_dir()


def test_a_finished_trial_with_read_only_files_leaves_nothing(layout, monkeypatch):
    workspace = trials.workspace_for(layout, "u1").create()
    tree_with_read_only_objects(workspace.home / "project")
    windows_unlink(monkeypatch)
    workspace.finish(passed=True)
    assert not workspace.directory.exists()
