"""Project location recovery is explicit, bounded, and identity checked."""
from types import SimpleNamespace

from openprogram.store.project import identity
from openprogram.store.project import location


def _project(**overrides):
    values = {
        "id": "p1",
        "path": "",
        "is_default": False,
        "location_state": location.PENDING,
        "directory_identity": "identity",
        "native_bookmark": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_existing_legacy_path_is_unverified_until_explicit_locate(tmp_path):
    project = _project(path=str(tmp_path), directory_identity="",
                       location_state=location.AVAILABLE)

    assert identity.path_is_replacement(project, tmp_path)
    assert location.evaluate_project_location(project) == location.PENDING


def test_pending_access_retries_only_that_project(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(location.projects, "get_project", lambda _id: project)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store",
        lambda: object(),
    )
    import openprogram.store.session.migration as migration
    monkeypatch.setattr(
        migration, "run_project_migration",
        lambda project_id, store, timeout: calls.append(
            (project_id, store, timeout)),
        raising=False,
    )
    location._migration_attempted.clear()

    assert location.refresh_project_location("p1") == location.PENDING
    assert location.refresh_project_location("p1") == location.PENDING
    assert [call[0] for call in calls] == ["p1"]


def test_deferred_migration_retries_after_writers_finish(monkeypatch):
    project = _project(session_ids=["s1"])
    calls = []
    active = {"value": True}
    monkeypatch.setattr(location.projects, "get_project", lambda _id: project)
    monkeypatch.setattr(
        "openprogram.store.session.migration._live_jobs",
        lambda _session_id: active["value"],
    )
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store",
        lambda: object(),
    )
    import openprogram.store.session.migration as migration

    def migrate(_project_id, _store, *, timeout):
        calls.append(timeout)
        if len(calls) == 2:
            project.location_state = location.AVAILABLE
        return {"s1": "deferred" if len(calls) == 1 else "done"}

    monkeypatch.setattr(migration, "run_project_migration", migrate)
    location._migration_attempted.clear()
    location._migration_deferred.clear()

    assert location.refresh_project_location("p1") == location.PENDING
    assert location.refresh_project_location("p1") == location.PENDING
    active["value"] = False
    assert location.refresh_project_location("p1") == location.AVAILABLE
    assert calls == [2.0, 2.0]


def test_native_event_clears_only_touched_project_retry(monkeypatch):
    p1 = _project(id="p1", path="/project-one")
    p2 = _project(id="p2", path="/project-two")
    observer = location.LocationObserver()
    monkeypatch.setattr(location.projects, "list_projects", lambda: [p1, p2])
    monkeypatch.setattr(
        location.projects, "get_project",
        lambda project_id: p1 if project_id == "p1" else p2,
    )
    monkeypatch.setattr(location, "refresh_project_location",
                        lambda project_id: calls.append(project_id) or location.PENDING)
    calls = []
    location._migration_attempted.update({"p1", "p2"})

    observer._on_native_paths([p1.path])
    observer.stop()

    assert calls == ["p1"]
    assert "p1" not in location._migration_attempted
    assert "p2" in location._migration_attempted


def test_bookmark_path_cannot_override_same_device_inode_mismatch(tmp_path, monkeypatch):
    folder = tmp_path / 'project'
    folder.mkdir()
    project = _project(path=str(folder), location_state=location.AVAILABLE,
                       directory_identity=identity.inode_token(folder), native_bookmark='bookmark')
    folder.rename(tmp_path / 'original')
    folder.mkdir()
    monkeypatch.setattr(identity.native, 'resolve_bookmark', lambda _blob: str(folder))
    assert location.bound_execution_state(project) == location.REPLACED


def test_bookmark_remains_available_when_device_identity_changes(tmp_path, monkeypatch):
    device, inode = identity.inode_token(tmp_path).split(':')
    project = _project(path=str(tmp_path), location_state=location.AVAILABLE,
                       directory_identity=f'{int(device) + 1}:{inode}', native_bookmark='bookmark')
    monkeypatch.setattr(identity.native, 'resolve_bookmark', lambda _blob: str(tmp_path))
    assert location.bound_execution_state(project) is None
