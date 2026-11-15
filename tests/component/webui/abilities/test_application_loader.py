"""Applications load from their own directory through one ProgramContext."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _package(root, **extra):
    root.mkdir()
    (root / "ui").mkdir()
    (root / "ui" / "index.html").write_text("<!doctype html><h1>Notes</h1>")
    (root / "backend.py").write_text("operations = {'save': lambda v, c: v, 'chunk': lambda v, c: v}\n")
    definition = {
        "id": "test.notes", "title": "Notes", "version": "1", "capabilities": ["storage.app"],
        "ui": {"root": "ui", "entry": "index.html"},
        "backend": {"kind": "python", "entry": "backend:operations"},
        "operations": {"save": {"agent": True, "input": {"type": "object"}},
                       "chunk": {"agent": False}},
        **extra,
    }
    (root / "application.json").write_text(json.dumps(definition))
    return definition


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.programs._host.ui import catalog
    # The backend's environment and import probe are covered by the
    # integration tests; here only the loader is under test.
    monkeypatch.setattr(catalog, "check_backend", lambda root, definition: None)
    from openprogram.interfaces.server.routes.catalog import applications
    from openprogram.interfaces.server.routes.control import resources
    app = FastAPI()
    applications.register(app)
    resources.register(app)
    with TestClient(app) as value:
        yield value


def test_install_records_the_source_and_copies_nothing(client, tmp_path):
    from openprogram.programs._host.ui import catalog
    source = tmp_path / "notes"
    _package(source)
    installed = client.post("/api/applications/install", json={"path": str(source), "trust": True})
    assert installed.status_code == 200, installed.text
    row = catalog.record("test.notes")
    assert row["source"] == row["path"] == str(source)
    assert not (catalog.home() / "versions").exists()
    listed = client.get("/api/applications").json()["applications"][0]
    assert listed["path"] == listed["source"] == str(source)
    assert listed["digest"] == catalog.package_digest(source) == installed.json()["digest"]


def test_reload_recomputes_the_digest_from_the_source(client, tmp_path):
    from openprogram.programs._host.ui import catalog, loader
    source = tmp_path / "notes"
    _package(source)
    first = client.post("/api/applications/install", json={"path": str(source), "trust": True}).json()["digest"]
    (source / "ui" / "index.html").write_text("<!doctype html><h1>Edited</h1>")
    assert loader.sync() == ["test.notes"]
    second = client.post("/api/applications/test.notes/open", json={}).json()
    assert second["application"]["digest"] == catalog.package_digest(source) != first
    assert "Edited" in client.get(second["ui_url"]).text
    assert loader.sync() == []


def test_a_catalog_row_of_the_copied_layout_loads_from_its_source(client, tmp_path):
    from openprogram.programs._host.ui import catalog, catalog_store, loader
    source = tmp_path / "notes"
    definition = _package(source)
    digest = catalog.package_digest(source)
    catalog_store.update(lambda rows: catalog_store.write([{
        "kind": "application", "path": str(catalog.home() / "versions" / digest), "source": str(source),
        "application": {**catalog.manifest(source), "digest": digest, "enabled": True, "source": str(source)},
    }]))
    assert definition["id"] in loader.sync()
    assert catalog.get("test.notes")["path"] == str(source)


def test_disable_disposes_ui_provider_and_entries(client, tmp_path):
    from openprogram.programs import DEFAULT_TOOLS
    from openprogram.programs._host.registry import program_tools
    from openprogram.programs._host.runtime import get
    from openprogram.host.resources import registry
    source = tmp_path / "notes"
    _package(source)
    assert client.post("/api/applications/install", json={"path": str(source), "trust": True}).status_code == 200
    opened = client.post("/api/applications/test.notes/open", json={}).json()

    entry = get("test_notes__save")
    assert entry is not None and entry._defer and entry._workflow_owner == "application:test.notes"
    assert entry.parameters["properties"]["input"] == {"type": "object"}
    assert "test_notes__save" not in DEFAULT_TOOLS
    assert get("test_notes__chunk") is None
    assert program_tools("application:test.notes") == ["test_notes__save"]
    assert registry.describe("application.test.notes")["actions"]["act"]
    assert client.get(opened["ui_url"]).status_code == 200

    assert client.patch("/api/applications/test.notes", json={"enabled": False}).status_code == 200
    assert get("test_notes__save") is None
    assert program_tools("application:test.notes") == []
    with pytest.raises(ValueError, match="unknown_resource_provider"):
        registry.describe("application.test.notes")
    assert client.get(opened["ui_url"]).status_code == 404
    assert client.get("/api/applications").json()["applications"][0]["enabled"] is False

    assert client.patch("/api/applications/test.notes", json={"enabled": True}).status_code == 200
    assert get("test_notes__save") is not None
    assert client.get(opened["ui_url"]).status_code == 200


def test_the_application_namespace_is_reserved_to_its_application():
    from openprogram.host.resources import ResourceProvider, ResourceRegistry
    registry = ResourceRegistry()
    provider = ResourceProvider("application.test.x", "X", {"list": {"type": "object"}}, lambda *_: {})
    with pytest.raises(ValueError, match="reserved"):
        registry.register(provider)
    with pytest.raises(ValueError, match="reserved"):
        registry.register(provider, owner="application:test.y")
    registry.register(provider, owner="application:test.x")
    assert registry.describe("application.test.x")["provider"] == "application.test.x"


def test_entry_names_are_valid_tool_names():
    from openprogram.programs._host.ui.loader import entry_name
    assert entry_name("local.notes", "summarize") == "local_notes__summarize"
    assert entry_name("file-analysis", "analyze") == "file-analysis__analyze"
    long = entry_name("a" * 90, "run")
    assert len(long) == 64 and long != entry_name("a" * 89 + "b", "run")


def test_a_builtin_application_package_mounts_through_the_same_path(tmp_path, monkeypatch):
    from openprogram.programs._host import registry as _registry
    from openprogram.programs._host.ui import catalog
    from openprogram.programs._host.runtime import get
    from openprogram.host.resources import registry
    root = tmp_path / "builtin"
    root.mkdir()
    definition = _package(root / "demo_notes")
    definition["id"] = "demo_notes"
    (root / "demo_notes" / "application.json").write_text(json.dumps(definition))
    monkeypatch.setattr(_registry, "_builtin_applications_root", lambda: str(root))
    assert _registry.builtin_application_names() == ["demo_notes"]
    ctx = _registry.load_builtin_application("demo_notes")
    try:
        assert ctx.owner == "application:demo_notes"
        assert catalog.get("demo_notes")["builtin"] is True
        assert get("demo_notes__save")._workflow_owner == "application:demo_notes"
        assert registry.describe("application.demo_notes")
        assert _registry.load_builtin_application("demo_notes") is ctx
    finally:
        _registry.dispose_program("application:demo_notes")
    assert get("demo_notes__save") is None
    with pytest.raises(FileNotFoundError):
        catalog.get("demo_notes")


def test_an_open_window_is_tracked_per_session_and_its_entries_stay_deferred(client, tmp_path):
    from openprogram.programs._host.runtime import (
        Tier, get, install_open_applications, split_tools_for_dispatch, tool_tier, _open_applications,
    )
    from openprogram.programs._host.ui.bindings import open_application_ids
    source = tmp_path / "notes"
    _package(source)
    assert client.post("/api/applications/install", json={"path": str(source), "trust": True}).status_code == 200
    entry = get("test_notes__save")
    assert tool_tier(entry) is Tier.DEFERRED
    assert split_tools_for_dispatch([entry])[0] == []

    opened = client.post("/api/applications/test.notes/open", json={"session_id": "chat-1"}).json()
    assert open_application_ids("chat-1") == {"test.notes"}
    assert open_application_ids("chat-2") == set()
    token = install_open_applications(open_application_ids("chat-1"))
    try:
        # An open window does not send the schemas: the model loads them
        # with tool_search, so the cached prefix stays the same.
        assert split_tools_for_dispatch([entry])[0] == []
    finally:
        _open_applications.reset(token)
    assert tool_tier(entry) is Tier.DEFERRED, "the registered tier never changes"

    closed = client.post("/api/applications/test.notes/close",
                         json={"instance_id": opened["instance_id"], "session_id": "chat-1"})
    assert closed.status_code == 200, closed.text
    assert open_application_ids("chat-1") == set()


def test_a_builtin_application_is_disabled_and_enabled_by_its_setting(client):
    from openprogram.programs._host import services as _services
    from openprogram.programs._host.registry import builtin_application_enabled
    assert _services.lookup("browser") is not None

    assert client.patch("/api/applications/browser", json={"enabled": False}).status_code == 200
    try:
        assert not builtin_application_enabled("browser")
        assert _services.lookup("browser") is None
        listed = {row["id"]: row for row in client.get("/api/applications").json()["builtin"]}
        assert listed["browser"] == {
            "id": "browser", "enabled": False, "loaded": False, "entries": [],
            "title": "Browser", "icon": "browser",
            "description": "The built-in web browser. The Agent uses it to read pages, fill forms and take screenshots.",
        }
    finally:
        assert client.patch("/api/applications/browser", json={"enabled": True}).status_code == 200
    assert _services.lookup("browser") is not None
    listed = {row["id"]: row for row in client.get("/api/applications").json()["builtin"]}
    assert listed["browser"]["enabled"] and listed["browser"]["loaded"]


def test_a_disabled_builtin_stays_disabled_when_the_registry_loads_again(client):
    from openprogram.programs._host import services as _services
    from openprogram.programs._host.ui import catalog
    from openprogram.programs._host.registry import load_program_modules, sync_builtin_application
    catalog.configure("browser", enabled=False)
    try:
        sync_builtin_application("browser")
        load_program_modules()
        assert _services.lookup("browser") is None
        assert catalog.builtin_settings("browser") == {"id": "browser", "enabled": False}
    finally:
        catalog.configure("browser", enabled=True)
        sync_builtin_application("browser")
    assert _services.lookup("browser") is not None


def test_a_tool_cannot_be_disabled(client):
    response = client.patch("/api/applications/goal", json={"enabled": False})
    assert response.status_code == 404


def test_the_office_editor_switch_provides_and_withdraws_its_service(client):
    from openprogram.programs._host import services as _services
    from openprogram.programs._host.registry import program_tools
    from openprogram.interfaces.server.routes.files import office_assets

    listed = {row["id"]: row for row in client.get("/api/applications").json()["builtin"]}
    assert listed["office_editor"]["title"] == "ONLYOFFICE" and listed["office_editor"]["icon"] == "office"
    assert listed["office_editor"]["entries"] == [] and program_tools("application:office_editor") == []
    assert office_assets.office_editor() is _services.lookup("office_editor") is not None
    try:
        assert client.patch("/api/applications/office_editor", json={"enabled": False}).status_code == 200
        assert office_assets.office_editor() is None
        assert "office_editor" not in _services.names()
        assert _services.lookup("browser") is not None, "the rest of the host keeps working"
    finally:
        assert client.patch("/api/applications/office_editor", json={"enabled": True}).status_code == 200
    assert office_assets.office_editor() is not None
