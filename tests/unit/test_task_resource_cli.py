from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _view(
    *,
    status: str = "running",
    resource_state: str = "live",
    known_cost: bool = False,
    reason_code: str | None = "budget.idle_exhausted",
) -> dict:
    return {
        "task_id": "task-1",
        "status": status,
        "resource_state": resource_state,
        "reason_code": reason_code,
        "reason_key": (
            f"resource.reason.{reason_code}" if reason_code else None
        ),
        "retryable": False,
        "limits": {
            "scheduler_capacity": 4,
            "limits": {
                "max_total_tokens": {
                    "configured": 100,
                    "effective": 100,
                    "source": "task",
                },
            },
        },
        "capacity": {
            "scheduler_capacity": 4,
            "session_live": {"used": 1, "limit": 2},
            "session_queued": {"used": 0, "limit": 3},
            "session_tasks": {"used": 2, "limit": 10},
            "queue_position": None,
        },
        "budget": {
            "scope": "task_with_shared_ancestors",
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
        },
    }


class _Resource:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_dict(self) -> dict:
        return self.payload


class _Runner:
    def __init__(self, before: dict, after: dict | None = None) -> None:
        self.before = before
        self.after = after or before
        self.cancelled: list[tuple[str, str | None]] = []

    def list_tasks(self, session_id: str):
        assert session_id == "session-1"
        return [SimpleNamespace(id="task-1")]

    def get_task_resource_view(self, task_id: str):
        assert task_id == "task-1"
        payload = self.after if self.cancelled else self.before
        return _Resource(payload)

    def cancel_task(self, task_id: str, *, reason: str | None = None):
        self.cancelled.append((task_id, reason))
        return SimpleNamespace(id=task_id)


@pytest.mark.parametrize(
    ("argv", "verb"),
    [
        (["subagent", "list", "--session", "session-1", "--json"], "list"),
        (["subagent", "show", "task-1", "--json"], "show"),
        (["subagent", "cancel", "task-1", "--json"], "cancel"),
    ],
)
def test_subagent_task_commands_parse(argv: list[str], verb: str) -> None:
    from openprogram.cli import build_parser

    args = build_parser().parse_args(argv)

    assert args.subagent_verb == verb
    assert args.json is True


def test_subagent_list_json_is_a_list_of_canonical_views(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    from openprogram._cli_cmds.subagent import _cmd_subagent_list

    assert _cmd_subagent_list("session-1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == [_view()]


def test_subagent_show_json_is_the_canonical_view(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    from openprogram._cli_cmds.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("task-1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == _view()


def test_subagent_cancel_returns_the_post_cancel_canonical_view(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    cancelled = _view(status="cancelled", reason_code="cancel.user")
    runner = _Runner(_view(), cancelled)
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    from openprogram._cli_cmds.subagent import _cmd_subagent_cancel

    assert _cmd_subagent_cancel("task-1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == cancelled
    assert runner.cancelled == [("task-1", "cancel.user")]


def test_subagent_human_view_shows_capacity_unknown_cost_and_reason(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    runner = _Runner(_view())
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    from openprogram._cli_cmds.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("task-1", as_json=False) == 0
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
        def get_task_resource_view(self, task_id: str):
            assert task_id == "task-1"
            return None

    runner = LegacyRunner(_view())
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    from openprogram._cli_cmds.subagent import _cmd_subagent_show

    assert _cmd_subagent_show("task-1", as_json=False) == 0
    assert "Unmetered" in capsys.readouterr().out
