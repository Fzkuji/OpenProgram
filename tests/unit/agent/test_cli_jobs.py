from __future__ import annotations

import json
from types import SimpleNamespace

from openprogram.cli import build_parser


def _runner():
    view = SimpleNamespace(to_dict=lambda: {
        "job_id": "t1", "status": "running", "resource_state": "active",
    })
    return SimpleNamespace(
        list_jobs=lambda session_id, limit=None: [SimpleNamespace(id="t1")],
        get_job=lambda job_id: SimpleNamespace(id=job_id),
        get_job_resource_view=lambda job_id: view,
    )


def test_jobs_parser_has_list_and_get_routes() -> None:
    listed = build_parser().parse_args(["jobs", "list", "--session", "s1"])
    fetched = build_parser().parse_args(["jobs", "get", "t1"])

    assert (listed.command, listed.jobs_verb, listed.session_id) == (
        "jobs", "list", "s1",
    )
    assert (fetched.command, fetched.jobs_verb, fetched.job_id) == (
        "jobs", "get", "t1",
    )


def test_jobs_commands_print_canonical_resource_dto(monkeypatch, capsys) -> None:
    from openprogram._cli_cmds import jobs

    monkeypatch.setattr("openprogram.agent.job.get_runner", _runner)
    assert jobs._cmd_jobs_list("s1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["job_id"] == "t1"

    assert jobs._cmd_jobs_get("t1", as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["job"]["job_id"] == "t1"

    assert jobs._cmd_jobs_get("t1") == 0
    text = capsys.readouterr().out
    assert "t1  running  resource=active" in text
    assert "capacity=" in text and "budget=" in text
