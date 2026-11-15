"""save_routine: a routine is a user Skill plus, optionally, a waking reminder."""

from __future__ import annotations

import pytest


@pytest.fixture
def routine_env(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.programs.tools.jobs.cron import cron as cron_tool
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.permissions.authority import local_owner_authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setenv("OPENPROGRAM_SCHEDULER_PATH", str(tmp_path / "scheduler" / "tasks.json"))
    monkeypatch.delenv(cron_tool.DEFAULT_CRON_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cron_tool, "_origin_session", lambda: ("sess-1", "main"))
    monkeypatch.setattr(cron_tool, "_authorize", lambda _action: (local_owner_authority(), None))
    return tmp_path


def _save(**kw):
    from openprogram.programs.tools.jobs.routine import save_routine

    args = {
        "name": "Weekly report",
        "description": "Summarise last week's merged pull requests for the team.",
        "steps": ["List merged pull requests since last Monday.", "Write a five-line summary."],
    }
    args.update(kw)
    return save_routine(**args)


def test_saves_a_skill_the_skill_tool_loads(routine_env):
    from openprogram.extensions.skills.loader import get_skill, resolve
    from openprogram.extensions.skills.tool import invoke

    reply = _save(corrections=["Group them by repository."])
    assert reply.startswith("Saved routine `routines/weekly-report`")
    assert "No schedule" in reply

    found = get_skill("routines/weekly-report")
    assert found is not None and found.source == "user"
    assert found.description.startswith("Saved routine. Summarise")
    assert "1. List merged pull requests" in found.body
    assert "- Group them by repository." in found.body
    assert resolve("weekly-report").name == "routines/weekly-report"
    assert "Write a five-line summary." in invoke("routines/weekly-report")


def test_saving_again_updates_the_same_skill_and_keeps_corrections(routine_env):
    from openprogram.extensions.skills.loader import get_skill

    _save(corrections=["Group them by repository."])
    reply = _save(steps=["List merged pull requests.", "Write a ten-line summary."])
    assert reply.startswith("Updated routine")
    body = get_skill("routines/weekly-report").body
    assert "ten-line" in body and "five-line" not in body
    assert "- Group them by repository." in body

    _save(corrections=["Group them by repository.", "Leave out bots."])
    body = get_skill("routines/weekly-report").body
    assert "- Leave out bots." in body and body.count("Group them by repository.") == 1


def test_cron_schedules_a_reminder_that_wakes_this_conversation(routine_env):
    from openprogram.extensions.skills.loader import get_skill
    from openprogram.runtime.scheduler import service

    reply = _save(cron="0 9 * * 1")
    tasks = service.list_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task["type"] == "recurring" and task["cron"] == "0 9 * * 1"
    assert task["wake"] is True and task["execution"]["session_id"] == "sess-1"
    assert "routines/weekly-report" in task["reminder"] and "skill tool" in task["reminder"]
    assert task["id"] in reply and "0 9 * * 1" in reply

    path = get_skill("routines/weekly-report").path
    text = open(path, encoding="utf-8").read()
    assert f"routine-task: {task['id']}" in text

    # A correction without cron keeps the schedule; a new cron moves it.
    reply = _save(corrections=["Leave out bots."])
    assert "schedule is unchanged" in reply and len(service.list_tasks()) == 1
    _save(cron="30 8 * * 1")
    tasks = service.list_tasks()
    assert len(tasks) == 1 and tasks[0]["id"] == task["id"] and tasks[0]["cron"] == "30 8 * * 1"


def test_deleted_schedule_is_recreated(routine_env):
    from openprogram.runtime.scheduler import service

    _save(cron="0 9 * * 1")
    service.delete_task(service.list_tasks()[0]["id"])
    _save(cron="0 9 * * 1")
    assert len(service.list_tasks()) == 1


def test_bad_input_and_refused_schedule_write_nothing(routine_env, monkeypatch):
    from openprogram.programs.tools.jobs.cron import cron as cron_tool
    from openprogram.programs.tools.jobs.routine import routine_path

    assert _save(name="  !! ").startswith("Error")
    assert _save(steps=[]).startswith("Error")
    assert _save(description=" ").startswith("Error")
    assert _save(cron="not a cron").startswith("Error")
    monkeypatch.setattr(cron_tool, "_authorize", lambda _action: ({}, "Error: no."))
    assert _save(cron="0 9 * * 1") == "Error: no."
    assert not routine_path("Weekly report").exists()


def test_capability_follows_the_schedule():
    from openprogram.runtime.permissions.tool_authority import capability_for_tool

    assert capability_for_tool("save_routine", {"name": "x"}) == "fs.write"
    assert capability_for_tool("save_routine", {"cron": "0 9 * * 1"}) == "schedule.create"


def test_registered_in_the_default_toolset_and_deferred():
    from openprogram.programs import DEFAULT_TOOLS, DEFERRED_DEFAULT_TOOLS
    from openprogram.programs._host.runtime import exposed_names

    assert "save_routine" in exposed_names()
    assert "save_routine" in DEFAULT_TOOLS and "save_routine" in DEFERRED_DEFAULT_TOOLS
