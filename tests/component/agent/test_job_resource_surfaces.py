import asyncio
import json
from types import SimpleNamespace

from openprogram.agent.job.types import Job, JobStatus
from openprogram.execution import EventCursor, JobResourceDTO


class _WS:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class _ResourceView:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def to_dict(self) -> dict:
        resource = {
            "admission_id": "admission-job-1",
            "resource_state": "queued",
            "queue_wait": {
                "state": "queued",
                "reason_code": "quota.queue_full",
                "since": 1.0,
                "position": 2,
            },
            "resource_lease_generation": None,
            "owner_instance_id": None,
            "limits": {"scheduler_capacity": 4, "limits": {}},
            "usage": {},
            "reservation": None,
        }
        return JobResourceDTO(
            job_id=self.job_id,
            execution_id=self.job_id,
            project_id="default",
            session_id="session-1",
            parent_execution_id=None,
            label="work",
            subject="work",
            prompt_summary="work",
            relation="owned",
            origin_turn_id=None,
            status="queued",
            status_version=0,
            capabilities={
                "pause": True,
                "step": True,
                "steer": False,
                "fork": False,
                "retry": False,
                "safe_point_kinds": [],
                "state_schema_version": 1,
            },
            checkpoint_head_id=None,
            resource=resource,
            event_cursor=EventCursor(
                execution_id=self.job_id,
                next_sequence=1,
                snapshot_status_version=0,
            ),
            execution={
                "execution_id": self.job_id,
                "job_id": self.job_id,
                "session_id": "session-1",
                "status": "queued",
                "status_version": 0,
                "resource": resource,
            },
            legacy={
                "resource_state": "queued",
                "reason_code": "quota.queue_full",
                "reason_key": "resource.reason.quota.queue_full",
                "retryable": True,
                "limits": {"scheduler_capacity": 4, "limits": {}},
                "capacity": {
                    "scheduler_capacity": 4,
                    "session_live": {"used": 1, "limit": 2},
                    "session_queued": {"used": 2, "limit": 8},
                    "session_jobs": {"used": 3, "limit": 100},
                    "queue_position": 2,
                },
                "budget": {},
            },
        ).to_dict()


def _job(*, status: JobStatus = JobStatus.QUEUED) -> Job:
    return Job(
        id="job-1",
        parent_session_id="session-1",
        prompt="work",
        agent_id="main",
        status=status,
    )


def test_job_ws_list_and_get_return_the_canonical_resource_dto(
    monkeypatch,
) -> None:
    from openprogram.webui.ws_actions import job as job_actions

    queued = _job()
    class Runner:
        def __init__(self) -> None:
            self.resource_reads: list[str] = []

        def list_jobs(self, *_args, **_kwargs):
            return [queued]

        def get_job(self, _job_id):
            return queued

        def get_job_resource_view(self, job_id):
            self.resource_reads.append(job_id)
            return _ResourceView(job_id)

    runner = Runner()
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    ws = _WS()

    asyncio.run(job_actions.handle_list_jobs(ws, {"session_id": "session-1"}))
    asyncio.run(job_actions.handle_get_job(ws, {"job_id": "job-1"}))

    expected = _ResourceView("job-1").to_dict()
    assert ws.messages[0]["data"]["jobs"][0]["resource"] == expected["resource"]
    assert ws.messages[0]["data"]["jobs"][0]["event_cursor"] == expected["event_cursor"]
    assert ws.messages[1]["data"]["job"]["resource"] == expected["resource"]
    assert ws.messages[1]["data"]["job"]["event_cursor"] == expected["event_cursor"]
    assert runner.resource_reads == ["job-1", "job-1"]


def test_job_status_broadcast_uses_canonical_resource_and_omits_failed_read(
    monkeypatch,
) -> None:
    from openprogram.agent.job import runner as runner_module

    sent: list[dict] = []
    monkeypatch.setattr(runner_module, "_broadcast", sent.append)
    runner = runner_module.JobRunner.__new__(runner_module.JobRunner)
    job = _job()
    runner.get_job_resource_view = lambda job_id: _ResourceView(job_id)

    runner._broadcast_job_status(job)

    assert sent[-1]["data"]["resource"] == _ResourceView(job.id).to_dict()

    def fail(_job_id):
        raise RuntimeError("ledger unavailable")

    runner.get_job_resource_view = fail
    runner._broadcast_job_status(job)

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
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "4")

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


def test_spawn_rejection_transport_preserves_usage_without_inventing_job(
    monkeypatch,
) -> None:
    from openprogram.agent.resource_governance import (
        AdmissionDecision,
        AdmissionRejected,
    )
    from openprogram.webui.ws_actions import job as job_actions

    class Runner:
        def spawn_job(self, **_kwargs):
            raise AdmissionRejected(AdmissionDecision(
                accepted=False,
                job_id=None,
                reason_code="quota.queue_full",
                retryable=True,
                effective_limits={"max_queued_per_session": 2},
                capacity={"session_queued": {"used": 2, "limit": 2}},
                usage={"tokens": 7, "unknown_cost_events": 1},
            ))

    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: Runner())
    ws = _WS()

    asyncio.run(job_actions.handle_spawn_job(ws, {
        "session_id": "session-1",
        "prompt": "work",
        "context": "clean",
    }))

    data = ws.messages[0]["data"]
    assert data["status"] == "rejected"
    assert data["job_id"] is None
    assert data["usage"] == {"tokens": 7, "unknown_cost_events": 1}
