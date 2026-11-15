"""A scheduled task keeps one branch: created once, then always continued."""
from __future__ import annotations

import json

import pytest

from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.scheduler import task_branch

TASK = {"id": "t1", "title": "VPS stock"}


def _run(db, sid, text, task=TASK):
    placement = task_branch.prepare(sid, task, db)
    db.append_message(sid, {"id": placement.msg_id, "role": "user", "content": "check", "source": "scheduler",
                            "predecessor": placement.parent, "timestamp": 1.0})
    reply = f"{placement.msg_id}_reply"
    db.append_message(sid, {"id": reply, "role": "assistant", "content": text, "source": "scheduler",
                            "predecessor": placement.msg_id, "timestamp": 2.0})
    db.set_head(sid, "a0")  # a background run leaves the head alone
    task_branch.finish(sid, task, placement, reply, db)
    return placement, reply


def _world(tmp_path):
    db = SessionDB(tmp_path / "sessions")
    sid = "local_s1"
    db.create_session(sid, "main", source="web")
    db.append_message(sid, {"id": "u0", "role": "user", "content": "hi", "predecessor": None, "timestamp": 0.1})
    db.append_message(sid, {"id": "a0", "role": "assistant", "content": "hello", "predecessor": "u0", "timestamp": 0.2})
    db.set_head(sid, "a0")
    return db, sid


def test_the_first_run_opens_the_branch_and_later_runs_continue_it(tmp_path):
    db, sid = _world(tmp_path)
    first, tip = _run(db, sid, "In stock!")
    assert first.parent == "a0" and first.tip is None
    assert task_branch.bound_tip(sid, "t1", db) == tip
    for _ in range(3):
        placement, new_tip = _run(db, sid, task_branch.QUIET_MARK)
        assert placement.parent == tip
        tip = new_tip
    tips = [row["head_msg_id"] for row in db.list_branches(sid)]
    assert sorted(tips) == sorted(["a0", tip])
    entries = {head: entry for head, entry in db.branch_entries(sid).items()
               if entry.get(task_branch.TASK_FIELD) == "t1"}
    assert list(entries) == [tip] and entries[tip]["name"] == "VPS stock"


def test_each_task_has_its_own_branch(tmp_path):
    db, sid = _world(tmp_path)
    _, one = _run(db, sid, "a")
    _, two = _run(db, sid, "b", {"id": "t2", "title": "Other"})
    assert task_branch.bound_tip(sid, "t1", db) == one and task_branch.bound_tip(sid, "t2", db) == two
    assert len(db.list_branches(sid)) == 3


def test_a_run_cut_off_halfway_does_not_open_another_branch(tmp_path):
    db, sid = _world(tmp_path)
    _, tip = _run(db, sid, "a")
    cut = task_branch.prepare(sid, TASK, db)
    db.append_message(sid, {"id": cut.msg_id, "role": "user", "content": "check", "source": "scheduler", "predecessor": tip, "timestamp": 3.0})
    db.set_head(sid, "a0")
    after, _ = _run(db, sid, "b")
    assert after.parent == cut.msg_id
    assert len(db.list_branches(sid)) == 2


def test_the_prompt_carries_the_last_result_that_said_something(tmp_path):
    db, sid = _world(tmp_path)
    _run(db, sid, "In stock at $5")
    placement, _ = _run(db, sid, task_branch.QUIET_MARK)
    next_run = task_branch.prepare(sid, TASK, db)
    note = task_branch.run_note(sid, next_run, db)
    assert "Check stock" not in note and "In stock at $5" in note and task_branch.QUIET_MARK in note
    assert placement.tip is not None


def _rounds_run(db, sid, text, rounds):
    placement = task_branch.prepare(sid, TASK, db)
    db.append_message(sid, {"id": placement.msg_id, "role": "user", "content": "check", "source": "scheduler",
                            "predecessor": placement.parent, "timestamp": 1.0})
    reply = f"{placement.msg_id}_reply"
    db.append_message(sid, {"id": reply, "role": "assistant", "content": text, "source": "scheduler",
                            "predecessor": placement.msg_id, "timestamp": 2.0,
                            "extra": json.dumps({"tool_calls": [], "blocks": [
                                {"type": "text", "text": said} for said in rounds]})})
    db.set_head(sid, "a0")
    task_branch.finish(sid, TASK, placement, reply, db)


