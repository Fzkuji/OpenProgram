from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.cli.commands.jobs import job_resource_payload
from openprogram.programs.tools.agents.agent.list_jobs.list_jobs import _list_jobs_impl
from openprogram.agent.run_control import _current_session_id
from openprogram.agent.job.types import JobStatus
from openprogram.execution import EventCursor, JobResourceDTO
from openprogram.webui.ws_actions import job as ws_job


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


class _View:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def to_dict(self) -> dict:
        resource = {
            "admission_id": self.job_id,
            "resource_state": "active",
            "queue_wait": None,
            "resource_lease_generation": 1,
            "owner_instance_id": "worker-1",
            "limits": {},
            "usage": {},
            "reservation": None,
        }
        return JobResourceDTO(
            job_id=self.job_id,
            execution_id=self.job_id,
            project_id="default",
            session_id="s1",
            parent_execution_id=None,
            label="subject",
            subject="subject",
            prompt_summary="prompt",
            relation="owned",
            origin_turn_id=None,
            status="running",
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
                "session_id": "s1",
                "status": "running",
                "status_version": 0,
                "resource": resource,
            },
        ).to_dict()


class _Runner:
    def __init__(self) -> None:
        self.job = SimpleNamespace(
            id="t1", parent_session_id="s1", status=JobStatus.RUNNING,
            subject="subject", prompt="prompt",
            to_dict=lambda: {
                "id": "t1", "parent_session_id": "s1", "status": "running",
            },
        )

    def list_jobs(self, session_id, status_filter=None, limit=None):
        return [self.job]

    def get_job(self, job_id):
        return self.job if job_id == "t1" else None

    def get_job_resource_view(self, job_id):
        return _View(job_id)


@pytest.mark.parametrize(
    ("handler", "command", "frame_type", "resource_path"),
    [
        (ws_job.handle_list_jobs, {"session_id": "s1"}, "jobs_list", ("jobs", 0)),
        (ws_job.handle_get_job, {"job_id": "t1"}, "job", ("job",)),
    ],
)
def test_web_job_surfaces_embed_canonical_resource_view(
    monkeypatch, handler, command, frame_type, resource_path,
) -> None:
    runner = _Runner()
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    ws = _WS()

    asyncio.run(handler(ws, command))

    frame = next(item for item in ws.frames if item["type"] == frame_type)
    payload = frame["data"]
    for key in resource_path:
        payload = payload[key]
    expected = runner.get_job_resource_view("t1").to_dict()
    assert payload["resource"] == expected["resource"]
    assert payload["event_cursor"] == expected["event_cursor"]


def test_web_model_and_cli_share_the_same_resource_dto(monkeypatch) -> None:
    runner = _Runner()
    expected = runner.get_job_resource_view("t1").to_dict()
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)

    ws = _WS()
    asyncio.run(ws_job.handle_list_jobs(ws, {"session_id": "s1"}))
    web_resource = ws.frames[0]["data"]["jobs"][0]

    token = _current_session_id.set("s1")
    try:
        model_resource = _list_jobs_impl().details["jobs"][0]["resource"]
    finally:
        _current_session_id.reset(token)

    cli_resource = job_resource_payload(session_id="s1")["jobs"][0]
    for key in (
        "job_id", "execution_id", "status", "capabilities", "resource",
        "event_cursor", "execution",
    ):
        assert (
            web_resource[key]
            == model_resource[key]
            == cli_resource[key]
            == expected[key]
        )
