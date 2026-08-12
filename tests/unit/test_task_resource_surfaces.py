import asyncio
import json
from types import SimpleNamespace

from openprogram.agent.task.types import Task, TaskStatus


class _WS:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class _ResourceView:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": "queued",
            "resource_state": "queued",
            "reason_code": "quota.queue_full",
            "reason_key": "resource.reason.quota.queue_full",
            "retryable": True,
            "limits": {"scheduler_capacity": 4, "limits": {}},
            "capacity": {
                "scheduler_capacity": 4,
                "session_live": {"used": 1, "limit": 2},
                "session_queued": {"used": 2, "limit": 8},
                "session_tasks": {"used": 3, "limit": 100},
                "queue_position": 2,
            },
            "budget": {},
        }


def _task(*, status: TaskStatus = TaskStatus.QUEUED) -> Task:
    return Task(
        id="task-1",
        parent_session_id="session-1",
        prompt="work",
        agent_id="main",
        status=status,
    )


def test_task_ws_list_get_and_cancel_return_the_canonical_resource_view(
    monkeypatch,
) -> None:
    from openprogram.webui.ws_actions import task as task_actions

    queued = _task()
    cancelled = _task(status=TaskStatus.CANCELLED)

    class Runner:
        def __init__(self) -> None:
            self.resource_reads: list[str] = []

        def list_tasks(self, *_args, **_kwargs):
            return [queued]

        def get_task(self, _task_id):
            return queued

        def cancel_task(self, _task_id, *, reason=None):
            return cancelled

        def get_task_resource_view(self, task_id):
            self.resource_reads.append(task_id)
            return _ResourceView(task_id)

    runner = Runner()
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    ws = _WS()

    asyncio.run(task_actions.handle_list_tasks(ws, {"session_id": "session-1"}))
    asyncio.run(task_actions.handle_get_task(ws, {"task_id": "task-1"}))
    asyncio.run(task_actions.handle_cancel_task(ws, {"task_id": "task-1"}))

    expected = _ResourceView("task-1").to_dict()
    assert ws.messages[0]["data"]["tasks"][0]["resource"] == expected
    assert ws.messages[1]["data"]["task"]["resource"] == expected
    assert ws.messages[2]["data"]["resource"] == expected
    assert runner.resource_reads == ["task-1", "task-1", "task-1"]


def test_task_status_broadcast_uses_canonical_resource_and_omits_failed_read(
    monkeypatch,
) -> None:
    from openprogram.agent.task import runner as runner_module

    sent: list[dict] = []
    monkeypatch.setattr(runner_module, "_broadcast", sent.append)
    runner = runner_module.TaskRunner.__new__(runner_module.TaskRunner)
    task = _task()
    runner.get_task_resource_view = lambda task_id: _ResourceView(task_id)

    runner._broadcast_task_status(task)

    assert sent[-1]["data"]["resource"] == _ResourceView(task.id).to_dict()

    def fail(_task_id):
        raise RuntimeError("ledger unavailable")

    runner.get_task_resource_view = fail
    runner._broadcast_task_status(task)

    assert "resource" not in sent[-1]["data"]
    assert sent[-1]["data"]["status"] == "queued"


def test_tui_settings_keep_configured_value_and_add_session_effective_source(
    monkeypatch,
) -> None:
    from openprogram.agent.resource_governance import ResourceLimits
    from openprogram.webui.ws_actions import settings as settings_actions

    monkeypatch.setattr(
        "openprogram.config_schema._setup._read_config",
        lambda: {"agent": {"resource_limits": {"max_total_tokens": 100}}},
    )
    monkeypatch.setattr(
        "openprogram.agent.resource_governance.session_resource_limits",
        lambda _session_id: ResourceLimits(max_total_tokens=50),
    )
    ws = _WS()

    asyncio.run(settings_actions.handle_get_settings(
        ws, {"session_id": "session-1"},
    ))

    row = next(
        item for item in ws.messages[-1]["data"]
        if item["key"] == "agent.resource_limits.max_total_tokens"
    )
    assert row["value"] == 100
    assert row["effective"] == 50
    assert row["source"] == "session"


def test_resource_limit_rest_returns_resolved_fields_and_owner_puts_override(
    tmp_path, monkeypatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.agent.resource_governance import ResourceLimits
    from openprogram.agent.session_db import SessionDB
    from openprogram.webui.routes.config import register

    db = SessionDB(tmp_path / "sessions")
    db.create_session("session-1", "main")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.agent.resource_governance.global_resource_limits",
        lambda: ResourceLimits(
            max_live_per_session=3,
            max_total_tokens=100,
        ),
    )
    monkeypatch.setattr(
        "openprogram.agent.authority.owner_principal_id", lambda: "owner/id",
    )
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "4")

    app = FastAPI()
    app.state.owner_auth = SimpleNamespace(authority={
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "authority_tier": "owner",
        "interaction": "interactive",
        "principal_id": "owner/id",
    })
    register(app)
    client = TestClient(app)

    initial = client.get("/api/sessions/session-1/resource-limits")
    assert initial.status_code == 200
    initial_revision = initial.json()["revision"]
    assert initial.json()["limits"]["max_total_tokens"] == {
        "configured": 100,
        "effective": 100,
        "source": "global",
    }

    updated = client.put(
        "/api/sessions/session-1/resource-limits",
        json={
            "limits": {"max_total_tokens": 80},
            "base_revision": initial_revision,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] != initial_revision
    assert updated.json()["limits"]["max_total_tokens"] == {
        "configured": 80,
        "effective": 80,
        "source": "session",
    }

    stale = client.put(
        "/api/sessions/session-1/resource-limits",
        json={
            "limits": {"max_total_tokens": 70},
            "base_revision": initial_revision,
        },
    )
    assert stale.status_code == 409


def test_spawn_rejection_transport_preserves_usage_without_inventing_task(
    monkeypatch,
) -> None:
    from openprogram.agent.resource_governance import (
        AdmissionDecision,
        AdmissionRejected,
    )
    from openprogram.webui.ws_actions import task as task_actions

    class Runner:
        def spawn_task(self, **_kwargs):
            raise AdmissionRejected(AdmissionDecision(
                accepted=False,
                task_id=None,
                reason_code="quota.queue_full",
                retryable=True,
                effective_limits={"max_queued_per_session": 2},
                capacity={"session_queued": {"used": 2, "limit": 2}},
                usage={"tokens": 7, "unknown_cost_events": 1},
            ))

    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: Runner())
    ws = _WS()

    asyncio.run(task_actions.handle_spawn_task(ws, {
        "session_id": "session-1",
        "prompt": "work",
        "context": "clean",
    }))

    data = ws.messages[0]["data"]
    assert data["status"] == "rejected"
    assert data["task_id"] is None
    assert data["usage"] == {"tokens": 7, "unknown_cost_events": 1}
