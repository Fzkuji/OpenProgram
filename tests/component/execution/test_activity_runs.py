"""Activity's run list and per-run events, on data shaped like a real monitor."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.execution.projections import activity_runs
from openprogram.runtime.execution.projections.activity_runs import (
    ChartReads,
    ExecutionReads,
    RunExecution,
    list_runs,
    list_task_runs,
    run_events,
)
from openprogram.runtime.jobs.types import Job, JobStatus
from openprogram.state.store.session.memory_index import SessionMemoryIndex
from openprogram.state.store.session.nodes import Call

SESSION = "local_monitor"
HOUR = 3600.0
T0 = 1_794_000_000.0
BRIEF = "Read the two product pages and report the cheapest tier."


@pytest.fixture(autouse=True)
def clear_chain_cache():
    activity_runs._chains.clear()
    activity_runs._chart_steps.clear()


def add(index, *nodes):
    for node in nodes:
        index.append(node, predecessor=node.predecessor, caller=node.caller or None)


def execution(execution_id, status, *, created, terminal=None, version=8, reason=None, checkpoint=None, parent=None,
              source_checkpoint=None):
    return SimpleNamespace(
        execution_id=execution_id, status=SimpleNamespace(value=status), created_at=created, terminal_at=terminal,
        status_version=version, reason_code=reason, checkpoint_head_id=checkpoint, parent_execution_id=parent,
        source_checkpoint_id=source_checkpoint,
    )


def attempt(execution_id, generation, *, leased, activated=None, ended=None, outcome=None, failure_text=None):
    return SimpleNamespace(
        attempt_id=f"{execution_id}-{generation}", execution_id=execution_id, generation=generation,
        status=SimpleNamespace(value="ended" if ended else "active"), leased_at=leased, activated_at=activated,
        ended_at=ended, outcome=outcome, failure_text=failure_text,
    )


def monitor():
    """Eight runs: the shapes the stored monitor conversation really holds."""
    index = SessionMemoryIndex()
    t = T0
    add(index,
        Call(id="u_empty", role="user", created_at=t, predecessor="ROOT", output="Watch the page.",
             metadata={"source": "scheduler"}),
        Call(id="a_empty", role="llm", created_at=t + 2, predecessor="u_empty", output="",
             metadata={"status": "completed", "finished_at": t + 9}),
        Call(id="report_ecbfda0a8aa1", role="llm", created_at=t + HOUR, predecessor="a_empty",
             output="The Nano tier is back in stock."),
    )
    t += 2 * HOUR
    add(index,
        Call(id="u_never", role="user", created_at=t, predecessor="report_ecbfda0a8aa1",
             output="Always-on check-in: anything new?"),
        Call(id="a_never", role="llm", created_at=t + 1, predecessor="u_never", output="[could not check] proxy down",
             metadata={"status": "completed", "finished_at": t + 44}),
        Call(id="s_job_missing", role="code", name="computer_use_agent", created_at=t + 3, caller="a_never",
             input=json.dumps({"context": "fresh", "brief": BRIEF}),
             output="[agent error: head=None] canonical execution admission is missing",
             metadata={"is_error": "True"}),
        Call(id="s_no_job", role="code", name="agent", created_at=t + 5, caller="a_never",
             input=json.dumps({"prompt": "Check the second page."}),
             output="agent refused: generation limit", metadata={"is_error": True}),
    )
    t += HOUR
    long_result = "r" * 9000
    add(index,
        Call(id="u_ran", role="user", created_at=t, predecessor="a_never", output="Is the page still refusing me?"),
        Call(id="a_ran", role="llm", created_at=t + 1, predecessor="u_ran", output="**Yes**, HTTP 403.",
             metadata={"status": "completed", "finished_at": t + 90}),
        Call(id="s_guide", role="code", name="computer_use_agent", created_at=t + 4, caller="a_ran",
             input=json.dumps({"target": "browser", "note": "n" * 2500}), output=long_result,
             metadata={"tool_call_id": "call_1", "spilled": {"path": "x"}}),
        Call(id="s_failed", role="code", name="bash", created_at=t + 6, caller="a_ran",
             input={"command": "false"}, output="exit 1", metadata={"is_error": "True"}),
        Call(id="s_agent", role="code", name="agent", created_at=t + 8, caller="a_ran",
             input=json.dumps({"prompt": BRIEF}), output="done"),
        Call(id="s_hidden", role="code", name="internal", created_at=t + 9, caller="a_ran",
             output="x", metadata={"structural": True}),
        Call(id="s_attach", role="code", name="attach", created_at=t + 10, caller="a_ran", output="x"),
        # The sub-agent's own branch, one nested sub-agent below it.
        Call(id="u_sub", role="user", created_at=t + 8, predecessor=None, output=BRIEF),
        Call(id="a_sub", role="llm", created_at=t + 9, predecessor="u_sub", output="Sold out."),
        Call(id="s_nested", role="code", name="agent", created_at=t + 10, caller="a_sub",
             input=json.dumps({"prompt": "Look closer."}), output="ok"),
    )
    t += HOUR
    add(index,
        Call(id="u_retried", role="user", created_at=t, predecessor="a_ran", output="Check again."),
        Call(id="a_retried", role="llm", created_at=t + 8, predecessor="u_retried", output="[nothing new]",
             metadata={"status": "running"}),
    )
    t += HOUR
    add(index,
        Call(id="u_paused", role="user", created_at=t, predecessor="a_retried", output="And now?"),
        Call(id="a_paused", role="llm", created_at=t + 1, predecessor="u_paused", output="Looking.",
             metadata={"status": "paused"}),
        # The reply a retry replaced: a sibling off the branch.
        Call(id="a_replaced", role="llm", created_at=t + 2, predecessor="u_paused", output="Older answer."),
    )
    t += HOUR
    add(index, Call(id="u_waiting", role="user", created_at=t, predecessor="a_paused", output="Last one."))
    index.set_head("u_waiting")
    return index


def monitor_reads():
    t = T0 + 3 * HOUR
    links = [
        RunExecution(execution("exec_retried", "failed", created=T0 + 4 * HOUR + 1, terminal=T0 + 4 * HOUR + 104,
                               reason="agent_runner_error", checkpoint="ckpt_head"), "u_retried", "a_retried"),
        RunExecution(execution("exec_paused", "paused", created=T0 + 5 * HOUR, version=3), "u_paused", "a_paused"),
        RunExecution(execution("exec_replaced", "completed", created=T0 + 5 * HOUR - 1, terminal=T0 + 5 * HOUR + 3),
                     "u_paused", "a_replaced"),
        RunExecution(execution("exec_waiting", "running", created=T0 + 6 * HOUR + 1), "u_waiting", None),
        RunExecution(execution("exec_program", "completed", created=T0 + 6 * HOUR + 5, terminal=T0 + 6 * HOUR + 6)),
        RunExecution(execution("j_child", "completed", created=t, terminal=t + 1, parent="exec_retried"), "u_sub", "a_sub"),
    ]
    attempts = {
        "exec_retried": [
            attempt("exec_retried", 1, leased=T0 + 4 * HOUR + 2, activated=T0 + 4 * HOUR + 3,
                    ended=T0 + 4 * HOUR + 90, outcome="restart_recoverable"),
            attempt("exec_retried", 2, leased=T0 + 4 * HOUR + 91, activated=T0 + 4 * HOUR + 92,
                    ended=T0 + 4 * HOUR + 104, outcome="failed", failure_text="RuntimeError: provider dropped the stream"),
        ],
        "exec_paused": [attempt("exec_paused", 1, leased=T0 + 5 * HOUR + 1, ended=T0 + 5 * HOUR + 30,
                                outcome="restart_recoverable")],
    }
    return ExecutionReads(
        links, lambda key: attempts.get(key, []), lambda key: 11 if key == "exec_retried" else None,
        lambda key: {"exec_retried-1": "ckpt_1"} if key == "exec_retried" else {},
    )


def jobs():
    t = T0 + 3 * HOUR
    return [
        Job(id="j_ea89b270c19946", parent_session_id=SESSION, prompt=f"You are a browser agent. {BRIEF}", agent_id="butler",
            context_mode="clean", caller_msg_id="a_never", label="computer use agent", status=JobStatus.ERRORED,
            created_at=T0 + 2 * HOUR + 3, completed_at=T0 + 2 * HOUR + 5, error="canonical execution admission is missing"),
        Job(id="j_ran", parent_session_id=SESSION, prompt=BRIEF, agent_id="butler", context_mode="clean",
            caller_msg_id="a_ran", spawn_caller="a_ran|node:s_agent", label="stock check", status=JobStatus.COMPLETED,
            created_at=t + 8, started_at=t + 8.5, completed_at=t + 40, head_id="a_sub", result_text="Sold out."),
        Job(id="j_nested", parent_session_id=SESSION, prompt="Look closer.", agent_id="butler", context_mode="clean",
            caller_msg_id="s_nested", status=JobStatus.COMPLETED, created_at=t + 11, started_at=t + 12,
            completed_at=t + 20, head_id="a_nested", result_text="ok"),
    ]


def rows_by_id(index=None, reads=None, **kwargs):
    index = index or monitor()
    page = list_runs(index, SESSION, reads or monitor_reads(), offset=0, limit=100, **kwargs)
    return {row["id"]: row for row in page["runs"]}, page


def events_of(run_id, kinds=None):
    body = run_events(monitor(), run_id, monitor_reads(), jobs=jobs())
    assert body is not None
    return [event for event in body["events"] if kinds is None or event["kind"] in kinds]


def test_the_chart_gets_every_runs_steps_in_short_form():
    page = list_runs(monitor(), SESSION, monitor_reads(), chart=ChartReads(jobs()))
    row = next(item for item in page["runs"] if item["id"] == "a_ran")
    assert all(event["kind"] != "asked" for event in row["steps"])
    guide = next(event for event in row["steps"] if event.get("node_id") == "s_guide")
    failed = next(event for event in row["steps"] if event.get("node_id") == "s_failed")
    assert len(guide["result"]) == activity_runs.CHART_RESULT_PEEK and len(guide["input"]["note"]) == activity_runs.CHART_VALUE_CHARS
    assert failed["failed"] is True and failed["result"] == "exit 1"
    [agent] = [event for event in row["steps"] if event["kind"] == "subagent"]
    assert [item.get("name") for item in agent["steps"]] == ["agent", None]
    assert agent["steps"][-1] == {"kind": "replied", "at": agent["steps"][-1]["at"], "text": "Sold out."}


def test_only_what_the_user_sent_gets_a_topic():
    asks = activity_runs.asks_on_branch(monitor(), SESSION, "a_ran")
    assert [ask.message_id for ask in asks] == ["u_ran"]


def test_lists_message_runs_and_execution_runs_newest_first():
    rows, page = rows_by_id()
    assert [row["id"] for row in page["runs"]] == [
        "exec_program", "exec_waiting", "a_paused", "a_replaced", "a_retried", "a_ran", "a_never",
        "report_ecbfda0a8aa1", "a_empty",
    ]
    assert page["total"] == 9
    assert rows["a_empty"]["asked_by"] == "schedule"
    assert rows["a_never"]["asked_by"] == "check-in"
    assert rows["a_ran"]["asked_by"] == "you"
    assert rows["report_ecbfda0a8aa1"]["is_report"] is True
    assert rows["report_ecbfda0a8aa1"]["asked_by"] == "none"
    assert rows["a_ran"]["execution_id"] is None and rows["a_ran"]["execution_status"] is None


def test_older_message_only_branch_needs_no_execution_rows():
    empty = ExecutionReads((), lambda _: [], lambda _: None)
    index = monitor()
    index.set_head("a_ran")
    page = list_runs(index, SESSION, empty)
    assert [row["id"] for row in page["runs"]] == ["a_ran", "a_never", "report_ecbfda0a8aa1", "a_empty"]
    assert page["runs"][0]["started_at"] == T0 + 3 * HOUR
    assert page["runs"][0]["ended_at"] == T0 + 3 * HOUR + 90
    assert page["runs"][0]["execution_status"] is None


def test_paused_run_has_no_end_and_keeps_its_attempt():
    rows, _ = rows_by_id()
    paused = rows["a_paused"]
    assert (paused["execution_status"], paused["message_status"], paused["ended_at"]) == ("paused", "paused", None)
    assert paused["started_at"] == T0 + 5 * HOUR
    assert paused["event_count"] == 1
    assert paused["revision"] == "3:-:0:paused"
    [event] = events_of("a_paused", {"attempt"})
    assert (event["number"], event["outcome"], event["ended_at"]) == (1, "restart_recoverable", T0 + 5 * HOUR + 30)
    assert event["failure_text"] is None


def test_retried_attempts_are_ordered_and_only_the_last_has_the_reason():
    rows, _ = rows_by_id()
    assert rows["a_retried"]["ended_at"] == T0 + 4 * HOUR + 104
    assert rows["a_retried"]["reason_code"] == "agent_runner_error"
    assert rows["a_retried"]["message_status"] == "running"
    assert rows["a_retried"]["revision"] == "8:11:0:running"
    first, second = events_of("a_retried", {"attempt"})
    assert (first["number"], first["reason_code"], first["checkpoint_id"], first["failure_text"]) == (
        1, None, "ckpt_1", None)
    assert first["at"] == T0 + 4 * HOUR + 90
    assert second["at"] == T0 + 4 * HOUR + 104
    assert (second["number"], second["reason_code"], second["checkpoint_id"]) == (2, "agent_runner_error", "ckpt_head")
    assert second["failure_text"] == "RuntimeError: provider dropped the stream"


def test_empty_reply_keeps_the_asked_row_and_has_no_replied_row():
    rows, _ = rows_by_id()
    assert rows["a_empty"]["reply"] == ""
    assert rows["a_empty"]["event_count"] == 0
    assert rows["a_empty"]["asked_preview"] == "Watch the page."
    assert [event["kind"] for event in events_of("a_empty")] == ["asked"]


def test_report_run_is_only_its_reply():
    events = events_of("report_ecbfda0a8aa1")
    assert [(event["kind"], event["text"]) for event in events] == [("replied", "The Nano tier is back in stock.")]


def test_sub_agent_that_never_started():
    with_job, without_job = events_of("a_never", {"subagent"})
    assert (with_job["job_id"], with_job["never_started"], with_job["failed"]) == ("j_ea89b270c19946", True, True)
    assert with_job["conversation"] is None
    assert with_job["error"] == "canonical execution admission is missing"
    assert with_job["closed_as"] == "errored"
    assert (without_job["job_id"], without_job["never_started"], without_job["status"]) == (None, True, None)
    assert without_job["brief"] == "Check the second page."
    assert without_job["error"] == "agent refused: generation limit"
    assert events_of("a_never", {"step"}) == []


def test_sub_agent_that_ran_replaces_its_step_and_nests_its_children():
    [event] = events_of("a_ran", {"subagent"})
    assert (event["job_id"], event["name"], event["status"], event["never_started"]) == (
        "j_ran", "stock check", "completed", False)
    assert event["conversation"] == {"session_id": SESSION, "head_id": "a_sub"}
    assert event["result"] == "Sold out."
    assert event["ended_at"] == T0 + 3 * HOUR + 40
    assert [child["job_id"] for child in event["children"]] == ["j_nested"]
    assert event["children"][0]["name"] == "Sub-agent"


def test_steps_are_bounded_flagged_and_skip_hidden_nodes():
    guide, failed = events_of("a_ran", {"step"})
    assert [guide["node_id"], failed["node_id"]] == ["s_guide", "s_failed"]
    assert guide["tool_call_id"] == "call_1"
    assert len(guide["input"]["note"]) == 2000 and guide["input_truncated"] is True
    assert guide["input"]["target"] == "browser"
    assert len(guide["result"]) == 8000 and guide["result_truncated"] is True
    assert failed["failed"] is True and failed["input"] == {"command": "false"}
    assert failed["input_truncated"] is False and failed["result_truncated"] is False


def _live_sub_agent_run(caller, *extra_nodes):
    index = SessionMemoryIndex()
    add(index,
        Call(id="u", role="user", created_at=T0, predecessor="ROOT", output="Go."),
        Call(id="a", role="llm", created_at=T0 + 1, predecessor="u", output=""),
        *extra_nodes)
    job = Job(id="j_live", parent_session_id=SESSION, prompt="Work.", agent_id="butler", context_mode="clean",
              caller_msg_id=caller, label="live", status=JobStatus.RUNNING, created_at=T0 + 2, started_at=T0 + 3)
    reads = ExecutionReads([], lambda _key: [], lambda _key: None)
    return run_events(index, "a", reads, jobs=[job])["events"]


def test_a_running_sub_agent_already_has_its_conversation():
    [event] = [item for item in _live_sub_agent_run("a") if item["kind"] == "subagent"]
    assert event["conversation"] == {"session_id": SESSION, "head_id": None}


def test_a_sub_agent_that_never_started_has_no_conversation():
    [event, *_] = events_of("a_never", {"subagent"})
    assert event["never_started"] is True
    assert event["conversation"] is None


def test_a_sub_agent_started_from_nested_program_code_belongs_to_the_run():
    events = _live_sub_agent_run(
        "fn_inner",
        Call(id="fn", role="code", name="helper", created_at=T0 + 1.2, caller="a"),
        Call(id="fn_inner", role="code", name="helper_inner", created_at=T0 + 1.4, caller="fn"),
    )
    assert [item["job_id"] for item in events if item["kind"] == "subagent"] == ["j_live"]


def test_a_sub_agent_of_another_run_stays_out():
    events = _live_sub_agent_run("elsewhere", Call(id="elsewhere", role="code", name="helper", created_at=T0, caller="other"))
    assert [item for item in events if item["kind"] == "subagent"] == []


def test_attempts_sit_where_they_ended_and_a_live_one_where_it_began():
    index = SessionMemoryIndex()
    add(index,
        Call(id="u", role="user", created_at=T0, predecessor="ROOT", output="Go."),
        Call(id="a", role="llm", created_at=T0 + 1, predecessor="u", output="Done.",
             metadata={"finished_at": T0 + 55}),
        Call(id="s", role="code", name="bash", created_at=T0 + 10, caller="a", input="{}", output="ok"),
    )
    attempts = [attempt("e", 1, leased=T0, activated=T0 + 1, ended=T0 + 90, outcome="restart_recoverable"),
                attempt("e", 2, leased=T0 + 91, activated=T0 + 92)]
    reads = ExecutionReads([RunExecution(execution("e", "running", created=T0), "u", "a")],
                           lambda _key: attempts, lambda _key: None)
    body = run_events(index, "a", reads)
    assert [(item["kind"], item["at"] - T0) for item in body["events"]] == [
        ("asked", 0), ("step", 10), ("replied", 55), ("attempt", 90), ("attempt", 92)]


def _failed_source_with(child):
    index = SessionMemoryIndex()
    add(index,
        Call(id="u", role="user", created_at=T0, predecessor="ROOT", output="Go."),
        Call(id="a", role="llm", created_at=T0 + 1, predecessor="u", output="[error] boom"),
    )
    source = RunExecution(execution("src", "failed", created=T0, terminal=T0 + 5), "u", "a")
    return index, ExecutionReads([source, child], lambda _key: [], lambda _key: None)


def test_a_retry_is_a_run_on_the_branch_it_came_from():
    child = RunExecution(execution("child", "running", created=T0 + 60, parent="src", source_checkpoint="ckpt"),
                         "u", "child_reply")
    index, reads = _failed_source_with(child)
    page = list_runs(index, SESSION, reads, head="a", offset=0, limit=20)
    assert [(row["id"], row["execution_id"]) for row in page["runs"]] == [("child", "child"), ("a", "src")]
    assert page["total"] == 2
    assert run_events(index, "child", reads) is not None


def test_a_retry_with_no_messages_is_listed_like_its_source():
    child = RunExecution(execution("child", "queued", created=T0 + 60, parent="src", source_checkpoint="ckpt"))
    index, reads = _failed_source_with(child)
    assert "child" in {row["id"] for row in list_runs(index, SESSION, reads, offset=0, limit=20)["runs"]}


def test_a_child_that_is_not_a_branch_is_no_run_of_its_own():
    child = RunExecution(execution("nested", "running", created=T0 + 60, parent="src"), "u", "nested_reply")
    index, reads = _failed_source_with(child)
    assert [row["id"] for row in list_runs(index, SESSION, reads, head="a", offset=0, limit=20)["runs"]] == ["a"]


def test_events_come_in_time_order():
    kinds = events_of("a_ran")
    assert [event["at"] for event in kinds] == sorted(event["at"] for event in kinds)
    assert [event["kind"] for event in kinds] == ["asked", "step", "step", "subagent", "replied"]


def test_turn_without_a_reply_is_a_run_named_by_its_execution():
    rows, _ = rows_by_id()
    waiting = rows["exec_waiting"]
    assert (waiting["message_id"], waiting["execution_id"], waiting["ended_at"]) == (None, "exec_waiting", None)
    assert waiting["user_message_id"] == "u_waiting"
    assert [event["kind"] for event in events_of("exec_waiting")] == ["asked"]


def test_retried_sibling_is_its_own_run_named_by_its_reply():
    rows, _ = rows_by_id()
    replaced = rows["a_replaced"]
    assert (replaced["execution_id"], replaced["reply"]) == ("exec_replaced", "Older answer.")
    assert replaced["user_message_id"] == "u_paused"


def test_execution_without_anchors_shows_only_on_the_current_head():
    assert "exec_program" in rows_by_id()[0]
    assert "exec_program" not in rows_by_id(head="a_paused")[0]


def test_paging_counts_everything_and_serves_only_the_page():
    index = monitor()
    reads = monitor_reads()
    first = list_runs(index, SESSION, reads, offset=0, limit=3)
    second = list_runs(index, SESSION, reads, offset=3, limit=3)
    assert (first["total"], second["total"], second["offset"]) == (9, 9, 3)
    assert [row["id"] for row in first["runs"]] == ["exec_program", "exec_waiting", "a_paused"]
    assert [row["id"] for row in second["runs"]] == ["a_replaced", "a_retried", "a_ran"]


def test_an_unchanged_branch_is_not_walked_again(monkeypatch):
    calls = []
    real = activity_runs._walk
    monkeypatch.setattr(activity_runs, "_walk", lambda *args: calls.append(args) or real(*args))
    index = monitor()
    reads = monitor_reads()
    list_runs(index, SESSION, reads)
    list_runs(index, SESSION, reads, offset=5)
    assert len(calls) == 1
    add(index, Call(id="a_waiting", role="llm", created_at=T0 + 7 * HOUR, predecessor="u_waiting", output="ok"))
    index.set_head("a_waiting")
    list_runs(index, SESSION, reads)
    assert len(calls) == 2


def test_chain_cache_is_bounded():
    index = monitor()
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    for number in range(activity_runs.CHAIN_CACHE_ENTRIES + 5):
        list_runs(index, f"session-{number}", reads)
    assert len(activity_runs._chains) == activity_runs.CHAIN_CACHE_ENTRIES


def test_broken_and_cyclic_chains_end_quietly():
    index = SessionMemoryIndex()
    add(index, Call(id="a", role="llm", created_at=T0, predecessor="b", output="x"),
        Call(id="b", role="llm", created_at=T0 - 1, predecessor="a", output="y"),
        Call(id="c", role="llm", created_at=T0 + 1, predecessor="missing", output="z"))
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    assert [row["id"] for row in list_runs(index, "cycle", reads, head="a")["runs"]] == ["a", "b"]
    assert [row["id"] for row in list_runs(index, "broken", reads, head="c")["runs"]] == ["c"]
    assert list_runs(index, "gone", reads, head="nope") == {"runs": [], "offset": 0, "total": 0}


def test_summaries_are_not_runs_and_keep_the_history_they_cover():
    index = SessionMemoryIndex()
    add(index,
        Call(id="u1", role="user", created_at=T0, predecessor="ROOT", output="first"),
        Call(id="a1", role="llm", created_at=T0 + 1, predecessor="u1", output="one"),
        Call(id="summary_x", role="llm", created_at=T0 + 2, predecessor="ROOT", output="recap",
             metadata={"covers_ids": ["u1", "a1"]}),
        Call(id="u2", role="user", created_at=T0 + 3, predecessor="summary_x", output="second"),
        Call(id="a2", role="llm", created_at=T0 + 4, predecessor="u2", output="two"))
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    assert [row["id"] for row in list_runs(index, "s", reads, head="a2")["runs"]] == ["a2", "a1"]


def test_structural_rewound_and_attach_nodes_are_not_runs():
    index = SessionMemoryIndex()
    add(index,
        Call(id="a1", role="llm", created_at=T0, predecessor="ROOT", output="kept"),
        Call(id="a2", role="llm", created_at=T0 + 1, predecessor="a1", metadata={"structural": True}),
        Call(id="a3", role="llm", created_at=T0 + 2, predecessor="a2", metadata={"rewound": True}),
        Call(id="a4", role="llm", created_at=T0 + 3, predecessor="a3", metadata={"function": "attach"}))
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    assert [row["id"] for row in list_runs(index, "s", reads, head="a4")["runs"]] == ["a1"]


def test_reply_and_asked_are_bounded():
    index = SessionMemoryIndex()
    add(index, Call(id="u", role="user", created_at=T0, predecessor="ROOT", output="q" * 9000),
        Call(id="a", role="llm", created_at=T0 + 1, predecessor="u", output="a" * 9000))
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    [row] = list_runs(index, "s", reads, head="a")["runs"]
    assert len(row["asked_preview"]) == 200
    assert (len(row["reply"]), row["reply_truncated"]) == (4000, True)
    asked, replied = run_events(index, "a", reads)["events"]
    assert (len(asked["text"]), asked["truncated"]) == (8000, True)
    assert (len(replied["text"]), replied["truncated"]) == (8000, True)


def test_unknown_run_has_no_events():
    assert run_events(monitor(), "nope", monitor_reads()) is None
    assert run_events(monitor(), "s_guide", monitor_reads()) is None
    assert run_events(monitor(), "task:123.0", monitor_reads()) is None


def test_pairing_uses_the_prompt_then_the_only_healthy_step():
    index = SessionMemoryIndex()
    add(index,
        Call(id="a", role="llm", created_at=T0, predecessor="ROOT", output="x"),
        Call(id="s1", role="code", name="agent", created_at=T0 + 1, caller="a", input={"prompt": "first"}),
        Call(id="s2", role="code", name="agent", created_at=T0 + 2, caller="a", input={"prompt": "second"}))
    def job(job_id, prompt, created):
        return Job(id=job_id, parent_session_id="s", prompt=prompt, agent_id="b", caller_msg_id="a",
                   status=JobStatus.COMPLETED, created_at=created)
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    body = run_events(index, "a", reads, jobs=[job("j1", "second", T0 + 3), job("j2", "reworded", T0 + 4)])
    kinds = sorted(event["kind"] for event in body["events"])
    assert kinds == ["replied", "subagent", "subagent"]


def test_nested_sub_agents_stop_at_the_depth_cap():
    index = SessionMemoryIndex()
    add(index, Call(id="a", role="llm", created_at=T0, predecessor="ROOT", output="x"))
    jobs_in_chain = []
    caller = "a"
    for depth in range(9):
        head = f"h{depth}"
        add(index, Call(id=head, role="llm", created_at=T0 + depth + 1, predecessor=None, output="y"))
        jobs_in_chain.append(Job(id=f"j{depth}", parent_session_id="s", prompt="p", agent_id="b", caller_msg_id=caller,
                                 status=JobStatus.COMPLETED, created_at=T0 + depth + 1, head_id=head))
        caller = head
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    [event] = [item for item in run_events(index, "a", reads, jobs=jobs_in_chain)["events"] if item["kind"] == "subagent"]
    depth = 0
    while event["children"]:
        event = event["children"][0]
        depth += 1
    assert depth == 5


def test_a_forked_sub_agent_does_not_adopt_its_siblings():
    index = SessionMemoryIndex()
    add(index,
        Call(id="a", role="llm", created_at=T0, predecessor="ROOT", output="x"),
        Call(id="u_fork", role="user", created_at=T0 + 1, predecessor="a", output="p"),
        Call(id="a_fork", role="llm", created_at=T0 + 2, predecessor="u_fork", output="q"))
    def job(job_id, head):
        return Job(id=job_id, parent_session_id="s", prompt=job_id, agent_id="b", caller_msg_id="a", parent_msg_id="a",
                   status=JobStatus.COMPLETED, created_at=T0 + 1, head_id=head)
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    events = [item for item in run_events(index, "a", reads, jobs=[job("j1", "a_fork"), job("j2", None)])["events"]
              if item["kind"] == "subagent"]
    assert sorted(item["job_id"] for item in events) == ["j1", "j2"]
    assert all(item["children"] == [] for item in events)


def test_task_runs_come_from_the_record_only():
    entries = [
        {"at": T0 + 9, "status": "failed", "text": "could not reach the page", "tip": "a_retried"},
        {"at": T0 + 8, "status": "quiet", "text": "", "tip": "a_empty"},
        {"at": T0 + 7, "status": "result", "text": "It broke before replying", "tip": None},
        {"at": T0 + 6, "status": "quiet", "text": "", "tip": "gone"},
    ]
    page = list_task_runs(monitor(), entries, monitor_reads())
    assert page["total"] == 4 and page["offset"] == 0
    retried, quiet, no_tip, unknown = page["runs"]
    assert (retried["id"], retried["task_outcome"], retried["task_text"]) == (
        "a_retried", "failed", "could not reach the page")
    assert retried["ended_at"] == T0 + 4 * HOUR + 104
    assert (quiet["id"], quiet["task_outcome"], quiet["task_text"], quiet["ended_at"]) == (
        "a_empty", "quiet", None, T0 + 9)
    assert (no_tip["id"], no_tip["started_at"], no_tip["ended_at"], no_tip["event_count"]) == (
        f"task:{T0 + 7}", None, T0 + 7, 0)
    assert no_tip["task_text"] == "It broke before replying"
    assert unknown["id"] == f"task:{T0 + 6}"


def test_a_missed_time_is_a_row_with_its_own_outcome():
    entries = [{"at": T0, "status": "missed", "text": "2026-11-10 09:00", "tip": None, "count": 2}]
    [row] = list_task_runs(monitor(), entries, monitor_reads())["runs"]
    assert (row["task_outcome"], row["task_text"], row["ended_at"]) == ("missed", "2026-11-10 09:00", T0)


def test_task_run_ends_at_the_record_time_when_the_node_kept_no_end():
    index = SessionMemoryIndex()
    add(index, Call(id="a", role="llm", created_at=T0, predecessor="ROOT", output="x"))
    reads = ExecutionReads((), lambda _: [], lambda _: None)
    [row] = list_task_runs(index, [{"at": T0 + 5, "status": "result", "text": "t", "tip": "a"}], reads)["runs"]
    assert row["ended_at"] == T0 + 5


# The endpoints, over the real stores


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.interfaces.server.routes.execution import activity_runs as route
    from openprogram.interfaces.server.routes.execution import lifecycle
    from openprogram.runtime.execution import AttemptStore, CapabilitySet, ExecutionStore
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.session_store import SessionStore

    database = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(database, SESSION)
    writer.append(Call(id="u1", role="user", created_at=T0, predecessor="ROOT", output="hello"))
    writer.append(Call(id="a1", role="llm", created_at=T0 + 1, predecessor="u1", output="hi"))
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    attempts = AttemptStore(store)
    store.create_revision(revision_id="r1", manifest={"entrypoint": "agent"})
    admitted = store.admit_execution(
        execution_id="exec1", run_id="run1", session_id=SESSION, revision_id="r1", input_ref="i", input_hash="h",
        entrypoint="agent", trusted_actor={"subject": "u"}, config_snapshot_ref="c",
        capabilities=CapabilitySet(pause=False, step=False, steer=False),
        user_message_id="u1", assistant_message_id="a1",
    )
    attempt_row, leased = attempts.lease("exec1", expected_version=admitted.status_version, owner_id="o")
    active, running = attempts.activate(
        attempt_row.attempt_id, generation=attempt_row.generation, expected_execution_version=leased.status_version)
    attempts.finish(
        active.attempt_id, generation=active.generation, expected_execution_version=running.status_version,
        target=ExecutionStatus.FAILED, outcome="failed", reason_code="agent_runner_error",
        failure_text="RuntimeError: boom",
    )
    tasks = [
        {"id": "mine", "session_id": SESSION},
        {"id": "via-execution", "execution": {"session_id": SESSION}},
        {"id": "theirs", "session_id": "other"},
    ]
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: database)
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr(lifecycle, "_actor_and_session", lambda _: ({}, SESSION))
    monkeypatch.setattr("openprogram.runtime.execution.authorization.authorize_session_action", lambda *_: None)
    monkeypatch.setattr("openprogram.runtime.scheduler.service.list_tasks", lambda: tasks)
    monkeypatch.setattr("openprogram.runtime.scheduler.task_runs.recent", lambda _task: [
        {"at": T0 + 2, "status": "failed", "text": "broke", "tip": "a1"}])
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs", lambda _session: [])
    monkeypatch.setattr(
        "openprogram.runtime.execution.conversation_scope.conversation_executions", lambda *_: ())
    app = FastAPI()
    route.register(app)
    with TestClient(app) as test_client:
        yield test_client


def test_endpoint_lists_runs_and_clamps_the_page(client):
    response = client.get(f"/api/session/{SESSION}/activity/runs?limit=1000&offset=-4")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["offset"], body["total"]) == (0, 1)
    [row] = body["runs"]
    assert (row["id"], row["execution_id"], row["execution_status"], row["reason_code"], row["event_count"]) == (
        "a1", "exec1", "failed", "agent_runner_error", 1)
    assert client.get(f"/api/session/{SESSION}/activity/runs?limit=0").json()["runs"] == [row]


def test_endpoint_returns_events_with_the_stored_failure(client):
    response = client.get(f"/api/session/{SESSION}/activity/runs/a1/events")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [event["kind"] for event in body["events"]] == ["asked", "replied", "attempt"]
    [attempt_event] = [event for event in body["events"] if event["kind"] == "attempt"]
    assert attempt_event["failure_text"] == "RuntimeError: boom"
    assert body["revision"] == client.get(f"/api/session/{SESSION}/activity/runs").json()["runs"][0]["revision"]


def test_endpoint_refuses_what_it_cannot_show(client):
    assert client.get("/api/session/other/activity/runs").status_code == 404
    assert client.get(f"/api/session/{SESSION}/activity/runs/nope/events").status_code == 404
    assert client.get("/api/session/other/activity/runs/a1/events").status_code == 404


def test_endpoint_reads_task_runs_only_for_this_conversation(client):
    body = client.get(f"/api/session/{SESSION}/activity/runs?task_id=mine&limit=1&offset=9").json()
    assert (body["offset"], body["total"]) == (0, 1)
    assert body["runs"][0]["task_outcome"] == "failed"
    assert client.get(f"/api/session/{SESSION}/activity/runs?task_id=via-execution").status_code == 200
    assert client.get(f"/api/session/{SESSION}/activity/runs?task_id=theirs").status_code == 404
    assert client.get(f"/api/session/{SESSION}/activity/runs?task_id=unknown").status_code == 404


def test_roster_endpoint_gives_each_job_its_facts(client, monkeypatch):
    from openprogram.runtime.execution import hooks

    job = Job(id="j1", parent_session_id=SESSION, prompt="look", agent_id="main", status=JobStatus.RUNNING)
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs", lambda _session: [job])
    seen = []

    class Facts:
        def roster(self, session_id, jobs):
            seen.append((session_id, [item.id for item in jobs]))
            return {"agents": {"j1": {"agent_status": "stalled", "queued": False,
                                      "usage": {"tokens": 5, "cost_usd": "0.010000", "tool_calls": 2}}},
                    "outgoing": [{"kind": "outgoing", "job_id": "d1", "to": "Butler", "status": "waiting"}]}

    monkeypatch.setattr(hooks, "registered", lambda name: Facts() if name == "agent_roster_facts" else None)
    body = client.get(f"/api/session/{SESSION}/activity/roster").json()
    assert seen == [(SESSION, ["j1"])]
    assert body["agents"]["j1"]["agent_status"] == "stalled" and body["outgoing"][0]["to"] == "Butler"
    assert client.get("/api/session/other/activity/roster").status_code == 404
    monkeypatch.setattr(hooks, "registered", lambda _name: None)
    assert client.get(f"/api/session/{SESSION}/activity/roster").json() == {"agents": {}, "outgoing": []}


def test_endpoint_is_not_found_for_a_missing_session(client, monkeypatch):
    from openprogram.interfaces.server.routes.execution import lifecycle

    monkeypatch.setattr(lifecycle, "_actor_and_session", lambda _: ({}, None))
    assert client.get("/api/session/absent/activity/runs").status_code == 404
