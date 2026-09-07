from unittest.mock import patch

import pytest


def test_resource_use_keeps_exact_owner_and_releases_on_error(tmp_path, monkeypatch):
    from openprogram.session_resources import ResourceUseStore, resource_use
    from openprogram.agent.run_control import set_current_session_id, reset_current_session_id

    store = ResourceUseStore(tmp_path / "resources.db")
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth-1")
    token = set_current_session_id("session-a")
    try:
        with pytest.raises(ValueError), resource_use("vm", "Test VM", "https://user:secret@vm.test/api?token=secret", store=store):
            rows = store.list("session-a")
            assert len(rows) == 1
            assert rows[0]["target"] == "https://vm.test/api"
            assert store.list("session-b") == []
            raise ValueError("operation failed")
        assert store.list("session-a") == []
    finally:
        reset_current_session_id(token)


def test_dead_or_reused_owner_is_not_an_active_resource(tmp_path, monkeypatch):
    from openprogram.session_resources import ResourceUseStore

    store = ResourceUseStore(tmp_path / "resources.db")
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth-1")
    store.add("session-a", None, "docker", "Docker", "ubuntu:24.04")
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth-2")
    assert store.list("session-a") == []


def test_docker_reports_use_only_while_running(tmp_path, monkeypatch):
    from openprogram.session_resources import ResourceUseStore
    from openprogram.backend.docker import DockerBackend
    from openprogram.agent.run_control import set_current_session_id, reset_current_session_id

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth")
    store = ResourceUseStore()
    def run(*args, **kwargs):
        rows = store.list("session-a")
        assert len(rows) == 1
        assert rows[0]["kind"] == "docker"
        assert rows[0]["target"] == "ubuntu:24.04"
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, "ok", "")
    token = set_current_session_id("session-a")
    try:
        with patch("openprogram.backend.docker.subprocess.run", run):
            assert DockerBackend().run("true", 1).exit_code == 0
        assert store.list("session-a") == []
    finally:
        reset_current_session_id(token)


def test_vm_adapter_reports_attachment_during_harness_call(tmp_path, monkeypatch):
    from openprogram.session_resources import ResourceUseStore
    from openprogram.agent.run_control import set_current_session_id, reset_current_session_id
    from openprogram.programs.gui_harness_bridge import install_gui_harness_web_use

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth")
    def harness(**kwargs):
        rows = ResourceUseStore().list("session-vm")
        assert len(rows) == 1
        assert rows[0]["kind"] == "vm"
        assert rows[0]["target"] == "http://vm.test:5000"
        return {"success": True}
    wrapped = install_gui_harness_web_use(harness)
    token = set_current_session_id("session-vm")
    try:
        result = wrapped.__wrapped__("inspect", surface="vm", vm_url="http://vm.test:5000?secret=token")
        assert result["status"] == "succeeded"
        assert ResourceUseStore().list("session-vm") == []
    finally:
        reset_current_session_id(token)


def test_resource_route_denies_other_session_before_reading(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes import processes

    monkeypatch.setattr(processes, "_actor_and_session", lambda request: ({}, "allowed"))
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/other/resources")
    assert result.status_code == 404
    assert not (tmp_path / "session-resources.db").exists()


def test_resource_route_authorizes_scope_once_and_combines_processes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes import processes
    from openprogram.session_resources import ResourceUseStore
    from openprogram.processes import ProcessStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth")
    monkeypatch.setattr("openprogram.execution.default_store", lambda: None)
    monkeypatch.setattr("openprogram.execution.conversation_scope.conversation_executions", lambda *args: [])
    checked = []
    monkeypatch.setattr(processes, "_authorize", lambda request, sid, record=None: checked.append((sid, record)))
    ResourceUseStore().add("allowed", None, "vm", "VM", "http://vm.test/")
    store = ProcessStore()
    record = store.create(session_id="allowed", execution_id=None, tool_call_id=None,
                          command="sleep 100", cwd=None, backend_id="docker")
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/allowed/resources")
    assert result.status_code == 200
    assert {row["source"] for row in result.json()["items"]} == {"usage", "process"}
    assert len(checked) == 1
    assert result.headers["cache-control"] == "no-store"
    store.update(record["id"], status="exited")
    store.remove(record["id"])


def test_resource_route_reuses_scope_and_rejects_forged_owner(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from openprogram.webui.routes import processes
    from openprogram.session_resources import ResourceUseStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.session_resources.process_identity", lambda pid: "birth")
    monkeypatch.setattr("openprogram.execution.default_store", lambda: None)
    calls = []
    def scope(*args):
        calls.append(1)
        return [SimpleNamespace(execution_id="exec-child", session_id="child")]
    monkeypatch.setattr("openprogram.execution.conversation_scope.conversation_executions", scope)
    monkeypatch.setattr(processes, "_authorize", lambda *args: None)
    store = ResourceUseStore()
    for _ in range(20):
        store.add("child", "exec-child", "vm", "VM", "http://vm.test/")
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/parent/resources")
        assert result.status_code == 200
        assert len(result.json()["items"]) == 20
        assert calls == [1]
        store.add("forged", "exec-child", "vm", "VM", "http://vm.test/")
        assert client.get("/api/session/parent/resources").status_code == 404
