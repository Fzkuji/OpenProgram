"""How the projection shapes rows the web client used to shape itself."""
from __future__ import annotations

from openprogram.interfaces.server.transcript.rows import project_rows


def _run(**fields) -> dict:
    return {"role": "assistant", "type": "status", "display": "runtime", "status": "completed", "content": "", **fields}


def test_a_direct_run_stays_top_level_when_only_its_predecessor_is_an_assistant() -> None:
    rows = project_rows([
        {"id": "assistant-1", "role": "assistant", "content": "parent reply"},
        _run(id="direct-run", function="report_workflow", caller="", predecessor="assistant-1"),
    ])
    assert [row["id"] for row in rows] == ["assistant-1", "direct-run"]
    assert "runtimeChildren" not in rows[0]
    assert (rows[1]["display"], rows[1]["rowKind"]) == ("runtime", "function_run")


def test_a_run_the_assistant_called_nests_under_that_assistant() -> None:
    rows = project_rows([
        {"id": "assistant-1", "role": "assistant", "content": "parent reply"},
        _run(id="owned-run", function="paper_agent", caller="assistant-1", predecessor="assistant-1"),
    ])
    assert [row["id"] for row in rows] == ["assistant-1"]
    assert [(child["id"], child["calledBy"]) for child in rows[0]["runtimeChildren"]] == [("owned-run", "assistant-1")]


def test_a_reply_a_restart_cut_off_names_the_reply_that_continues_it() -> None:
    rows = {row["id"]: row for row in project_rows([
        {"id": "u1", "role": "user", "content": "run sleep 40"},
        {"id": "u1_reply", "role": "assistant", "content": "", "predecessor": "u1",
         "blocks": [{"type": "tool", "tool": "bash", "tool_call_id": "t1", "is_error": True}]},
        {"id": "r_user", "role": "user", "content": "Continue the interrupted task", "display": "runtime",
         "trigger": "restart", "predecessor": "u1_reply"},
        {"id": "r_user_reply", "role": "assistant", "content": "done", "predecessor": "r_user"},
    ])}
    assert rows["u1_reply"]["continuedBy"] == "r_user_reply"
    assert rows["r_user_reply"]["continues"] == ["u1_reply"]
    assert rows["r_user"]["rowKind"] == "silent"
    assert "continues" not in rows["u1_reply"]


class _Sessions:
    def __init__(self, *names: str):
        self.names = set(names)

    def get_session(self, session_id: str) -> dict | None:
        return {"id": session_id} if session_id in self.names else None


def test_a_receipt_on_an_old_row_counts_only_when_its_sender_exists() -> None:
    rows = project_rows([
        {"id": "a", "role": "user", "content": "[message from sender-session:head (helper)]\nthe body"},
        {"id": "b", "role": "user", "content": "[task from no-such-session]\ntyped by a person"},
        {"id": "c", "role": "user", "content": "[task from sender-session]\nstored as typed", "origin": "person"},
    ], db=_Sessions("sender-session"))
    assert [(row["origin"], row["rowKind"]) for row in rows] == [
        ("agent_delivery", "received_message"), ("person", "user"), ("person", "user")]


def test_a_stored_hand_off_between_conversations_is_agent_traffic() -> None:
    rows = project_rows([
        {"id": "a", "role": "user", "content": "📨 Message from “Butler” (butler):\n\nCheck the deploy\n\n(Another of your conversations handed this to you.)"},
        {"id": "b", "role": "user", "content": "↩️ Reply from “Butler” (butler) to the message you sent it:\n\nThree files changed."},
        {"id": "c", "role": "user", "content": "📨 Message from “Nobody” (no-such-session):\n\npasted by a person"},
        {"id": "d", "role": "user", "content": "I saw 📨 Message from “Butler” (butler): in the log"},
    ], db=_Sessions("butler"))
    assert [(row["origin"], row["rowKind"]) for row in rows] == [
        ("agent_delivery", "received_message"), ("agent_delivery", "received_message"),
        ("person", "user"), ("person", "user")]


def test_a_failed_reply_keeps_its_partial_text_apart_from_the_failure() -> None:
    row, legacy, compatible = project_rows([
        {"id": "failed-history", "role": "assistant", "status": "error", "content": "Partial answer",
         "error_detail": "connection lost", "error_reason": "transport", "error_retryable": True, "error_retry_after_s": 4},
        {"id": "legacy", "role": "assistant", "status": "error", "content": "[error] ProviderStreamError: ConnectError",
         "blocks": [{"type": "text", "text": "Existing answer"}]},
        {"id": "persisted-error", "role": "assistant", "status": "error", "content": "[error] disconnected",
         "error_detail": "[error] disconnected", "blocks": [{"type": "text", "text": "Partial answer"}]},
    ])
    assert (row["content"], row["errorDetail"], row["errorReason"], row["errorRetryable"], row["errorRetryAfterS"]) == (
        "Partial answer", "connection lost", "transport", True, 4)
    assert "errorCause" not in row and "errorCause" not in legacy
    assert (legacy["content"], legacy["errorDetail"]) == ("Existing answer", "[error] ProviderStreamError: ConnectError")
    assert (compatible["content"], compatible["errorDetail"]) == ("Partial answer", "[error] disconnected")


def test_a_reply_a_spent_quota_stopped_carries_its_cause() -> None:
    cause = {"cause": "quota_exhausted", "http_status": 429, "provider": "openai-codex", "limit": "5h",
             "resets_at": 1_800_000_000.0, "account": None,
             "other_limits": [{"label": "Week", "left_fraction": 0.71, "resets_at": None}]}
    failed, done = project_rows([
        {"id": "spent", "role": "assistant", "status": "error", "content": "[error] usage_limit_reached",
         "error_detail": "[error] usage_limit_reached", "error_reason": "authz", "error_cause": cause},
        {"id": "fine", "role": "assistant", "status": "completed", "content": "ok", "error_cause": cause},
    ])
    assert failed["errorCause"] == cause
    assert "errorCause" not in done


def test_only_the_message_that_set_a_goal_carries_the_goal_mark() -> None:
    rows = {row["id"]: row for row in project_rows([
        {"id": "g", "role": "user", "content": "ship the release", "starts_goal": True},
        {"id": "plain", "role": "user", "content": "and the notes", "predecessor": "g"},
    ])}
    assert (rows["g"]["startsGoal"], rows["g"]["content"]) == (True, "ship the release")
    assert "startsGoal" not in rows["plain"]
