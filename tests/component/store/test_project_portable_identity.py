"""Portable moves through the real registration and session-store boundary."""
import json
import shutil

import pytest

from openprogram.state.store.project import project_store as projects
from openprogram.state.store.project.discovery import discover_moved_projects
from openprogram.state.store.project.location import refresh_project_location
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    result = SessionStore(state / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: result)
    yield result
    result.close()


def test_open_moved_folder_preserves_project_and_history(tmp_path, store):
    original = tmp_path / "old" / "paper"
    original.mkdir(parents=True)
    store.create_session("s1", "main", project_path=str(original), title="Research notes")
    project = projects.project_for_session("s1")
    # Cross-volume moves are represented portably as copy then delete.
    moved = tmp_path / "elsewhere" / "renamed"
    shutil.copytree(original, moved)
    shutil.rmtree(original)
    reopened = projects.resolve_project(moved)
    assert reopened.id == project.id
    assert reopened.session_ids == ["s1"]
    assert store.get_session("s1")["title"] == "Research notes"
    assert projects.get_project(project.id).path == str(moved)


def test_marker_discovery_keeps_identity_after_parent_move(tmp_path, store):
    parent = tmp_path / "group"
    folder = parent / "paper"
    folder.mkdir(parents=True)
    project = projects.resolve_project(folder)
    parent.rename(tmp_path / "renamed-group")
    assert discover_moved_projects() == [project.id]
    assert projects.get_project(project.id).path == str(tmp_path / "renamed-group" / "paper")


def test_open_copy_gets_independent_project(tmp_path, store):
    original = tmp_path / "paper"
    original.mkdir()
    project = projects.resolve_project(original)
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    other = projects.resolve_project(copy)
    assert other.id != project.id
    assert other.directory_identity != project.directory_identity
    assert projects.get_project(project.id).path == str(original)


def test_multiple_marker_copies_are_not_auto_selected(tmp_path, store):
    original = tmp_path / "paper"
    original.mkdir()
    project = projects.resolve_project(original)
    shutil.copytree(original, tmp_path / "a")
    shutil.copytree(original, tmp_path / "b")
    shutil.rmtree(original)
    assert discover_moved_projects(roots=[tmp_path]) == []
    assert projects.get_project(project.id).path == str(original)


def test_symlink_marker_is_never_overwritten(tmp_path, store):
    folder = tmp_path / "paper"
    folder.mkdir()
    target = tmp_path / "private.json"
    target.write_text('{"private":true}')
    meta = folder / ".openprogram"
    meta.mkdir()
    (meta / "project.json").symlink_to(target)
    with pytest.raises(projects.ProjectStoreError):
        projects.resolve_project(folder)
    assert json.loads(target.read_text()) == {"private": True}


def test_legacy_locate_keeps_sessions_and_enables_future_moves(tmp_path, store):
    original = tmp_path / "paper"
    original.mkdir()
    store.create_session("s1", "main", project_path=str(original), title="Old notes")
    project = projects.project_for_session("s1")
    # A legacy record cannot prove continuity at a new path before Locate.
    project.directory_identity = "old-device:old-inode"
    projects._upsert(project)
    (original / ".openprogram" / "project.json").unlink()
    moved = tmp_path / "moved"
    original.rename(moved)
    assert refresh_project_location(project.id) == "missing"
    projects.relocate_project(project.id, moved, replace_identity=True)
    renamed = tmp_path / "renamed"
    moved.rename(renamed)
    assert discover_moved_projects() == [project.id]
    assert store.get_session("s1")["title"] == "Old notes"


def test_verified_legacy_record_upgrades_at_original_path(tmp_path, store):
    from openprogram.state.store.project.identity import inode_token
    folder = tmp_path / "paper"
    folder.mkdir()
    project = projects.resolve_project(folder)
    (folder / ".openprogram" / "project.json").unlink()
    project.directory_identity = inode_token(folder)
    projects._upsert(project)
    assert refresh_project_location(project.id) == "available"
    folder.rename(tmp_path / "renamed")
    assert discover_moved_projects() == [project.id]


@pytest.mark.parametrize("contents", ['{"version":1,"id":3}', '{"version":9}', 'not json'])
def test_invalid_marker_does_not_register_or_overwrite(tmp_path, store, contents):
    folder = tmp_path / "paper"
    (folder / ".openprogram").mkdir(parents=True)
    marker = folder / ".openprogram" / "project.json"
    marker.write_text(contents)
    with pytest.raises(projects.ProjectStoreError):
        projects.resolve_project(folder)
    assert marker.read_text() == contents
    assert projects.list_projects() == []


