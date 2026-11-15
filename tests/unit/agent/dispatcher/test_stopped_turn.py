"""A stopped reply, as it is stored and as later requests show it.

- What ran keeps its signed history; the text cut while streaming is one
  last unsigned response (OpenCode ``to-llm-message``).
- Every call has a result: a call that never started gets "Not run" (Codex).
- The next message carries a note that the reply was stopped, written once
  on its node (Codex ``<turn_aborted>`` content, Grok Build placement).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, EventTextDelta, EventTextEnd, EventTextStart, Model,
    TextContent, ThinkingContent, ToolCall, Usage,
)
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import turn_runtime as D

STOPPED_NOTE = "The user stopped your previous reply before it finished; its last part is incomplete."


def _model() -> Model:
    return Model(id="stub", name="stub", api="completion", provider="openai", base_url="https://api.openai.com/v1")


def _message(content, *, stop_reason=None) -> AssistantMessage:
    return AssistantMessage(content=content, api="completion", provider="openai", model="stub",
                            usage=Usage(input_tokens=5, output_tokens=2),
                            **({"stop_reason": stop_reason} if stop_reason else {}),
                            timestamp=int(time.time() * 1000))


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    store = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: _model())
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {"id": agent_id, "system_prompt": "be brief"})
    return store


def _turn(text: str, stream_fn, cancel_event=None, on_event=None):
    original = D._run_loop_blocking

    def wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return original(req=req, history=history, on_event=on_event, cancel_event=cancel_event,
                        stream_fn=stream, **_extra)

    stream = stream_fn
    with patch.object(D, "_run_loop_blocking", wrapped):
        return D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text=text, agent_id="main", source="tui"),
            cancel_event=cancel_event, on_event=on_event,
        )


def _stopped_mid_text(cancel_event: threading.Event):
    async def stream(model, context, options):
        from openprogram.runtime.execution.run_control.wakeups import set_and_fire

        yield EventStart(partial=_message([]))
        yield EventTextStart(content_index=0, partial=_message([TextContent(text="")]))
        yield EventTextDelta(content_index=0, delta="Once upon", partial=_message([TextContent(text="Once upon")]))
        set_and_fire(cancel_event)
        await asyncio.Event().wait()
    return stream


def _cut_with_an_unread_delta(cancel_event: threading.Event | None, fail: BaseException | None = None):
    """A provider that mutates one partial, as the real ones do, and has
    already added a delta the loop never reads when the response is cut."""
    async def stream(model, context, options):
        from openprogram.runtime.execution.run_control.wakeups import set_and_fire

        partial = _message([TextContent(text="")])
        yield EventStart(partial=partial)
        yield EventTextStart(content_index=0, partial=partial)
        partial.content[0].text = "Once upon"
        yield EventTextDelta(content_index=0, delta="Once upon", partial=partial)
        partial.content[0].text += " a time"
        if fail is not None:
            raise fail
        set_and_fire(cancel_event)
        await asyncio.Event().wait()
    return stream


def _frames(events: list[dict], kind: str) -> list[dict]:
    return [e["data"] for e in events if e.get("type") == "chat_response" and e["data"].get("type") == kind]


def _streamed_text(events: list[dict]) -> str:
    return "".join(f["event"].get("text") or "" for f in _frames(events, "stream_event")
                   if f["event"].get("type") == "text")


def _block_text(blocks: list[dict]) -> str:
    return "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")


def _answer(seen: list):
    async def stream(model, context, options):
        seen.append(list(context.messages))
        yield EventStart(partial=_message([]))
        yield EventTextStart(content_index=0, partial=_message([TextContent(text="")]))
        yield EventTextDelta(content_index=0, delta="ok", partial=_message([TextContent(text="ok")]))
        yield EventTextEnd(content_index=0, content="ok", partial=_message([TextContent(text="ok")]))
        yield EventDone(reason="stop", message=_message([TextContent(text="ok")], stop_reason="stop"))
    return stream


def _text(message) -> str:
    return "".join(getattr(block, "text", "") for block in message.content if hasattr(block, "text"))


def test_a_reply_stopped_mid_text_keeps_its_text_and_the_next_message_says_so(db) -> None:
    stop = threading.Event()
    _turn("tell me a story", _stopped_mid_text(stop), cancel_event=stop)

    reply = db.get_messages("c1")[-1]
    assert reply["role"] == "assistant" and reply["status"] == "cancelled", reply
    assert reply["content"] == "Once upon"
    assert reply["interruption"] == {"reason_code": "cancel.user", "phase": "streaming", "cut_tools": False}
    blocks = json.loads(reply["extra"])["blocks"]
    assert blocks == [{"type": "text", "text": "Once upon", "partial": True, "round_start": True}]

    second: list = []
    _turn("continue", _answer(second))
    third: list = []
    _turn("thanks", _answer(third))

    live = _text(second[0][-1])
    assert STOPPED_NOTE in live and live.endswith("continue")
    assert live.index(STOPPED_NOTE) < live.index("continue")
    # The stopped reply is sent as it was shown, and the note is read back
    # from the message's node: the next request sends the same bytes.
    assert [_text(m) for m in second[0][:-1]][-1] == "Once upon"
    replayed = [m for m in third[0] if getattr(m, "role", None) == "user" and _text(m).endswith("continue")]
    assert replayed and _text(replayed[0]) == live
    # The note is for the message after the stopped reply only.
    assert STOPPED_NOTE not in _text(third[0][-1])


def test_a_stopped_reply_is_the_same_in_the_stream_the_result_frame_the_row_and_the_result(db) -> None:
    stop = threading.Event()
    events: list[dict] = []
    result = _turn("tell me a story", _cut_with_an_unread_delta(stop), cancel_event=stop, on_event=events.append)

    reply = db.get_messages("c1")[-1]
    frame = _frames(events, "result")[-1]
    # A delta the loop never read was never sent, so it is not kept either.
    assert _streamed_text(events) == "Once upon"
    assert reply["content"] == "Once upon"
    assert _block_text(json.loads(reply["extra"])["blocks"]) == "Once upon"
    # A client that reads only the text (CLI --print, a channel) gets it.
    assert frame["content"] == "Once upon" and result.final_text == "Once upon"
    assert _block_text(frame["blocks"]) == "Once upon"
    assert result.stopped is True


def test_a_stopped_reply_with_a_file_keeps_its_text_before_the_file(db, monkeypatch) -> None:
    from openprogram.programs.tools.interaction import send_file
    from openprogram.state.attachments import find_markers

    sent = {"name": "notes.txt", "path": "/work/notes.txt", "size": 3}
    monkeypatch.setattr(send_file, "outbound_files", lambda tool_calls, session_id: [dict(sent)])
    stop = threading.Event()
    events: list[dict] = []
    result = _turn("tell me a story", _cut_with_an_unread_delta(stop), cancel_event=stop, on_event=events.append)

    reply = db.get_messages("c1")[-1]
    frame = _frames(events, "result")[-1]
    assert reply["content"].startswith("Once upon\n\n")
    assert [path for _name, _meta, path in find_markers(reply["content"])] == ["/work/notes.txt"]
    assert frame["content"] == reply["content"] == result.final_text
    assert result.attached_files == ["/work/notes.txt"]


def test_a_reply_that_fails_mid_stream_keeps_only_what_was_sent(db) -> None:
    events: list[dict] = []
    result = _turn("tell me a story", _cut_with_an_unread_delta(None, fail=ValueError("provider broke")),
                   on_event=events.append)

    reply = db.get_messages("c1")[-1]
    frame = _frames(events, "error")[-1]
    assert result.failed is True and reply["status"] in ("error", "failed"), reply
    assert _streamed_text(events) == "Once upon"
    assert _block_text(json.loads(reply["extra"])["blocks"]) == "Once upon"
    assert frame["content"] == reply["content"]


def test_a_stop_while_tools_ran_keeps_the_response_and_closes_every_call() -> None:
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _TurnTally, _keep_stopped_response

    tally = _TurnTally()
    tally.ended_response = _message([
        ThinkingContent(thinking="plan", thinking_signature="sig-1"),
        TextContent(text="Running both."),
        ToolCall(id="a", name="bash", arguments={"command": "make"}),
        ToolCall(id="b", name="bash", arguments={"command": "make test"}),
    ], stop_reason="toolUse")
    ordered = [{"type": "text", "text": "Earlier.", "signature": "sig-0", "round_start": True}]

    _keep_stopped_response(tally, ordered)

    assert [b["type"] for b in ordered] == ["text", "thinking", "text", "tool", "tool"]
    assert ordered[1] == {"type": "thinking", "text": "plan", "signature": "sig-1", "round_start": True}
    assert [b.get("tool_call_id") for b in ordered[3:]] == ["a", "b"]


def test_a_stream_cut_mid_call_drops_the_call_and_its_reasoning_signature() -> None:
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _TurnTally, _keep_stopped_response

    tally = _TurnTally()
    tally.open_response = _message([
        ThinkingContent(thinking="half a thought", thinking_signature=None),
        TextContent(text="Let me"),
        ToolCall(id="c", name="bash", arguments={}),
    ])
    tally.forwarded = [
        {"type": "thinking", "index": 0, "text": "half a thought"},
        {"type": "text", "index": 1, "text": "Let me"},
    ]
    ordered: list[dict] = []

    _keep_stopped_response(tally, ordered)

    assert ordered == [
        {"type": "thinking", "text": "half a thought", "partial": True, "round_start": True},
        {"type": "text", "text": "Let me", "partial": True},
    ]


def test_a_call_that_never_started_gets_a_not_run_result(db) -> None:
    from openprogram.runtime.conversation.turn_runtime.persistence import (
        _close_stopped_calls, stopped_reply_interruption,
    )
    from openprogram.runtime.context.render import render_dag_messages
    from openprogram.runtime.execution.run_control.stop_reasons import NOT_RUN_TEXT
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call, ROLE_CODE

    db.create_session("c1", "main")
    db.append_message("c1", {"id": "u1", "role": "user", "content": "build and test"})
    writer = SessionNodeWriter(db, "c1")
    blocks = [
        {"type": "text", "text": "Running both.", "round_start": True},
        {"type": "tool", "tool": "bash", "tool_call_id": "a", "input": '{"command": "make"}',
         "result": "Stopped by the user after 1.2s.", "is_error": True, "outcome": "cancelled"},
        {"type": "tool", "tool": "bash", "tool_call_id": "b", "input": '{"command": "make test"}',
         "result": None, "is_error": None},
    ]
    db.append_message("c1", {"id": "u1_reply", "role": "assistant", "content": "Running both.",
                             "predecessor": "u1", "status": "cancelled",
                             "extra": json.dumps({"blocks": blocks})})
    writer.append(Call(id="u1_reply_t_a", created_at=time.time(), role=ROLE_CODE, name="bash",
                       input='{"command": "make"}', output="Stopped by the user after 1.2s.",
                       caller="u1_reply", metadata={"tool_call_id": "a", "is_error": True, "outcome": "cancelled"}))

    class _Req:
        session_id = "c1"
        memory_policy = None

    _close_stopped_calls(db, _Req(), "u1_reply", blocks, set())

    assert blocks[2]["result"] == NOT_RUN_TEXT and blocks[2]["outcome"] == "not_started"
    assert stopped_reply_interruption(blocks)["cut_tools"] is True
    db.merge_node_metadata("c1", "u1_reply", {"extra": json.dumps({"blocks": blocks})})
    graph = SessionNodeWriter(db, "c1").load()
    ids = [n.id for n in sorted(graph.nodes.values(), key=lambda n: n.created_at or 0) if n.id != "ROOT"]
    messages = render_dag_messages(graph, ids)
    calls = [b.id for m in messages if getattr(m, "role", "") == "assistant" for b in m.content
             if isinstance(b, ToolCall)]
    results = {m.tool_call_id: m.content[0].text for m in messages if getattr(m, "role", "") == "toolResult"}
    assert calls == ["a", "b"]
    assert results == {"a": "Stopped by the user after 1.2s.", "b": NOT_RUN_TEXT}
