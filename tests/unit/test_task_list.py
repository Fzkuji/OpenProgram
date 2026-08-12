"""Unit tests for the task_list agentic function
(``openprogram/functions/agentics/task_list/``): the size decision that
decides whether to split at all, the serial loop, the retry-then-revise
path when the judge says no, resuming from the board, and the rule that
a revision never touches a completed item.

Two reuse boundaries are tested here because they are the point of the
design: list state IS the todo planning board (entries written by the
plain todo tools keep working alongside), and item completion IS
``evaluate_goal`` (no second judge).

Every LLM round goes through a module-level ``_run_*_turn`` seam (the
same shape ``goal`` uses), so the tests stub those and never reach a
provider."""
from __future__ import annotations

import json

import pytest

import openprogram.functions.agentics.task_list as TL
from openprogram.functions.tools.todo import shared


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A real todos.json on disk, driven through the shared storage
    layer — the same file the todo tools read and write."""
    path = tmp_path / "todos.json"
    monkeypatch.setattr(shared, "todos_path", lambda sid: path)
    monkeypatch.setattr(shared, "current_session_id", lambda: "s1")
    return path


def _read_board(path):
    return json.loads(path.read_text())["todos"] if path.exists() else []


@pytest.fixture
def always_pass(monkeypatch):
    """Goal judge that accepts every item."""
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (True, "ok"))


def _plan(*texts, split=True, reason="r", upstream=-1):
    return json.dumps({
        "split": split, "reason": reason,
        "items": [{"text": t, "done_when": f"{t} done",
                   "context_spec": {"upstream": upstream}} for t in texts],
    })


# ---------------------------------------------------------------------------
# Size decision — a small task is not split
# ---------------------------------------------------------------------------

def test_small_task_runs_as_one_item(monkeypatch, board, always_pass) -> None:
    prompts = []

    def _plan_turn(sid, prompt, *, agent_id, spawn_caller, label):
        prompts.append(prompt)
        return '{"split": false, "reason": "one pass is enough"}'

    runs = []
    monkeypatch.setattr(TL, "_run_planner_turn", _plan_turn)
    monkeypatch.setattr(TL, "_run_executor_turn",
                        lambda sid, p, **k: runs.append(p) or "did it")

    out = TL.run_task_list(task="rename one variable", session_id="s1")

    assert out["status"] == "completed"
    assert len(out["items"]) == 1                  # not split
    assert out["items"][0]["subject"] == "rename one variable"
    assert len(runs) == 1                          # exactly one executor run
    assert "Do NOT split" in prompts[0]            # the rule is in the prompt
    assert "<task>\nrename one variable\n</task>" in prompts[0]


def test_large_task_is_split_into_items(monkeypatch, board,
                                        always_pass) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("step one", "step two"))
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "done")

    out = TL.run_task_list(task="big task", session_id="s1")
    assert out["status"] == "completed"
    assert [it["subject"] for it in out["items"]] == ["step one", "step two"]
    assert all(it["status"] == TL.COMPLETED for it in out["items"])


# ---------------------------------------------------------------------------
# The list is the todo board
# ---------------------------------------------------------------------------

def test_items_are_ordinary_todo_board_entries(monkeypatch, board,
                                               always_pass) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("first", "second"))
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "ok")
    TL.run_task_list(task="t", session_id="s1")

    entries = _read_board(board)
    assert len(entries) == 2
    for e in entries:
        # Every field todo_create writes, so todo_list renders these.
        for field in ("id", "subject", "description", "status", "owner",
                      "blocked_by", "created_at", "updated_at"):
            assert field in e
        assert e["status"] in shared.STATUSES
        assert e["owner"] == TL.WORKFLOW_OWNER
        # …plus the three workflow fields.
        assert e["done_criteria"] and "upstream" in e["context_spec"]
        assert e["result_summary"] == "ok"


def test_todo_list_renders_a_running_workflow(monkeypatch, board) -> None:
    from openprogram.functions.tools.todo.todo_list.todo_list import (
        _todo_list_impl)

    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("write the parser"))
    rendered = []
    monkeypatch.setattr(
        TL, "_run_executor_turn",
        lambda *a, **k: rendered.append(_todo_list_impl()) or "ok")
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (True, "ok"))
    TL.run_task_list(task="t", session_id="s1")

    # Mid-run the board shows the item as in_progress, in the standard
    # todo_list format — no extra surface needed to watch a workflow.
    assert "in_progress:" in rendered[0]
    assert "#1 write the parser" in rendered[0]
    assert "completed:" in _todo_list_impl()


def test_hand_written_todos_survive_a_workflow(monkeypatch, board,
                                               always_pass) -> None:
    from openprogram.functions.tools.todo.todo_create.todo_create import (
        _todo_create_impl)

    _todo_create_impl("a todo the user wrote", "keep me")
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("workflow item"))
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "ok")
    TL.run_task_list(task="t", session_id="s1")

    entries = _read_board(board)
    mine = [e for e in entries if e["subject"] == "a todo the user wrote"]
    assert len(mine) == 1                       # untouched, still there
    assert mine[0]["status"] == "pending" and mine[0]["owner"] == ""
    # …and the workflow never adopted it as one of its items.
    assert len(TL.workflow_items("s1", "t")) == 1


def test_old_board_without_workflow_fields_still_loads(board) -> None:
    # A todos.json written before these fields existed: reading it must
    # not raise, and it contributes no workflow items.
    board.write_text(json.dumps({"version": 1, "todos": [
        {"id": "1", "subject": "legacy", "description": "", "status": "pending",
         "owner": "", "blocked_by": [], "created_at": 1.0, "updated_at": 1.0},
    ]}))
    assert shared.load("s1")[0]["subject"] == "legacy"
    assert TL.workflow_items("s1", "t") == []
    # The helpers tolerate an entry with no context_spec / criteria.
    legacy = shared.load("s1")[0]
    assert TL.render_range_for(legacy) == {"callers": 0, "subcalls": -1}
    assert TL._upstream_summaries([legacy], legacy) == ""


# ---------------------------------------------------------------------------
# Serial advance — items run in order, each with its own bounded context
# ---------------------------------------------------------------------------

def test_items_run_serially_with_upstream_handoff(monkeypatch, board,
                                                  always_pass) -> None:
    prompts = []
    monkeypatch.setattr(
        TL, "_run_planner_turn",
        lambda *a, **k: _plan("write the file", "test the file"))

    def _exec(sid, prompt, *, agent_id, spawn_caller, label):
        prompts.append(prompt)
        return f"result of item {len(prompts)}"

    monkeypatch.setattr(TL, "_run_executor_turn", _exec)
    out = TL.run_task_list(task="t", session_id="s1")

    assert len(prompts) == 2                      # strictly serial, one each
    assert "<upstream_results>" not in prompts[0]
    assert "result of item 1" in prompts[1]       # item 2 gets item 1's hand-off
    assert "<your_item>\nwrite the file\n</your_item>" in prompts[0]
    assert "<your_item>\ntest the file\n</your_item>" in prompts[1]
    assert out["items"][0]["result_summary"] == "result of item 1"


def test_upstream_zero_isolates_an_item(monkeypatch, board,
                                        always_pass) -> None:
    plan = json.dumps({"split": True, "reason": "r", "items": [
        {"text": "a", "done_when": "a", "context_spec": {"upstream": -1}},
        {"text": "b", "done_when": "b", "context_spec": {"upstream": 0}},
    ]})
    prompts = []
    monkeypatch.setattr(TL, "_run_planner_turn", lambda *a, **k: plan)
    monkeypatch.setattr(
        TL, "_run_executor_turn",
        lambda sid, p, **k: prompts.append(p) or f"summary {len(prompts)}")

    TL.run_task_list(task="t", session_id="s1")
    assert "<upstream_results>" not in prompts[1]   # upstream=0 → stands alone


def test_context_spec_maps_to_render_range() -> None:
    # The planner's per-item context budget is the same number that
    # bounds the DAG slice a nested call renders.
    assert TL.render_range_for({"context_spec": {"upstream": 0}}) == {
        "callers": 0, "subcalls": 0}
    assert TL.render_range_for({"context_spec": {"upstream": 2}}) == {
        "callers": 0, "subcalls": 2}
    assert TL.render_range_for({}) == {"callers": 0, "subcalls": -1}


def test_executor_applies_context_spec_at_agent_turn_boundary(monkeypatch) -> None:
    from openprogram.agent import sub_agent_run

    seen = []

    class Result:
        failed = False
        final_text = "done"
        error = None

    monkeypatch.setattr(
        sub_agent_run,
        "run_agent_turn",
        lambda *args, **kwargs: seen.append(kwargs) or Result(),
    )
    item = {"id": "2", "subject": "work", "done_criteria": "done",
            "context_spec": {"upstream": 2}}

    TL.execute_item("task", item, "", session_id="s1")

    assert seen[0]["render_range"] == {"callers": 0, "subcalls": 2}


# ---------------------------------------------------------------------------
# The judge is the goal judge
# ---------------------------------------------------------------------------

def test_judge_item_calls_evaluate_goal_with_the_criterion(monkeypatch) -> None:
    from openprogram.agent import goal as _goal
    seen = []

    def _fake(session_id, goal, *, agent_id, spawn_caller=None):
        seen.append((session_id, goal, agent_id))
        return ("met", "criterion satisfied", "", [])

    # Patch on the package object — the goal package's documented seam.
    monkeypatch.setattr(_goal, "evaluate_goal", _fake)
    passed, reason = TL.judge_item(
        "s1", {"done_criteria": "tests pass", "subject": "run tests"},
        agent_id="main")

    assert (passed, reason) == (True, "criterion satisfied")
    sid, goal_payload, agent_id = seen[0]
    assert sid == "s1" and agent_id == "main"
    assert goal_payload["text"] == "tests pass"   # done_criteria is the goal


@pytest.mark.parametrize("verdict", ["unmet", "needs_user", "judge_failure"])
def test_any_non_met_verdict_is_not_passed(monkeypatch, verdict) -> None:
    from openprogram.agent import goal as _goal
    monkeypatch.setattr(
        _goal, "evaluate_goal",
        lambda *a, **k: (verdict, "why", "q", []))
    passed, reason = TL.judge_item("s1", {"done_criteria": "x"},
                                   agent_id="main")
    assert passed is False and reason == "why"


# ---------------------------------------------------------------------------
# Judge says no — one retry, then back to the planner
# ---------------------------------------------------------------------------

def test_failed_item_is_retried_once(monkeypatch, board) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("hard step"))
    runs = []
    monkeypatch.setattr(TL, "_run_executor_turn",
                        lambda sid, p, **k: runs.append(p) or "attempt")
    verdicts = iter([(False, "missing the output file"), (True, "ok")])
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: next(verdicts))

    out = TL.run_task_list(task="t", session_id="s1")

    assert out["status"] == "completed"
    assert len(runs) == 2                          # one retry, not more
    assert out["items"][0]["attempts"] == 2
    assert "missing the output file" in runs[1]    # the retry knows why
    assert "<previous_attempt_failed>" in runs[1]


def test_two_failures_go_back_to_planner(monkeypatch, board) -> None:
    planner_calls = []

    def _planner(sid, prompt, *, agent_id, spawn_caller, label):
        planner_calls.append(prompt)
        if "<failed_item>" in prompt:
            return json.dumps({"reason": "split it", "items": [
                {"text": "smaller half", "done_when": "a",
                 "context_spec": {"upstream": 1}},
                {"text": "other half", "done_when": "b",
                 "context_spec": {"upstream": 1}}]})
        return _plan("too big")

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "tried")
    # The original item never passes; its replacements do.
    monkeypatch.setattr(
        TL, "judge_item",
        lambda sid, item, **k: (item["subject"] != "too big", "scope too large"))

    out = TL.run_task_list(task="t", session_id="s1")

    assert out["status"] == "completed"
    assert len(planner_calls) == 2                 # planned once, revised once
    assert "<why_it_failed>\nscope too large\n</why_it_failed>" in planner_calls[1]
    assert "[#1] too big" in planner_calls[1]
    assert [it["subject"] for it in out["items"]] == [
        "too big", "smaller half", "other half"]
    assert len(out["revisions"]) == 1
    assert out["revisions"][0]["reason"] == "split it"


# ---------------------------------------------------------------------------
# Revision leaves completed items alone
# ---------------------------------------------------------------------------

def test_revision_does_not_touch_completed_items(monkeypatch, board) -> None:
    def _planner(sid, prompt, *, agent_id, spawn_caller, label):
        if "<failed_item>" in prompt:
            return json.dumps({"reason": "restated", "items": [
                {"text": "replacement", "done_when": "r",
                 "context_spec": {"upstream": -1}}]})
        return _plan("works", "fails")

    monkeypatch.setattr(TL, "_run_planner_turn", _planner)
    monkeypatch.setattr(
        TL, "_run_executor_turn",
        lambda sid, p, **k: "the item 1 result" if "works" in p else "x")
    monkeypatch.setattr(
        TL, "judge_item",
        lambda sid, item, **k: (item["subject"] != "fails", "no good"))

    out = TL.run_task_list(task="t", session_id="s1")

    done_one = next(it for it in out["items"] if it["subject"] == "works")
    assert done_one["status"] == TL.COMPLETED
    assert done_one["result_summary"] == "the item 1 result"   # untouched

    rev = out["revisions"][0]
    assert rev["before"] == ["2"]              # the unfinished tail only
    assert "1" not in rev["before"]            # the completed item is not in it
    assert rev["after"] == ["3"]               # replaced by a fresh entry
    # The board agrees: item 1 keeps its result through the revision.
    on_disk = {e["id"]: e for e in _read_board(board)}
    assert on_disk["1"]["result_summary"] == "the item 1 result"
    assert on_disk["2"]["result_summary"].startswith("(revised away")


# ---------------------------------------------------------------------------
# Resume from the board
# ---------------------------------------------------------------------------

def test_board_is_checkpointed_as_each_item_starts(monkeypatch, board,
                                                   always_pass) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("one", "two"))
    seen = []

    def _exec(sid, prompt, *, agent_id, spawn_caller, label):
        seen.append([e["status"] for e in _read_board(board)])
        return "ok"

    monkeypatch.setattr(TL, "_run_executor_turn", _exec)
    TL.run_task_list(task="t", session_id="s1")

    assert seen[0] == ["in_progress", "pending"]
    assert seen[1] == ["completed", "in_progress"]   # item 1 already saved


def test_resume_skips_completed_items(monkeypatch, board) -> None:
    # A run killed after item 1: the board says 1 is done, 2 is not.
    board.write_text(json.dumps({"version": 1, "todos": [
        {"id": "1", "subject": "one", "description": "", "status": "completed",
         "owner": TL.WORKFLOW_OWNER, "blocked_by": [], "created_at": 1.0,
         "updated_at": 1.0, "task": "t", "done_criteria": "one",
         "context_spec": {"upstream": -1}, "result_summary": "already finished",
         "attempts": 1},
        {"id": "2", "subject": "two", "description": "", "status": "pending",
         "owner": TL.WORKFLOW_OWNER, "blocked_by": [], "created_at": 1.0,
         "updated_at": 1.0, "task": "t", "done_criteria": "two",
         "context_spec": {"upstream": -1}, "result_summary": "", "attempts": 0},
    ]}))
    runs = []
    monkeypatch.setattr(
        TL, "_run_planner_turn",
        lambda *a, **k: pytest.fail("resume must not re-plan"))
    monkeypatch.setattr(TL, "_run_executor_turn",
                        lambda sid, p, **k: runs.append(p) or "second result")
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (True, "ok"))

    out = TL.run_task_list(task="t", session_id="s1", resume=True)

    assert len(runs) == 1                        # only the unfinished item ran
    assert "<your_item>\ntwo\n</your_item>" in runs[0]
    assert "already finished" in runs[0]         # item 1's hand-off carried over
    assert out["status"] == "completed"


def test_resume_picks_up_an_item_left_in_progress(monkeypatch, board) -> None:
    # Killed mid-item: status stayed in_progress and must be retried.
    board.write_text(json.dumps({"version": 1, "todos": [
        {"id": "1", "subject": "interrupted", "description": "",
         "status": "in_progress", "owner": TL.WORKFLOW_OWNER, "blocked_by": [],
         "created_at": 1.0, "updated_at": 1.0, "task": "t",
         "done_criteria": "d", "context_spec": {"upstream": -1},
         "result_summary": "", "attempts": 1},
    ]}))
    runs = []
    monkeypatch.setattr(TL, "_run_executor_turn",
                        lambda sid, p, **k: runs.append(p) or "finished it")
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (True, "ok"))

    out = TL.run_task_list(task="t", session_id="s1")
    assert len(runs) == 1
    assert out["items"][0]["status"] == TL.COMPLETED


def test_resume_retries_interrupted_item_after_two_recorded_attempts(
    monkeypatch, board
) -> None:
    board.write_text(json.dumps({"version": 1, "todos": [
        {"id": "1", "subject": "interrupted", "description": "",
         "status": "in_progress", "owner": TL.WORKFLOW_OWNER,
         "blocked_by": [], "created_at": 1.0, "updated_at": 1.0,
         "task": "t", "done_criteria": "d",
         "context_spec": {"upstream": -1}, "result_summary": "",
         "attempts": 2},
    ]}))
    runs = []
    monkeypatch.setattr(TL, "_run_executor_turn",
                        lambda sid, p, **k: runs.append(p) or "finished")
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (True, "ok"))

    out = TL.run_task_list(task="t", session_id="s1", resume=True)

    assert len(runs) == 1
    assert out["items"][0]["status"] == TL.COMPLETED
    assert out["items"][0]["attempts"] == 1


def test_resume_ignores_another_tasks_entries(monkeypatch, board,
                                              always_pass) -> None:
    board.write_text(json.dumps({"version": 1, "todos": [
        {"id": "1", "subject": "old", "description": "", "status": "pending",
         "owner": TL.WORKFLOW_OWNER, "blocked_by": [], "created_at": 1.0,
         "updated_at": 1.0, "task": "an older task", "done_criteria": "old",
         "context_spec": {"upstream": -1}, "result_summary": "", "attempts": 0},
    ]}))
    monkeypatch.setattr(TL, "_run_planner_turn",
                        lambda *a, **k: _plan("fresh work"))
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "ok")

    out = TL.run_task_list(task="a new task", session_id="s1")
    assert [it["subject"] for it in out["items"]] == ["fresh work"]
    # The other task's entry is still on the board, untouched.
    assert any(e["subject"] == "old" and e["status"] == "pending"
               for e in _read_board(board))


# ---------------------------------------------------------------------------
# Hand-off summary length limit
# ---------------------------------------------------------------------------

def test_long_handoff_is_truncated_with_the_rule_stated() -> None:
    short = "concluded X; wrote /tmp/out.txt"
    assert TL.clip_handoff(short) == short

    clipped = TL.clip_handoff("y" * (TL.HANDOFF_SUMMARY_MAX_CHARS + 500))
    assert len(clipped) < TL.HANDOFF_SUMMARY_MAX_CHARS + 200
    assert "truncated" in clipped
    assert "artifact paths" in clipped           # tells the reader what to do


# ---------------------------------------------------------------------------
# Plan parsing
# ---------------------------------------------------------------------------

def test_parse_plan_accepts_fenced_json() -> None:
    out = TL._parse_plan(
        '```json\n{"split": true, "reason": "r", '
        '"items": [{"text": "do it"}]}\n```')
    assert out["split"] is True and out["items"][0]["text"] == "do it"


def test_new_item_fills_defaults(board) -> None:
    item = TL._new_item("s1", "t", {"text": "do it"}, "7")
    assert item["subject"] == "do it"
    assert item["done_criteria"] == "do it"      # falls back to the text
    assert item["context_spec"] == {"upstream": -1}
    assert item["status"] == TL.PENDING and item["attempts"] == 0
    assert item["owner"] == TL.WORKFLOW_OWNER and item["blocked_by"] == []


def test_parse_plan_rejects_malformed_replies() -> None:
    with pytest.raises(ValueError):
        TL._parse_plan("no json at all")
    with pytest.raises(ValueError):
        TL._parse_plan('{"split": "yes"}')       # split must be bool
    with pytest.raises(ValueError):
        TL._parse_plan('{"split": true, "items": []}')   # split with no items


@pytest.mark.parametrize("reply", [
    '{"reason": "missing"}',
    '{"reason": "wrong type", "items": {}}',
])
def test_revision_requires_explicit_items_array(monkeypatch, reply) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn", lambda *a, **k: reply)

    with pytest.raises(ValueError, match="items"):
        TL.revise_task_list("t", [], {"id": "1"}, "failed", "s1")


def test_malformed_revision_is_not_applied(monkeypatch, board) -> None:
    calls = 0

    def planner(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _plan("work") if calls == 1 else '{"reason": "missing"}'

    monkeypatch.setattr(TL, "_run_planner_turn", planner)
    monkeypatch.setattr(TL, "_run_executor_turn", lambda *a, **k: "attempt")
    monkeypatch.setattr(TL, "judge_item", lambda *a, **k: (False, "failed"))

    out = TL.run_task_list(task="t", session_id="s1")

    assert out["items"][0]["result_summary"].startswith("(failed:")
    assert out["revisions"][0]["reason"].startswith("revision failed:")