def test_unwritable_marker_reports_error_without_registration(tmp_path, store, monkeypatch):
    import openprogram.state.store.project.identity as identity
    folder = tmp_path / "paper"
    folder.mkdir()
    def denied(*args, **kwargs):
        raise PermissionError("read-only directory")
    monkeypatch.setattr(identity.tempfile, "mkstemp", denied)
    with pytest.raises(projects.ProjectStoreError, match="read-only directory"):
        projects.resolve_project(folder)
    assert projects.list_projects() == []


def test_relocation_refuses_another_registered_marker(tmp_path, store):
    first, second, copy = (tmp_path / name for name in ("first", "second", "copy"))
    first.mkdir()
    second.mkdir()
    project = projects.resolve_project(first)
    other = projects.resolve_project(second)
    shutil.copytree(second, copy)
    with pytest.raises(projects.ProjectStoreError, match="identity belongs to another project"):
        projects.relocate_project(project.id, copy, replace_identity=True)
    assert projects.get_project(project.id).path == str(first)
    assert projects.get_project(other.id).path == str(second)


def test_failed_session_repair_stays_pending(tmp_path, store, monkeypatch):
    folder = tmp_path / "paper"
    folder.mkdir()
    project = projects.resolve_project(folder)
    moved = tmp_path / "moved"
    folder.rename(moved)
    def fail(*args, **kwargs):
        raise OSError("session update failed")
    monkeypatch.setattr(store, "relocate_project_sessions", fail)
    assert refresh_project_location(project.id) == "pending"
    assert projects.get_project(project.id).path == str(moved)
    assert projects.get_project(project.id).migration_error == "session update failed"


def test_a_pending_project_with_nothing_left_to_migrate_becomes_available(
    tmp_path, store, monkeypatch,
):
    folder = tmp_path / "paper"
    folder.mkdir()
    project = projects.resolve_project(folder)
    moved = tmp_path / "moved"
    folder.rename(moved)
    with monkeypatch.context() as patch:
        patch.setattr(store, "relocate_project_sessions",
                      lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
        assert refresh_project_location(project.id) == "pending"
    # The folder carries its marker and no session waits on a repair.
    assert refresh_project_location(project.id) == "available"
    assert projects.get_project(project.id).location_state == "available"


def test_first_execution_after_move_uses_new_project_location(tmp_path, store):
    from openprogram.runtime.conversation.workdir import apply_default_workdir
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder))
    moved = tmp_path / "moved"
    folder.rename(moved)
    class Runtime:
        workdir = None
        def set_workdir(self, path):
            self.workdir = path
    runtime = Runtime()
    assert apply_default_workdir(runtime, "s1") == moved
    assert runtime.workdir == str(moved)


def test_opening_replacement_does_not_return_original_project(tmp_path, store):
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder), title="Original notes")
    project = projects.project_for_session("s1")
    folder.rename(tmp_path / "original")
    folder.mkdir()
    with pytest.raises(projects.ProjectStoreError, match="location"):
        projects.resolve_project(folder)
    assert not (folder / ".openprogram" / "project.json").exists()
    assert store.get_session("s1")["title"] == "Original notes"
    assert projects.get_project(project.id).session_ids == ["s1"]


