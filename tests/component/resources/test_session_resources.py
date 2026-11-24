from unittest.mock import patch

import pytest


def _scope(monkeypatch, executions, parents=lambda *args: {}):
    """Stand in for the conversation's execution scope."""
    monkeypatch.setattr(
        "openprogram.runtime.execution.conversation_scope.conversation_execution_scope",
        lambda *args, **kwargs: (tuple(executions(*args)), parents(*args)),
    )


def test_resource_use_keeps_exact_owner_and_releases_on_error(tmp_path, monkeypatch):
    from openprogram.host.resources.session import ResourceUseStore, resource_use
    from openprogram.runtime.execution.run_control import (
        set_current_session_id,
        reset_current_session_id,
    )

    store = ResourceUseStore(tmp_path / "resources.db")
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth-1")
    token = set_current_session_id("session-a")
    try:
        with pytest.raises(ValueError), resource_use("machine", "Lab VM", "https://user:secret@vm.test/api?token=secret", store=store):
            rows = store.list("session-a")
            assert len(rows) == 1
            assert rows[0]["target"] == "https://vm.test/api"
            assert store.list("session-b") == []
            raise ValueError("operation failed")
        assert store.list("session-a") == []
    finally:
        reset_current_session_id(token)


def test_dead_or_reused_owner_is_not_an_active_resource(tmp_path, monkeypatch):
    from openprogram.host.resources.session import ResourceUseStore

    store = ResourceUseStore(tmp_path / "resources.db")
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth-1")
    store.add("session-a", None, "machine", "Builder", "ubuntu:24.04")
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth-2")
    assert store.list("session-a") == []


@pytest.mark.parametrize("backend_kind", ["docker", "ssh"])
def test_code_backends_do_not_register_software_resources(tmp_path, monkeypatch, backend_kind):
    from openprogram.host.resources.session import ResourceUseStore
    from openprogram.host.backend.docker import DockerBackend
    from openprogram.host.backend.ssh import SshBackend
    from openprogram.runtime.execution.run_control import (
        set_current_session_id,
        reset_current_session_id,
    )

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth")
    store = ResourceUseStore()
    def run(*args, **kwargs):
        rows = store.list("session-a")
        assert rows == []
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, "ok", "")
    token = set_current_session_id("session-a")
    try:
        with patch(f"openprogram.host.backend.{backend_kind}.subprocess.run", run):
            backend = DockerBackend() if backend_kind == "docker" else SshBackend("example.test")
            assert backend.run("true", 1).exit_code == 0
        assert store.list("session-a") == []
    finally:
        reset_current_session_id(token)


def test_a_machine_target_reports_its_row_during_the_call(tmp_path, monkeypatch):
    from openprogram.host import machines
    from openprogram.host.resources.session import ResourceUseStore
    from openprogram.runtime.execution.run_control import (
        set_current_session_id,
        reset_current_session_id,
    )
    from openprogram.programs._host.execution import ToolReturn
    from openprogram.programs.tools.computer_use.computer_use import registry, tool
    from openprogram.programs.tools.computer_use.computer_use.contract import Described, Provider, Row

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth")

    class Lab(Provider):
        family, machine = "screen", "lab"
        capabilities = frozenset({"pixels", "pointer"})

        def describe(self):
            return Described("a virtual machine")

        def resource(self, subject):
            return Row("machine", "Lab VM", "http://vm.test:5000?secret=token")

        def act(self, subject, steps, call):
            rows = ResourceUseStore().list("session-lab")
            assert [(row["kind"], row["title"], row["target"]) for row in rows] == [
                ("machine", "Lab VM", "http://vm.test:5000")]
            return ToolReturn(text="ok")

    monkeypatch.setattr(registry, "providers", lambda conversation="": {"lab screen": Lab()})
    # A call runs only on a machine that is still set up and switched on.
    lab = machines.Machine(id="lab", name="Lab VM")
    monkeypatch.setattr(machines, "get_machine", lambda machine_id, cfg=None: lab if machine_id == "lab" else None)
    token = set_current_session_id("session-lab")
    try:
        result = tool.computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 2})
        assert result.text == '[device="lab", type="screen"]\nok'
        assert ResourceUseStore().list("session-lab") == []
    finally:
        reset_current_session_id(token)


def test_resource_route_denies_other_session_before_reading(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.execution import processes

    monkeypatch.setattr(processes, "_actor_and_session", lambda request: ({}, "allowed"))
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/other/resources")
    assert result.status_code == 404
    assert not (tmp_path / "session-resources.db").exists()


def test_resource_route_keeps_processes_in_activity_only(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.execution import processes
    from openprogram.host.resources.session import ResourceUseStore
    from openprogram.host.processes import ProcessStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth")
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: None)
    _scope(monkeypatch, lambda *args: [])
    checked = []
    monkeypatch.setattr(processes, "_authorize", lambda request, sid, record=None: checked.append((sid, record)))
    ResourceUseStore().add("allowed", None, "machine", "Lab VM", "http://vm.test/")
    store = ProcessStore()
    record = store.create(session_id="allowed", execution_id=None, tool_call_id=None,
                          command="sleep 100", cwd=None, backend_id="docker")
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/allowed/resources")
    assert result.status_code == 200
    assert {row["source"] for row in result.json()["items"]} == {"usage"}
    assert [(row["kind"], row["status"]) for row in result.json()["items"]] == [("machine", "attached")]
    assert store.get(record["id"])["status"] == "starting"
    assert len(checked) == 1
    assert result.headers["cache-control"] == "no-store"
    store.update(record["id"], status="exited")
    store.remove(record["id"])


def test_resource_route_reuses_scope_and_rejects_forged_owner(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from openprogram.interfaces.server.routes.execution import processes
    from openprogram.host.resources.session import ResourceUseStore

    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.host.resources.session.process_identity", lambda pid: "birth")
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: None)
    calls = []
    def scope(*args):
        calls.append(1)
        return [SimpleNamespace(execution_id="exec-child", session_id="child")]
    _scope(monkeypatch, scope)
    monkeypatch.setattr(processes, "_authorize", lambda *args: None)
    store = ResourceUseStore()
    for _ in range(20):
        store.add("child", "exec-child", "machine", "Lab VM", "http://vm.test/")
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/parent/resources")
        assert result.status_code == 200
        assert len(result.json()["items"]) == 20
        assert calls == [1]
        store.add("forged", "exec-child", "machine", "Lab VM", "http://vm.test/")
        assert client.get("/api/session/parent/resources").status_code == 404
