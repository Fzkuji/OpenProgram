import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openprogram.agent.authority import local_owner_authority
from openprogram.context.nodes import Call
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverAck, DriverBinding, DriverRegistry
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.execution.store import ExecutionStore
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


class _PauseDriver:
    def __init__(self, executions):
        self.executions = executions
        self.pauses = []

    def capabilities(self):
        return CapabilitySet(pause=True)

    async def request_pause(self, handle, command_id: str) -> DriverAck:
        execution = self.executions.get_execution(handle["execution_id"])
        self.pauses.append((command_id, execution.status))
        assert execution.status is ExecutionStatus.PAUSING
        return DriverAck(command_id=command_id, attempt_id=handle["attempt_id"])

    async def request_cancel(self, handle, command_id: str) -> DriverAck:
        return DriverAck(command_id=command_id, attempt_id=handle["attempt_id"])

    async def activate(self, attempt, activation):
        del activation
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=self,
            handle={
                "execution_id": attempt.execution_id,
                "attempt_id": attempt.attempt_id,
            },
        )


def _running_execution(tmp_path):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "chat"})
    execution = executions.create_execution(
        execution_id="exec_live",
        run_id="run_live",
        session_id="parent",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True, safe_point_kinds=("action.after",), state_schema_version=1),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="worker_1", ttl_seconds=30, attempt_id="attempt_live",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, attempts, running, active


def test_page_pause_is_rejected_without_pausing_canonical_execution(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore
    from openprogram.webui.routes import processes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    executions, attempts, running, active = _running_execution(tmp_path)
    registry = DriverRegistry()
    driver = _PauseDriver(executions)
    registry.bind(DriverBinding(
        execution_id=running.execution_id, attempt_id=active.attempt_id,
        generation=active.generation, driver=driver,
        handle={"execution_id": running.execution_id, "attempt_id": active.attempt_id},
    ))
    service = RuntimeControlService(executions, attempts, registry)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.execution.control.default_control_service", lambda: service)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:live", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id=running.execution_id,
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    actor = local_owner_authority()
    monkeypatch.setattr(processes, "_authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(processes, "_actor_and_session", lambda request: (actor, None))
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        pause = client.post(
            "/api/session/parent/resources/page:live/control",
            json={"action": "pause", "command_id": "pause-live", "generation": 1},
        )
        assert pause.status_code == 400
        assert executions.get_execution(running.execution_id).status is ExecutionStatus.RUNNING
        assert driver.pauses == []
        stored = store.get_resource("page:live")
        assert stored["control_state"] == "idle"
        assert not stored.get("pause_command_id")

        # The canonical conversation control plane remains independent from
        # page resource controls.
        paused = asyncio.run(service.request_pause(
            command_id="conversation-pause",
            execution_id=running.execution_id,
            expected_version=running.status_version,
            actor={"surface": "conversation"},
        )).execution
        assert paused.status is ExecutionStatus.PAUSING
        _safe_point(service, executions, running, active, "conversation-pause")
        resumed = asyncio.run(service.request_continue(
            command_id="conversation-continue",
            execution_id=running.execution_id,
            expected_version=executions.get_execution(running.execution_id).status_version,
            actor={"surface": "conversation"},
            activator=driver.activate,
        )).execution
        assert resumed.status is ExecutionStatus.RUNNING
        assert store.get_resource("page:live")["control_state"] == "idle"


def _safe_point(service, executions, running, attempt, command_id):
    return service.arrive_safe_point(
        attempt_id=attempt.attempt_id, generation=attempt.generation,
        command_id=command_id,
        expected_execution_version=executions.get_execution(running.execution_id).status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "start", "phase": "after"},),
            state_refs={"program": {"cursor": 0}},
        ),
    )


def test_page_resume_is_rejected_without_resuming_canonical_execution(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import BrowserResourceStore, writes_fenced
    from openprogram.webui.routes import processes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    executions, attempts, running, active = _running_execution(tmp_path)
    registry = DriverRegistry()
    driver = _PauseDriver(executions)
    registry.bind(DriverBinding(
        execution_id=running.execution_id, attempt_id=active.attempt_id,
        generation=active.generation, driver=driver,
        handle={"execution_id": running.execution_id, "attempt_id": active.attempt_id},
    ))
    service = RuntimeControlService(
        executions, attempts, registry, activator=driver.activate,
    )
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.execution.control.default_control_service", lambda: service)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:live", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id=running.execution_id,
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    actor = local_owner_authority()
    monkeypatch.setattr(processes, "_authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(processes, "_actor_and_session", lambda request: (actor, None))
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        resume = client.post(
            "/api/session/parent/resources/page:live/control",
            json={"action": "resume", "command_id": "resume-1", "generation": 1},
        )
        assert resume.status_code == 400, resume.text
        stored = store.get_resource("page:live")
        assert stored["control_state"] == "idle"
        assert not stored.get("pause_command_id")
        assert writes_fenced("page:live") is False
        current = executions.get_execution(running.execution_id)
        assert current.status is ExecutionStatus.RUNNING


def test_retain_does_not_erase_title_when_binding_has_no_url(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore, retain_from_binding
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    owner = object()
    webtab.ensure_connection_revision(owner)
    binding_id = webtab.register_binding(owner, "win", "tab-a", "target-1")
    page_key = webtab.binding_page_key(binding_id)
    store.update_display(
        page_key, title="Plans overview",
        target="https://user:secret@example.test/p?token=1",
        connection_generation=int(
            webtab.binding_page_descriptor(binding_id).get("connection_generation") or 0
        ),
    )
    retain_from_binding(binding_id, "win", "tab-a", "target-1", 1)
    row = store.get_resource(page_key)
    assert row["title"] == "Plans overview"
    assert "token=" not in row["target"]
    assert "secret" not in row["target"]
    webtab.release_binding(binding_id)