def test_concurrent_open_registers_one_project_that_survives_move(tmp_path, store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, BrokenBarrierError
    folder = tmp_path / "paper"
    folder.mkdir()
    new_id = projects._new_project_id
    allocating = Barrier(2, timeout=2)
    def competing_ids():
        # Expose two callers deciding to register before either publishes.
        # A serialized registration has one allocator and expires this barrier.
        try:
            allocating.wait()
        except BrokenBarrierError:
            pass
        return new_id()
    monkeypatch.setattr(projects, "_new_project_id", competing_ids)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(projects.resolve_project, folder)
        second = pool.submit(projects.resolve_project, folder)
        a, b = first.result(timeout=30), second.result(timeout=30)
    assert a.id == b.id
    assert len(projects.list_projects()) == 1
    moved = tmp_path / "moved"
    folder.rename(moved)
    assert projects.resolve_project(moved).id == a.id


def test_legacy_upgrade_refuses_another_projects_marker(tmp_path, store):
    from openprogram.state.store.project.identity import inode_token
    first, second = (tmp_path / name for name in ("first", "second"))
    first.mkdir()
    second.mkdir()
    owner = projects.resolve_project(first)
    legacy = projects.resolve_project(second)
    legacy.directory_identity = inode_token(second)
    projects._upsert(legacy)
    shutil.copyfile(first / ".openprogram" / "project.json", second / ".openprogram" / "project.json")
    assert refresh_project_location(legacy.id) == "pending"
    assert projects.get_project(legacy.id).directory_identity == legacy.directory_identity
    assert projects.get_project(owner.id).directory_identity == owner.directory_identity
    moved = tmp_path / "moved"
    first.rename(moved)
    assert projects.resolve_project(moved).id == owner.id


def _registered_twice(tmp_path):
    """A folder registered, then moved and opened again as a new project."""
    gone = tmp_path / "dropbox" / "paper"
    gone.mkdir(parents=True)
    stale = projects.resolve_project(gone)
    current = tmp_path / "projects" / "paper"
    current.mkdir(parents=True)
    owner = projects.resolve_project(current)
    shutil.rmtree(gone)
    return stale, owner, current


def test_locating_a_missing_project_at_another_projects_folder_folds_it(tmp_path, store):
    stale, owner, current = _registered_twice(tmp_path)
    result = projects.relocate_project(stale.id, current, replace_identity=True)
    assert result.id == owner.id
    assert projects.get_project(stale.id) is None
    assert projects.get_project(owner.id).path == str(current.resolve())


def test_a_missing_project_with_conversations_does_not_fold(tmp_path, store):
    stale, owner, current = _registered_twice(tmp_path)
    projects.bind_session("s1", stale.id)
    with pytest.raises(projects.ProjectStoreError, match="keeps its conversations"):
        projects.relocate_project(stale.id, current, replace_identity=True)
    assert projects.get_project(stale.id).session_ids == ["s1"]


def test_a_present_project_cannot_move_onto_another_projects_folder(tmp_path, store):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    one = projects.resolve_project(first)
    projects.resolve_project(second)
    with pytest.raises(projects.ProjectStoreError, match="already the project"):
        projects.relocate_project(one.id, second, replace_identity=True)
    assert projects.get_project(one.id).path == str(first.resolve())


def test_moving_one_project_brings_back_the_ones_that_moved_with_it(tmp_path, store):
    """A whole Projects folder moved: locating one finds the rest."""
    old = tmp_path / "old" / "Projects"
    paper, notes = old / "paper", old / "notes"
    paper.mkdir(parents=True)
    notes.mkdir()
    first, second = projects.resolve_project(paper), projects.resolve_project(notes)
    new = tmp_path / "Archive" / "Projects"
    new.parent.mkdir()
    old.rename(new)

    projects.relocate_project(first.id, new / "paper", replace_identity=True)
    assert projects.get_project(second.id).path == str((new / "notes").resolve())
    assert projects.get_project(second.id).location_state == "available"


def test_a_sibling_is_followed_only_where_its_own_marker_is(tmp_path, store):
    old = tmp_path / "old"
    paper, notes = old / "paper", old / "notes"
    paper.mkdir(parents=True)
    notes.mkdir()
    first, second = projects.resolve_project(paper), projects.resolve_project(notes)
    new = tmp_path / "new"
    new.mkdir()
    paper.rename(new / "paper")
    shutil.rmtree(notes)
    (new / "notes").mkdir()  # same name, a different folder

    projects.relocate_project(first.id, new / "paper", replace_identity=True)
    assert projects.get_project(second.id).path == str(notes)


def test_opening_a_folder_inside_a_moved_project_reattaches_it(tmp_path, store):
    folder = tmp_path / "paper"
    (folder / "src").mkdir(parents=True)
    project = projects.resolve_project(folder)
    moved = tmp_path / "elsewhere" / "paper"
    moved.parent.mkdir()
    folder.rename(moved)

    projects.resolve_project(moved / "src")
    assert projects.get_project(project.id).path == str(moved.resolve())
    assert projects.get_project(project.id).location_state == "available"


def test_a_folder_inside_a_present_project_leaves_it_alone(tmp_path, store):
    folder = tmp_path / "paper"
    (folder / "src").mkdir(parents=True)
    project = projects.resolve_project(folder)
    inner = projects.resolve_project(folder / "src")
    assert inner.id != project.id
    assert projects.get_project(project.id).path == str(folder.resolve())


def test_a_legacy_record_is_confirmed_at_its_own_path(tmp_path, store):
    """A cloud-synced folder can change its inode; confirming the folder at
    the recorded path gives it a portable identity in place."""
    folder = tmp_path / "LLM Knowledge"
    folder.mkdir()
    project = projects.resolve_project(folder)
    (folder / ".openprogram" / "project.json").unlink()
    project = projects.get_project(project.id)
    project.directory_identity = "1:999"
    projects._upsert(project)
    assert refresh_project_location(project.id) in {"replaced", "pending"}

    confirmed = projects.relocate_project(project.id, folder, expected_path=project.path,
                                          replace_identity=True)
    assert confirmed.id == project.id
    assert refresh_project_location(project.id) == "available"
    assert projects.get_project(project.id).directory_identity.startswith("marker:")
