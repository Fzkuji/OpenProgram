"""A scheduled task's runs as Activity shows them: which task each run
belongs to, how it came out, why it did not finish, what it cost, and the
tips its line leaves, on data shaped like a real hourly monitor."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.scheduler import task_activity
from openprogram.runtime.scheduler.task_activity import (
    RunAnchor, TaskRun, Turn, failure_reason, outcome_of, page, read_runs, run_costs, run_tasks,
    summary, turn_from_payload,
)
from openprogram.state.store.session.graph.branches import runtime_started_heads, scheduled_heads
from openprogram.state.store.session.memory_index import SessionMemoryIndex
from openprogram.state.store.session.nodes import Call

SESSION = "local_monitor"
TASK = "5bc19070"
HOUR = 3600.0
T0 = 1_794_000_000.0
SCHEDULED = {"source": "scheduler"}


def add(index, *nodes):
    for node in nodes:
        index.append(node, predecessor=node.predecessor, caller=node.caller or None)


def run_nodes(index, name, after, at, reply="[nothing new]"):
    add(index,
        Call(id=name, role="user", created_at=at, predecessor=after, output="Check the two pages.", metadata=dict(SCHEDULED)),
        Call(id=f"{name}_reply", role="llm", created_at=at + 30, predecessor=name, output=reply, metadata=dict(SCHEDULED)))
    return f"{name}_reply"


def record(execution_id, status, created, terminal=None, *, reason=None, version=4, parent=None):
    return SimpleNamespace(execution_id=execution_id, status=SimpleNamespace(value=status), created_at=created,
                           terminal_at=terminal, reason_code=reason, status_version=version,
                           parent_execution_id=parent, session_id=SESSION)


def monitor():
    """The reader's turn, then a task line: two runs saved before turns
    named their task, a run cut off before its reply that the next run
    continued from its prompt, and two runs that name the task."""
    index = SessionMemoryIndex()
    add(index,
        Call(id="ask", role="user", created_at=T0, predecessor="ROOT", output="Watch the stock.", metadata={"source": "web"}),
        Call(id="ask_reply", role="llm", created_at=T0 + 5, predecessor="ask", output="Set up hourly."))
    tip = run_nodes(index, "r1", "ask_reply", T0 + HOUR)
    tip = run_nodes(index, "r2", tip, T0 + 2 * HOUR, reply="[could not check] The page asked for a captcha.")
    # r3 was cut off: its reply exists, and r4 continued from its prompt.
    run_nodes(index, "r3", tip, T0 + 3 * HOUR, reply="")
    tip = run_nodes(index, "r4", "r3", T0 + 4 * HOUR)
    tip = run_nodes(index, "r5", tip, T0 + 5 * HOUR, reply="The Nano tier is back in stock.")
    index.set_head("ask_reply")
    return index


ANCHORS = [
    RunAnchor("exec_5", "r5", Turn(source="scheduler", task_id=TASK)),
    RunAnchor("exec_4", "r4", Turn(source="scheduler", task_id=TASK)),
    RunAnchor("exec_3", "r3", Turn(source="scheduler")),
    RunAnchor("exec_2", "r2", Turn(source="scheduler")),
    RunAnchor("exec_1", "r1", Turn(source="scheduler")),
    RunAnchor("exec_ask", "ask", Turn(source="web")),
]


def test_a_turn_names_its_task_and_an_older_run_takes_its_lines():
    tasks = run_tasks(monitor(), ANCHORS)
    assert tasks == {"exec_5": TASK, "exec_4": TASK, "exec_3": TASK, "exec_2": TASK, "exec_1": TASK}


def test_the_branch_record_names_the_task_when_no_turn_does():
    index = monitor()
    index.set_meta(branch_refs={"branch_task": {"branch_id": "branch_task", "scheduled_task_id": "other",
                                                "head_id": "r5_reply", "status": "active"}})
    unnamed = [RunAnchor(anchor.execution_id, anchor.user_message_id, Turn(source=anchor.turn.source)) for anchor in ANCHORS]
    assert set(run_tasks(index, unnamed).values()) == {"other"}


def test_turn_payload_reads_the_task_the_label_and_the_source():
    turn = turn_from_payload({"kind": "chat", "request": {
        "source": "scheduler", "user_text": "Check\n the   pages", "scheduled_task": {"id": TASK, "title": "Stock"}}})
    assert turn == Turn(source="scheduler", task_id=TASK, label="Check the pages")
    assert turn_from_payload(None) == Turn()


def test_the_tips_a_task_line_leaves_are_the_tasks_not_the_readers():
    index = monitor()
    nodes = index.nodes_by_id
    heads = ["r5_reply", "r3_reply", "ask_reply"]
    assert scheduled_heads(nodes, heads) == {"r5_reply", "r3_reply"}
    # The selected branch is never a task's, whatever wrote on it.
    assert scheduled_heads(nodes, heads, kept={"r5_reply"}) == {"r3_reply"}


def test_reports_left_behind_are_not_a_branch_the_reader_made():
    index = monitor()
    add(index,
        Call(id="report_a1", role="llm", created_at=T0 + 9 * HOUR, predecessor="r2_reply", output="Back in stock."),
        Call(id="report_a2", role="llm", created_at=T0 + 10 * HOUR, predecessor="report_a1", output="Still in stock."))
    nodes = {key: SimpleNamespace(id=node.id, role="assistant" if node.role == "llm" else node.role,
                                  predecessor=node.predecessor, metadata=node.metadata)
             for key, node in index.nodes_by_id.items()}
    assert runtime_started_heads(nodes, ["report_a2", "r5_reply"]) == {"report_a2"}


@pytest.mark.parametrize(("status", "recorded", "reply", "expected"), [
    ("running", None, "", ("running", None)),
    ("paused", None, "", ("waiting", None)),
    ("failed", None, "", ("failed", None)),
    ("cancelled", None, "", ("stopped", None)),
    ("interrupted", None, "", ("interrupted", None)),
    ("completed", None, "[nothing new]", ("quiet", None)),
    ("completed", None, "", ("quiet", None)),
    ("completed", None, "The Nano tier is back.", ("notified", None)),
    ("completed", None, "[could not check] 403 on both pages.", ("unable", "403 on both pages.")),
    # The task's record wins: a reply that sent a file said something.
    ("completed", {"status": "result", "text": "x"}, "[nothing new]", ("notified", None)),
    ("completed", {"status": "failed", "text": "captcha"}, "anything", ("unable", "captcha")),
])
def test_outcome(status, recorded, reply, expected):
    assert outcome_of(status, recorded=recorded, reply=reply) == expected


def test_an_always_on_agent_is_judged_by_its_last_round():
    reply = "I will look at both pages now.\n\n[nothing new]"
    assert outcome_of("completed", reply=reply, last_round="[nothing new]", always_on=True) == ("quiet", None)
    assert outcome_of("completed", reply=reply, last_round="[nothing new]", always_on=False)[0] == "notified"


def test_why_a_run_did_not_finish_reads_in_both_languages():
    assert failure_reason("failed", "agent_runner_error", 3 * HOUR + 28 * 60) == {
        "en": "The agent runner stopped with an error after 3 h 28 min.",
        "zh": "智能体运行器出错，运行 3 小时 28 分 后停止。",
    }
    assert failure_reason("stopped", "time_limit", 3600)["en"] == "It reached its time limit and was stopped after 1 h."
    assert failure_reason("interrupted", "owner_lost_before_checkpoint", 50)["zh"].startswith("运行中 OpenProgram 停止了")
    assert failure_reason("failed", None, 42) == {"en": "It failed after 42 s.", "zh": "运行 42 秒 后失败。"}
    assert failure_reason("unable", None, 10, "验证码拦截。") == {"en": "验证码拦截。", "zh": "验证码拦截。"}
    assert failure_reason("quiet", None, 10) is None


def runs(*outcomes):
    return [TaskRun(execution_id=f"exec_{number}", task_id=TASK, started_at=T0 - number * HOUR,
                    ended_at=T0 - number * HOUR + 30, status="completed", reason_code=None, outcome=outcome)
            for number, outcome in enumerate(outcomes)]


def test_summary_counts_and_the_last_24_oldest_first():
    listed = runs("quiet", "failed", "notified", *["quiet"] * 30, "stopped")
    found = summary(listed)
    assert (found["runs"], found["unfinished"], found["notified"], found["last_at"]) == (34, 2, 1, T0)
    assert len(found["recent"]) == 24
    assert found["recent"][-3:] == ["notified", "failed", "quiet"]
    assert found["cost"] is None
    assert summary(listed, {"exec_0": 0.5, "exec_1": 0.25, "other": 9.0})["cost"] == 0.75


def test_a_page_keeps_one_filter_and_says_what_the_task_cost():
    listed = runs("quiet", "failed", "notified", "interrupted", "quiet", "stopped")
    unfinished = page(listed, kind="unfinished", offset=1, limit=1, costs={"exec_1": 0.5, "exec_4": 0.25})
    assert unfinished["total"] == 3
    assert [row["execution_id"] for row in unfinished["page"]] == ["exec_3"]
    assert unfinished["cost"] == 0.75
    assert unfinished["runs"] == 6
    notified = page(listed, kind="notified")
    assert [row["outcome"] for row in notified["page"]] == ["notified"]
    assert notified["page"][0]["reason"] is None
    assert page(listed, kind="all", costs={})["cost"] is None


def test_a_call_is_charged_to_its_run_its_helpers_run_or_the_run_going_then():
    top = [record("exec_a", "completed", T0, T0 + 100), record("exec_b", "completed", T0 + HOUR, T0 + HOUR + 100)]
    events = [
        {"ts": T0 + 10, "execution_id": "exec_a", "job_id": None, "cost": 0.5},
        {"ts": T0 + HOUR + 5, "execution_id": None, "job_id": None, "cost": 0.25},
        {"ts": T0 + 20, "execution_id": None, "job_id": "j_helper", "cost": 1.0},
        {"ts": T0 + 30, "execution_id": "j_helper", "job_id": None, "cost": 2.0},
        {"ts": T0 + 2 * HOUR, "execution_id": None, "job_id": None, "cost": 4.0},
        {"ts": T0 + 40, "execution_id": "exec_a", "job_id": None, "cost": None},
    ]
    assert run_costs(events, top, {"j_helper": "exec_a"}) == {"exec_a": 3.5, "exec_b": 0.25}


class Store:
    def __init__(self, records, inputs, turns):
        self.records, self.inputs, self.turns = records, inputs, turns

    def list_for_session(self, _session_id):
        return self.records

    def list_inputs_for_session(self, _session_id):
        return self.inputs

    def get_agent_turn_input(self, execution_id):
        return self.turns.get(execution_id)


def test_reading_a_conversation_gives_each_task_its_runs_newest_first(monkeypatch):
    from openprogram.runtime.scheduler import task_runs

    index = monitor()
    records = [
        record("exec_5", "completed", T0 + 5 * HOUR, T0 + 5 * HOUR + 40),
        record("exec_4", "completed", T0 + 4 * HOUR, T0 + 4 * HOUR + 30),
        record("exec_3", "failed", T0 + 3 * HOUR, T0 + 3 * HOUR + 60, reason="agent_runner_error"),
        record("exec_2", "completed", T0 + 2 * HOUR, T0 + 2 * HOUR + 30),
        record("exec_1", "completed", T0 + HOUR, T0 + HOUR + 30),
        record("exec_ask", "completed", T0, T0 + 5),
        record("j_helper", "failed", T0 + 3 * HOUR + 5, T0 + 3 * HOUR + 9, parent="exec_3"),
    ]
    inputs = [SimpleNamespace(execution_id=f"exec_{name}", user_message_id=f"r{name}", assistant_message_id=f"r{name}_reply")
              for name in range(1, 6)] + [SimpleNamespace(execution_id="exec_ask", user_message_id="ask", assistant_message_id="ask_reply")]
    turns = {f"exec_{name}": {"source": "scheduler", **({"scheduled_task": {"id": TASK}} if name >= 4 else {})} for name in range(1, 6)}
    turns["exec_ask"] = {"source": "web"}
    monkeypatch.setattr(task_runs, "recent", lambda _task_id: [{"execution_id": "exec_5", "status": "result", "text": "x"}])
    monkeypatch.setattr(task_activity, "_always_on", lambda _session_id: False)
    found = read_runs(SESSION, Store(records, inputs, turns), index)
    assert list(found) == [TASK]
    assert [(run.execution_id, run.outcome) for run in found[TASK]] == [
        ("exec_5", "notified"), ("exec_4", "quiet"), ("exec_3", "failed"), ("exec_2", "unable"), ("exec_1", "quiet")]
    assert found[TASK][3].note == "The page asked for a captcha."
    # A conversation the schedule never ran in is not read further.
    assert read_runs(SESSION, Store([records[5]], inputs[5:], {"exec_ask": {"source": "web"}}), index) == {}


def test_the_task_page_reads_one_page_of_one_filter(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.interfaces.server.routes.execution import activity_runs
    from openprogram.runtime.scheduler import service

    listed = runs("quiet", "failed", "quiet", "stopped")
    monkeypatch.setattr(activity_runs, "_may_read", lambda _request, _session_id: True)
    monkeypatch.setattr(service, "list_tasks", lambda: [{"id": TASK, "session_id": SESSION, "title": "Stock"}])
    monkeypatch.setattr(task_activity, "read_runs", lambda _session_id: {TASK: listed})
    monkeypatch.setattr(task_activity, "read_costs", lambda _session_id: {"exec_1": 0.5})
    app = FastAPI()
    activity_runs.register(app)
    client = TestClient(app)
    body = client.get(f"/api/session/{SESSION}/activity/tasks/{TASK}?filter=unfinished&limit=1").json()
    assert (body["task_id"], body["runs"], body["unfinished"], body["total"], body["cost"]) == (TASK, 4, 2, 2, 0.5)
    assert [row["outcome"] for row in body["page"]] == ["failed"]
    assert body["page"][0]["reason"] == {"en": "It failed after 30 s.", "zh": "运行 30 秒 后失败。"}
    assert client.get(f"/api/session/{SESSION}/activity/tasks/{TASK}?filter=everything").status_code == 404
    assert client.get(f"/api/session/other/activity/tasks/{TASK}").status_code == 404