@pytest.mark.parametrize("always_on_agent, previous", [("butler", "In stock at $5"), (None, "Checking.[nothing new]")])
def test_a_progress_line_before_the_quiet_mark_is_not_a_previous_result(tmp_path, monkeypatch, always_on_agent, previous):
    from openprogram.runtime.conversation import always_on

    monkeypatch.setattr(always_on, "agent_for_session", lambda _sid: always_on_agent)
    db, sid = _world(tmp_path)
    _run(db, sid, "In stock at $5")
    _rounds_run(db, sid, "Checking.[nothing new]", ["Checking.", "[nothing new]"])
    assert task_branch.last_result(sid, task_branch.bound_tip(sid, "t1", db), db) == previous


def test_the_main_line_stays_listed_while_the_task_branch_is_checked_out(tmp_path):
    db, sid = _world(tmp_path)
    _, tip = _run(db, sid, "a")
    db.set_head(sid, tip)
    assert sorted(row["head_msg_id"] for row in db.list_branches(sid)) == sorted(["a0", tip])


def test_quiet_replies():
    for text in ("[nothing new]", " 【无新内容】。", "nothing new."):
        assert task_branch.is_quiet(text)
    assert not task_branch.is_quiet("Nothing new except a price drop")


def test_a_run_that_could_not_check_gives_its_reason():
    assert task_branch.unable_reason("[could not check] V.PS 打不开") == "V.PS 打不开"
    assert task_branch.unable_reason("V.PS 有货") is None


def test_only_the_third_failure_in_a_row_counts_as_news_and_a_success_clears_it(tmp_path, monkeypatch):
    from openprogram.programs.tools.jobs.cron import cron
    from openprogram.runtime.scheduler import task_runs

    monkeypatch.setattr(cron, "_resolve_path", lambda: str(tmp_path / "tasks.json"))
    counts = [task_runs.record("t1", task_runs.FAILED, "403") for _ in range(4)]
    assert counts == [1, 2, 3, 4]
    assert task_runs.record("t1", task_runs.QUIET) == 0
    assert task_runs.record("t1", task_runs.FAILED, "403") == 1
    assert task_runs.recent("t1")[0]["status"] == task_runs.FAILED
    for _ in range(30):
        task_runs.record("t1", task_runs.QUIET)
    assert len(task_runs.recent("t1")) == task_runs.KEEP


def _note(monkeypatch, tmp_path, *, enabled=True, rows=()):
    from types import SimpleNamespace

    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation.always_on import store

    db, sid = _world(tmp_path)
    placement = task_branch.prepare(sid, TASK, db)
    spec = SimpleNamespace(id="watcher", always_on={"enabled": enabled})
    monkeypatch.setattr(manager, "get", lambda agent_id: spec if agent_id == "watcher" else None)
    monkeypatch.setattr(store, "load_preferences", lambda agent_id: list(rows))
    return lambda agent_id="watcher": task_branch.run_note(sid, placement, db, agent_id=agent_id)


def test_an_always_on_agents_preferences_reach_its_scheduled_runs(monkeypatch, tmp_path):
    note = _note(monkeypatch, tmp_path, rows=[{"id": "p1", "text": "Skip passing builds"}])()
    assert "- Skip passing builds" in note
    assert "leave out counts as nothing new" in note
    # The stopping rule the model must apply them to still follows.
    assert note.index("Skip passing builds") < note.index(task_branch.QUIET_MARK)


def test_a_normal_agents_scheduled_run_note_is_unchanged(monkeypatch, tmp_path):
    rows = [{"id": "p1", "text": "Skip passing builds"}]
    without_an_agent = _note(monkeypatch, tmp_path / "base", rows=rows)(None)
    assert "Skip passing builds" not in without_an_agent
    for note in (_note(monkeypatch, tmp_path / "off", enabled=False, rows=rows)(),
                 _note(monkeypatch, tmp_path / "gone", rows=rows)("deleted"),
                 _note(monkeypatch, tmp_path / "none", rows=[])()):
        assert note == without_an_agent
