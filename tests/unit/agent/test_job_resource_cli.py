from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.execution import EventCursor, JobResourceDTO


def _view(
    *,
    status: str = "running",
    resource_state: str = "live",
    known_cost: bool = False,
    reason_code: str | None = "budget.idle_exhausted",
) -> dict:
    limits = {
        "scheduler_capacity": 4,
        "limits": {
            "max_total_tokens": {
                "configured": 100,
                "effective": 100,
                "source": "job",
            },
        },
    }
    capacity = {
        "scheduler_capacity": 4,
        "session_live": {"used": 1, "limit": 2},
        "session_queued": {"used": 0, "limit": 3},
        "session_jobs": {"used": 2, "limit": 10},
        "queue_position": None,
    }
    budget = {
        "scope": "job_with_shared_ancestors",
        "tokens": {"actual": 10, "reserved": 5, "limit": 100},
        "cost_usd": {
            "actual": None if not known_cost else "0.25",
            "reserved": "0.10",
            "limit": "1.00",
            "known": known_cost,
            "unknown_events": 1 if not known_cost else 0,
        },
        "runtime_seconds": {"used": 15.0, "limit": 60},
        "idle_seconds": {"used": 5.0, "limit": 20},
        "shared_remaining": {
            "tokens": 70,
            "cost_usd": None if not known_cost else "0.50",
            "cost_unknown_events": 1 if not known_cost else 0,
        },
    }
    return JobResourceDTO(
        job_id="job-1",
        execution_id="job-1",
        project_id="default",
        session_id="session-1",
        parent_execution_id=None,
        label="Research limits",
        subject="Research limits",
        prompt_summary="work",
        relation="owned",
        origin_turn_id=None,
        status=status,
        status_version=3,
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
        resource={
            "admission_id": "admission-job-1",
            "resource_state": resource_state,
            "queue_wait": None,
            "resource_lease_generation": 1,
            "owner_instance_id": "worker-1",
            "limits": limits,
            "usage": budget,
            "reservation": None,
        },
        event_cursor=EventCursor(
            execution_id="job-1", next_sequence=4, snapshot_status_version=3,
        ),
        execution={
            "execution_id": "job-1",
            "job_id": "job-1",
            "session_id": "session-1",
            "status": status,
            "status_version": 3,
            "resource": {
                "admission_id": "admission-job-1",
                "resource_state": resource_state,
            },
        },
        legacy={
            "resource_state": resource_state,
            "reason_code": reason_code,
            "reason_key": (
                f"resource.reason.{reason_code}" if reason_code else None
            ),
            "retryable": False,
            "limits": limits,
            "capacity": capacity,
            "budget": budget,
        },
    ).to_dict()


class _Resource:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_dict(self) -> dict:
        return self.payload


class _Runner:
    def __init__(self, before: dict) -> None:
        self.before = before

    def list_jobs(self, session_id: str):
        assert session_id == "session-1"
        return [SimpleNamespace(id="job-1")]

    def get_job_resource_view(self, job_id: str):
        assert job_id == "job-1"
        payload = self.before
        return _Resource(payload)


@pytest.mark.parametrize(
    ("argv", "verb"),
    [
        (["subagent", "list", "--session", "session-1", "--json"], "list"),
        (["subagent", "show", "job-1", "--json"], "show"),
    ],
)
def test_job_resource_commands_parse(argv: list[str], verb: str) -> None:
    from openprogram.cli import build_parser

    args = build_parser().parse_args(argv)

    assert args.subagent_verb == verb
    assert args.json is True


@pytest.mark.parametrize("verb", ["pause", "continue", "step", "cancel"])
def test_execution_control_commands_parse(verb: str) -> None:
    from openprogram.cli import build_parser

    args = build_parser().parse_args([
        "execution", verb, "job-1", "--expected-version", "3",
    ])

    assert args.execution_verb == verb
    assert args.execution_id == "job-1"
    assert args.expected_version == 3


def test_subagent_list_json_is_a_list_of_canonical_views(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    from openprogram.cli.commands.subagent import _cmd_subagent_list

    assert _cmd_subagent_list("session-1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == [_view()]


def test_subagent_show_json_is_the_canonical_view(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    from openprogram.cli.commands.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("job-1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == _view()


@pytest.mark.parametrize("operation", ["pause", "continue", "step", "cancel"])
def test_execution_control_sends_canonical_command_and_cursor(
    operation: str,
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    requests: list[tuple[str, str, dict]] = []
    canonical = _view(status="cancelled" if operation == "cancel" else "paused")

    def worker_request(method: str, path: str, body: dict):
        requests.append((method, path, body))
        return 200, {
            "command": {
                "command_id": f"cmd-{operation}",
                "execution_id": "job-1",
                "status": "applied",
            },
            "execution": canonical["execution"],
            "event_cursor": canonical["event_cursor"],
        }

    from openprogram.cli.commands import execution

    monkeypatch.setattr(execution, "_worker_request", worker_request)
    assert execution._cmd_execution_control(
        operation, "job-1", expected_version=3, command_id=f"cmd-{operation}",
    ) == 0
    capsys.readouterr()

    method, path, body = requests[0]
    assert method == "POST"
    assert path == f"/api/execution/{operation}"
    assert body == {
        "type": "execution.command",
        "action": f"execution.{operation}",
        "command_id": f"cmd-{operation}",
        "execution_id": "job-1",
        "expected_version": 3,
        "payload": {"reason_code": "cancel.user"}
        if operation == "cancel" else {},
    }
    assert canonical["event_cursor"] == {
        "execution_id": "job-1",
        "next_sequence": 4,
        "snapshot_status_version": 3,
    }


def test_subagent_human_view_shows_capacity_unknown_cost_and_reason(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    from openprogram.cli.commands.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("job-1", as_json=False) == 0
    output = capsys.readouterr().out

    assert "Session 1/2 live" in output
    assert "Tokens: 70" in output
    assert "Cost: Unknown (1 event)" in output
    assert "Runtime: 45s" in output
    assert "Idle: 15s" in output
    assert "Reason: budget.idle_exhausted" in output


def test_subagent_human_view_treats_missing_resource_as_unmetered(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    class LegacyRunner(_Runner):
        def get_job_resource_view(self, job_id: str):
            assert job_id == "job-1"
            return None

    runner = LegacyRunner(_view())
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    from openprogram.cli.commands.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("job-1", as_json=False) == 0
    assert "Unmetered" in capsys.readouterr().out
