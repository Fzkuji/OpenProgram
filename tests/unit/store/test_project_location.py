"""Project location recovery is explicit, bounded, and identity checked."""
from types import SimpleNamespace

from openprogram.state.store.project import identity
from openprogram.state.store.project import location


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
        "openprogram.state.store.session.session_store.default_store",
        lambda: object(),
    )
    import openprogram.state.store.session.migration as migration
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
        "openprogram.state.store.session.migration._live_jobs",
        lambda _session_id: active["value"],
    )
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store",
        lambda: object(),
    )
    import openprogram.state.store.session.migration as migration

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



def test_replacement_at_old_path_blocks_execution(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    captured = identity.capture_directory_identity(folder)
    project = _project(path=str(folder), location_state=location.AVAILABLE,
                       **captured)
    folder.rename(tmp_path / "original")
    folder.mkdir()
    assert location.bound_execution_state(project) == location.REPLACED


def test_portable_identity_does_not_depend_on_device_or_inode(tmp_path, monkeypatch):
    captured = identity.capture_directory_identity(tmp_path)
    project = _project(path=str(tmp_path), location_state=location.AVAILABLE,
                       **captured)
    def unavailable(_path):
        raise AssertionError("portable identities must not consult device or inode")
    monkeypatch.setattr(identity, "inode_token", unavailable)
    assert location.bound_execution_state(project) is None
