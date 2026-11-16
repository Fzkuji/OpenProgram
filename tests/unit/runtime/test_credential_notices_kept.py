"""A credential switch's line over a reply is stored with the reply.

The chat shows "Switched to Work: Personal hit its rate limit" over a reply
while it streams (``chat-stream.ts``); the turn keeps it
(``loop_runner._record_agent_event``), the reply row stores it
(``persistence.persist_assistant_message``) and the transcript sends it again
after a reload (``transcript/rows.py``).
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.interfaces.server.transcript.rows import project_rows
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation.turn_runtime import loop_runner
from openprogram.runtime.conversation.turn_runtime.persistence import persist_assistant_message


def _retry(reason: str, credential: dict | None) -> SimpleNamespace:
    inner = SimpleNamespace(type="retry", reason=reason, credential=credential, restart=False)
    return SimpleNamespace(type="message_update", assistant_message_event=inner,
                           message=SimpleNamespace(role="assistant"))


def _req():
    from openprogram.runtime.agent.turn_request import TurnRequest

    return TurnRequest(session_id="s1", user_text="hi", agent_id="main", source="web", user_msg_id="u1")


def test_the_turn_keeps_each_switch_and_nothing_for_other_retries() -> None:
    tally = loop_runner._TurnTally()
    for ev in (_retry("credential", {"to": "Work", "from": "Personal", "cause": "rate_limit"}),
               _retry("transport", None),
               _retry("credential_wait", {"to": "Work"}),
               _retry("credential", {"to": "Spare", "from": "Work"})):
        loop_runner._record_agent_event(ev, tally, req=None, on_event=None, ordered_blocks_out=None)
    assert tally.usage_total["credential_notices"] == [
        {"to": "Work", "from": "Personal", "cause": "rate_limit"},
        {"to": "Spare", "from": "Work"},
    ]


def test_the_reply_stores_its_notices_and_the_transcript_sends_them_after_a_reload(tmp_path, monkeypatch) -> None:
    db = SessionDB(tmp_path / "sessions")
    db.append_message("s1", {"id": "u1", "role": "user", "content": "hi", "timestamp": 1})
    notices = [{"to": "Work", "from": "Personal", "cause": "rate_limit"}]
    msg, *_ = persist_assistant_message(
        db=db, req=_req(), session={}, usage={"credential_notices": notices, "service_tiers": ["default"]},
        final_text="hello", history=[], tool_calls=[], _ordered_blocks=[], _agentic_tool_names=set(),
        _placeholder_inserted=False, cancel_event=None, assistant_msg_id="u1_reply", user_msg_id="u1",
    )
    assert msg["credential_notices"] == notices
    assert "credential_notices" not in msg["usage"], "the notices are not usage"
    rows = {row["id"]: row for row in project_rows(db.get_messages("s1"))}
    assert rows["u1_reply"]["credentialNotices"] == notices


def test_a_reply_with_no_switch_sends_no_notices() -> None:
    rows = project_rows([{"id": "a", "role": "assistant", "content": "x"},
                         {"id": "b", "role": "assistant", "content": "y", "credential_notices": [{"from": "x"}]}])
    assert all("credentialNotices" not in row for row in rows)
