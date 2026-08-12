from __future__ import annotations

import json
from types import SimpleNamespace

from openprogram.cli import build_parser


def _runner():
    view = SimpleNamespace(to_dict=lambda: {
        "task_id": "t1", "status": "running", "resource_state": "active",
    })
    return SimpleNamespace(
        list_tasks=lambda session_id, limit=None: [SimpleNamespace(id="t1")],
        get_task=lambda task_id: SimpleNamespace(id=task_id),
        get_task_resource_view=lambda task_id: view,
    )


def test_tasks_parser_has_list_and_get_routes() -> None:
    listed = build_parser().parse_args(["tasks", "list", "--session", "s1"])
    fetched = build_parser().parse_args(["tasks", "get", "t1"])

    assert (listed.command, listed.tasks_verb, listed.session_id) == (
        "tasks", "list", "s1",
    )
    assert (fetched.command, fetched.tasks_verb, fetched.task_id) == (
        "tasks", "get", "t1",
    )


def test_tasks_commands_print_canonical_resource_dto(monkeypatch, capsys) -> None:
    from openprogram._cli_cmds import tasks

    monkeypatch.setattr("openprogram.agent.task.get_runner", _runner)
    assert tasks._cmd_tasks_list("s1") == 0
    assert json.loads(capsys.readouterr().out)["tasks"][0]["task_id"] == "t1"

    assert tasks._cmd_tasks_get("t1") == 0
    assert json.loads(capsys.readouterr().out)["task"]["task_id"] == "t1"
