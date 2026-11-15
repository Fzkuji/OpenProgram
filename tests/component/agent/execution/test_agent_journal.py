"""An Agent turn's journal: what it records and how an interrupted turn resumes."""

from openprogram.runtime.execution.agent_journal import (
    AgentJournal,
    INTERRUPTED_TOOL_TEXT,
    interrupted_outcome,
    latest_pointer,
    load_journal,
    manifest_fields,
)
from tests.support.agent_journal import MemorySessions

SESSION = "local_journal"
TURN = {"user_message_id": "u1", "assistant_message_id": "a1", "base_history_head_id": "u1"}


def _decision(*calls):
    return {
        "role": "assistant", "api": "fake", "provider": "fake", "model": "fake", "timestamp": 1,
        "stop_reason": "toolUse",
        "content": [{"type": "text", "text": "working"}]
        + [{"type": "toolCall", "id": call, "name": name, "arguments": {}} for call, name in calls],
    }


def _result(call, name):
    return {
        "role": "toolResult", "tool_call_id": call, "tool_name": name, "timestamp": 1,
        "content": [{"type": "text", "text": f"{call} done"}],
    }


def _journal(sessions, execution_id="e1"):
    journal = AgentJournal(SESSION, execution_id, sessions=sessions)
    journal.record_snapshot({"contract": 1})
    return journal


def _interrupted(sessions, execution_id="e1"):
    state = load_journal(SESSION, latest_pointer(SESSION, execution_id, sessions=sessions), sessions=sessions)
    return state.interrupted_step()


def test_folding_restores_the_decision_and_finished_results():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "bash"), ("c2", "bash")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "bash", {})
    journal.record_tool_result("c1", _result("c1", "bash"), is_error=False, outcome="completed",
                               repeat_failures={"bash": 1})
    state = load_journal(SESSION, journal.pointer(), sessions=sessions)
    assert state.last.action_id == "p1"
    assert [item["tool_call_id"] for item in state.tool_results()] == ["c1"]
    assert state.repeat_failures == {"bash": 1}
    assert state.interrupted_step() is None


def test_a_read_only_tool_is_simply_run_again():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "read")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "read", {})
    assert interrupted_outcome(_interrupted(sessions)[1]) == "rerun"


def test_an_edit_that_did_not_touch_the_file_runs_again(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("before")
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "edit")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "edit", {"file_path": str(target)})
    assert interrupted_outcome(_interrupted(sessions)[1]) == "rerun"


def test_an_edit_that_changed_the_file_is_reported(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("before")
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "edit")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "edit", {"file_path": str(target)})
    target.write_text("after")
    assert interrupted_outcome(_interrupted(sessions)[1]) == "report"


def test_any_other_tool_is_reported_and_never_rerun():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "bash")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "bash", {})
    assert interrupted_outcome(_interrupted(sessions)[1]) == "report"
    state = load_journal(SESSION, journal.pointer(), sessions=sessions)
    assert state.last.steps["c1"].result is None
    assert INTERRUPTED_TOOL_TEXT


def test_a_suspended_function_is_not_an_interruption():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "ask_user")), action_id="p1", turn=TURN)
    journal.record_tool_started("c1", "ask_user", {})
    journal.record_tool_suspended("c1")
    assert _interrupted(sessions) is None


def test_steering_is_folded_once_and_kept_for_the_next_context():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "bash")), action_id="p1", turn=TURN)
    message = {"role": "user", "content": [{"type": "text", "text": "also this"}], "timestamp": 1}
    journal.record_steer("cmd-1", "m1", message)
    journal.record_steer("cmd-1", "m1", message)
    state = load_journal(SESSION, journal.pointer(), sessions=sessions)
    assert [item["command_id"] for item in state.decisions[-1].steers_after] == ["cmd-1"]


def test_a_branch_reads_its_source_up_to_the_fork_point():
    sessions = MemorySessions()
    source = _journal(sessions, "e1")
    source.record_decision(_decision(("c1", "bash")), action_id="p1", turn=TURN)
    fork_point = source.pointer()
    source.record_decision(_decision(("c2", "bash")), action_id="after-fork", turn=TURN)
    branch = AgentJournal(SESSION, "e2", state=load_journal(SESSION, fork_point, sessions=sessions),
                          position=fork_point["position"], source=fork_point, sessions=sessions)
    branch.record_decision(_decision(("c3", "bash")), action_id="p2", turn=TURN)
    state = load_journal(SESSION, branch.pointer(), sessions=sessions)
    assert [item.action_id for item in state.decisions] == ["p1", "p2"]


def test_the_manifest_points_at_the_journal_and_holds_no_messages():
    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "bash")), action_id="p1", turn=TURN)
    state = load_journal(SESSION, journal.pointer(), sessions=sessions)
    kind, frontier, refs = manifest_fields(state, journal.pointer())
    assert refs["agent_journal"] == journal.pointer()
    assert refs["agent_position"]["current_decision"]["provider_action_id"] == "p1"
    assert "working" not in str(refs) and kind and frontier


def test_a_resumed_tool_result_keeps_its_details():
    from openprogram.models.providers.types import ToolResultMessage

    sessions = MemorySessions()
    journal = _journal(sessions)
    journal.record_decision(_decision(("c1", "tool_search")), action_id="p1", turn=TURN)
    loads = {"loads": [{"name": "t", "definition": {"name": "t", "description": "", "parameters": {}}}]}
    message = ToolResultMessage.model_validate({**_result("c1", "tool_search"), "details": loads})
    journal.record_tool_result("c1", message.model_dump(mode="json"), is_error=False,
                               outcome="completed", repeat_failures={})

    state = load_journal(SESSION, journal.pointer(), sessions=sessions)
    restored = [ToolResultMessage.model_validate(item) for item in state.tool_results()]
    assert restored[0].details == loads
