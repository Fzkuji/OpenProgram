"""A reply's flat text: its passages, a blank line apart where a block came between.

The chat draws a reply's blocks; CLI ``--print``, a channel, search, a title
and a copied message read its ``content``. Every builder of that text uses
``reply_text``: text blocks in a row are joined as they are, and passages a
tool call, reasoning or a steering message came between get a blank line,
unless the boundary already has whitespace. The stream (deltas plus each
frame's ``separator``), the result frame, the stored row, a reload and
``TurnResult.final_text`` say the same; the next request still carries the
provider's own blocks.
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
    AssistantMessage, EventDone, EventStart, EventTextDelta, EventTextEnd, EventTextStart,
    EventThinkingDelta, EventThinkingEnd, EventThinkingStart, Model, TextContent, ThinkingContent,
    ToolCall, Usage,
)
from openprogram.programs._host import runtime as R
from openprogram.runtime.agent import tool_execution
from openprogram.programs._host.runtime import function
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import turn_runtime as D
from openprogram.state.reply_text import ReplyText, reply_text, stream_text


def _model() -> Model:
    return Model(id="stub", name="stub", api="completion", provider="openai", base_url="https://api.openai.com/v1")


def _message(content, *, stop_reason=None) -> AssistantMessage:
    return AssistantMessage(content=content, api="completion", provider="openai", model="stub",
                            usage=Usage(input=5, output=2),
                            **({"stop_reason": stop_reason} if stop_reason else {}),
                            timestamp=int(time.time() * 1000))


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    store = SessionDB(tmp_path / "sessions-git")
    store.test_root = tmp_path / "sessions-git"
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: _model())
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "be brief", "tools": ["passage_probe"]})
    return store


@pytest.fixture(autouse=True)
def turn_loops_closed():
    """A turn closes its event loop, and the pool that loop owns, on a thread
    of its own (``loop_runner._close_turn_loop``); the test ends after it."""
    yield
    for thread in threading.enumerate():
        if thread.name == "openprogram-turn-loop-close":
            thread.join(timeout=10)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):
    """One registered tool, ``passage_probe``; ``ran`` is called when it runs."""
    saved = dict(R._registry), {k: set(v) for k, v in R._toolset_membership.items()}, \
        {k: set(v) for k, v in R._unsafe_in_channel.items()}
    R._registry.clear()
    R._toolset_membership.clear()
    R._unsafe_in_channel.clear()
    tool_execution._cache.clear()
    import openprogram.programs as programs
    monkeypatch.setattr(programs, "_exposed_set", lambda: None)
    hooks: dict = {"ran": lambda: None}

    @function(name="passage_probe", description="looks something up")
    def passage_probe() -> str:
        hooks["ran"]()
        return "found"

    yield hooks
    R._registry.clear()
    R._toolset_membership.clear()
    R._unsafe_in_channel.clear()
    tool_execution._cache.clear()
    R._registry.update(saved[0])
    R._toolset_membership.update(saved[1])
    R._unsafe_in_channel.update(saved[2])


def _owner_request(**fields) -> agent_turn_request.TurnRequest:
    from openprogram.runtime.permissions.authority import local_owner_authority

    return agent_turn_request.TurnRequest(**fields, **local_owner_authority())


def _turn(text: str, stream_fn, cancel_event=None, on_event=None):
    original = D._run_loop_blocking

    def wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **extra):
        return original(req=req, history=history, on_event=on_event, cancel_event=cancel_event,
                        stream_fn=stream, **extra)

    stream = stream_fn
    with patch.object(D, "_run_loop_blocking", wrapped):
        return D.process_user_turn(
            _owner_request(session_id="c1", user_text=text, agent_id="main", source="tui",
                           permission_mode="bypass"),
            cancel_event=cancel_event, on_event=on_event,
        )


async def _say(text: str, *, index: int = 0, before=()):
    """The stream events of one text block."""
    before = list(before)
    yield EventTextStart(content_index=index, partial=_message([*before, TextContent(text="")]))
    yield EventTextDelta(content_index=index, delta=text, partial=_message([*before, TextContent(text=text)]))
    yield EventTextEnd(content_index=index, content=text, partial=_message([*before, TextContent(text=text)]))


def _text_then_tool_then_text(first: str, second: str, seen: list | None = None):
    calls = {"n": 0}

    async def stream(model, context, options):
        calls["n"] += 1
        if seen is not None:
            seen.append(list(context.messages))
        yield EventStart(partial=_message([]))
        if calls["n"] == 1:
            async for event in _say(first):
                yield event
            yield EventDone(reason="toolUse", message=_message(
                [TextContent(text=first), ToolCall(id="call-1", name="passage_probe", arguments={})],
                stop_reason="toolUse"))
        else:
            async for event in _say(second):
                yield event
            yield EventDone(reason="stop", message=_message([TextContent(text=second)], stop_reason="stop"))
    return stream


def _frames(events: list[dict], kind: str) -> list[dict]:
    return [e["data"] for e in events if e.get("type") == "chat_response" and e["data"].get("type") == kind]


def _streamed(events: list[dict]) -> str:
    """The reply as a client that only reads text deltas builds it."""
    return "".join(stream_text(f["event"]) for f in _frames(events, "stream_event")
                   if f["event"].get("type") == "text")


def _reply(store: SessionDB) -> dict:
    return next(m for m in reversed(store.get_messages("c1")) if m["role"] == "assistant")


def _reloaded(store: SessionDB) -> SessionDB:
    """The same sessions read from disk by a process that never ran the turn."""
    return SessionDB(store.test_root)


# The rule


def test_passages_a_block_came_between_get_a_blank_line_and_a_continuous_stream_stays_as_it_is() -> None:
    tool = {"type": "tool", "tool": "bash", "tool_call_id": "a"}
    assert reply_text([{"type": "text", "text": "attaching it here now."}, tool,
                       {"type": "text", "text": "Created and attached."}]) \
        == "attaching it here now.\n\nCreated and attached."
    # One provider text stream cut into blocks (citations) is not touched.
    assert reply_text([{"type": "text", "text": "The sky is "}, {"type": "text", "text": "blue"},
                       {"type": "text", "text": "."}]) == "The sky is blue."
    # Whitespace already at the boundary is all the separation there is.
    assert reply_text([{"type": "text", "text": "One.\n"}, tool, {"type": "text", "text": "Two."}]) == "One.\nTwo."
    assert reply_text([{"type": "text", "text": "One."}, tool, {"type": "text", "text": " Two."}]) == "One. Two."
    # Reasoning that is shown and a steering message end a passage; reasoning
    # with only a signature is never drawn and ends nothing.
    assert reply_text([{"type": "text", "text": "A."}, {"type": "thinking", "text": "hm"},
                       {"type": "text", "text": "B."}]) == "A.\n\nB."
    assert reply_text([{"type": "text", "text": "A."}, {"type": "steering", "message_id": "s", "text": "wait"},
                       {"type": "text", "text": "B."}]) == "A.\n\nB."
    assert reply_text([{"type": "text", "text": "A"}, {"type": "thinking", "text": "", "signature": "sig"},
                       {"type": "text", "text": "B"}]) == "AB"
    # Several blocks between two passages are one blank line, and nothing
    # opens or closes the reply.
    assert reply_text([tool, {"type": "text", "text": "A."}, tool, {"type": "thinking", "text": "hm"}, tool,
                       {"type": "text", "text": "B."}, tool]) == "A.\n\nB."


def test_a_provider_message_reads_the_same_as_its_stored_blocks() -> None:
    message = _message([
        ThinkingContent(thinking="plan", thinking_signature="sig"),
        TextContent(text="Looking."),
        ToolCall(id="a", name="bash", arguments={}),
        TextContent(text="Done"),
        TextContent(text=" now."),
    ])
    assert reply_text(message.content) == "Looking.\n\nDone now."
    grown = ReplyText()
    assert [grown.add(block) for block in message.content] == ["", "Looking.", "", "\n\nDone", " now."]
    assert grown.text == "Looking.\n\nDone now."


# Through the turn


def test_text_then_a_tool_then_text_reads_as_two_paragraphs_everywhere(db, probe) -> None:
    events: list[dict] = []
    stored_as_streamed: list[tuple[str, str]] = []

    def on_event(env: dict) -> None:
        events.append(env)
        data = env.get("data") or {}
        if data.get("type") == "stream_event" and (data.get("event") or {}).get("type") == "text":
            # The row is written before its frame goes out.
            stored_as_streamed.append((_reply(db)["content"], _streamed(events)))

    seen: list = []
    result = _turn("find it", _text_then_tool_then_text("I am attaching it here now.", "Created and attached."),
                   on_event=on_event)

    whole = "I am attaching it here now.\n\nCreated and attached."
    assert result.final_text == whole
    assert _streamed(events) == whole
    assert stored_as_streamed == [("I am attaching it here now.",) * 2, (whole, whole)]
    frame = _frames(events, "result")[-1]
    assert frame["content"] == whole
    # The blocks are the model's own text: the separator is not in any of them.
    assert [b.get("text") for b in frame["blocks"] if b["type"] == "text"] == [
        "I am attaching it here now.", "Created and attached."]
    assert reply_text(frame["blocks"]) == whole
    assert _reply(db)["content"] == whole
    assert _reply(_reloaded(db))["content"] == whole

    # The next request sends each response as the provider wrote it.
    _turn("thanks", _text_then_tool_then_text("ok", "ok", seen))
    sent = [[(type(block).__name__, getattr(block, "text", None)) for block in message.content]
            for message in seen[0] if getattr(message, "role", None) == "assistant"]
    assert sent == [[("TextContent", "I am attaching it here now."), ("ToolCall", None)],
                    [("TextContent", "Created and attached.")]]


def test_the_stream_log_only_ever_adds_to_the_reply_and_a_cold_read_matches(db, probe) -> None:
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    db.create_session("c1", "main")
    writer = SessionNodeWriter(db, "c1")
    writer.append(Call(id="u", role="user", output="start"))
    writer.append(Call(id="u_reply", role="llm", output="", predecessor="u"))
    request = _owner_request(session_id="c1", agent_id="main", source="web", user_msg_id="u",
                             user_text="start", permission_mode="bypass")
    events: list[dict] = []
    ordered: list[dict] = []
    tap = make_stream_tap(on_event=events.append, req=request, assistant_msg_id="u_reply",
                          placeholder_inserted=True, agentic_tool_names=set())

    final, _usage, _calls = D._run_loop_blocking(
        req=request, history=[], on_event=tap, cancel_event=None,
        stream_fn=_text_then_tool_then_text("Looking.", "Found it."),
        assistant_msg_id="u_reply", ordered_blocks_out=ordered)
    tap.flush()

    whole = "Looking.\n\nFound it."
    assert final == whole == reply_text(ordered) == reply_text(tap.blocks) == _streamed(events)
    live = next(n for n in db.get_nodes("c1") if n.id == "u_reply")
    assert live.output == whole
    # The reply was never closed by a whole write: a process reading the log
    # cold folds the stream lines to the same text.
    cold = next(n for n in _reloaded(db).get_nodes("c1") if n.id == "u_reply")
    assert cold.output == whole
    assert [b.get("text") for b in json.loads(cold.metadata["extra"])["blocks"] if b["type"] == "text"] == [
        "Looking.", "Found it."]
    lines = [json.loads(line.rsplit("\t", 1)[0]) if "\t" in line else json.loads(line)
             for path in db.test_root.rglob("*.jsonl") for line in path.read_text().splitlines()
             if '"node.stream"' in line]
    stream_lines = [line["data"] for line in lines if line.get("type") == "node.stream"]
    assert stream_lines and not any("output" in data for data in stream_lines)
    assert "".join(data.get("output_tail", "") for data in stream_lines).endswith("\n\nFound it.")


def test_text_then_reasoning_then_text_in_one_response(db, probe) -> None:
    final = [TextContent(text="First."), ThinkingContent(thinking="let me think"),
             TextContent(text="Second"), TextContent(text=", continued.")]

    async def stream(model, context, options):
        yield EventStart(partial=_message([]))
        async for event in _say("First."):
            yield event
        yield EventThinkingStart(content_index=1, partial=_message(final[:1] + [ThinkingContent(thinking="")]))
        yield EventThinkingDelta(content_index=1, delta="let me think", partial=_message(final[:2]))
        yield EventThinkingEnd(content_index=1, content="let me think", partial=_message(final[:2]))
        async for event in _say("Second", index=2, before=final[:2]):
            yield event
        # The same stream of text, cut into a second block.
        async for event in _say(", continued.", index=3, before=final[:3]):
            yield event
        yield EventDone(reason="stop", message=_message(final, stop_reason="stop"))

    events: list[dict] = []
    result = _turn("think", stream, on_event=events.append)

    whole = "First.\n\nSecond, continued."
    assert result.final_text == whole == _streamed(events)
    assert _frames(events, "result")[-1]["content"] == whole
    assert _reply(db)["content"] == whole == _reply(_reloaded(db))["content"]
    separators = [f["event"].get("separator") for f in _frames(events, "stream_event")
                  if f["event"].get("type") == "text"]
    assert separators == [None, "\n\n", None]


def test_a_stop_in_the_second_passage_keeps_the_blank_line_before_it(db, probe) -> None:
    stop = threading.Event()
    calls = {"n": 0}

    async def stream(model, context, options):
        from openprogram.runtime.execution.run_control.wakeups import set_and_fire

        calls["n"] += 1
        yield EventStart(partial=_message([]))
        if calls["n"] == 1:
            async for event in _say("Looking."):
                yield event
            yield EventDone(reason="toolUse", message=_message(
                [TextContent(text="Looking."), ToolCall(id="call-1", name="passage_probe", arguments={})],
                stop_reason="toolUse"))
            return
        partial = _message([TextContent(text="")])
        yield EventTextStart(content_index=0, partial=partial)
        partial.content[0].text = "Found"
        yield EventTextDelta(content_index=0, delta="Found", partial=partial)
        # Added to the partial, never read by the loop: nobody was sent it.
        partial.content[0].text += " it."
        set_and_fire(stop)
        await asyncio.Event().wait()

    events: list[dict] = []
    result = _turn("find it", stream, cancel_event=stop, on_event=events.append)

    whole = "Looking.\n\nFound"
    reply = _reply(db)
    frame = _frames(events, "result")[-1]
    assert result.stopped is True and reply["status"] == "cancelled"
    assert result.final_text == whole == _streamed(events)
    assert frame["content"] == whole == reply_text(frame["blocks"])
    assert reply["content"] == whole == reply_text(json.loads(reply["extra"])["blocks"])
    assert _reply(_reloaded(db))["content"] == whole


def test_a_steer_between_two_passages_is_one_blank_line(db, probe) -> None:
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    db.create_session("c1", "main")
    writer = SessionNodeWriter(db, "c1")
    writer.append(Call(id="u", role="user", output="start"))
    writer.append(Call(id="u_reply", role="llm", output="", predecessor="u"))
    queue: list[dict] = []
    probe["ran"] = lambda: queue.append({"command_id": "input-1", "payload": {"message": "also check B"}})
    request = _owner_request(session_id="c1", agent_id="main", source="web", user_msg_id="u",
                             user_text="start", permission_mode="bypass")
    events: list[dict] = []
    ordered: list[dict] = []
    tap = make_stream_tap(on_event=events.append, req=request, assistant_msg_id="u_reply",
                          placeholder_inserted=True, agentic_tool_names=set())

    final, _usage, _calls = D._run_loop_blocking(
        req=request, history=[], on_event=tap, cancel_event=None,
        stream_fn=_text_then_tool_then_text("Checking A.", "A and B are fine."),
        assistant_msg_id="u_reply", ordered_blocks_out=ordered,
        execution_context={"canonical_execution": True, "steer_inputs": queue, "steer_consumed_ids": set()})
    tap.flush()

    assert [b["type"] for b in ordered] == ["text", "tool", "steering", "text"]
    whole = "Checking A.\n\nA and B are fine."
    assert final == whole == _streamed(events) == reply_text(ordered) == reply_text(tap.blocks)
    assert next(n for n in db.get_nodes("c1") if n.id == "u_reply").output == whole
    assert next(n for n in _reloaded(db).get_nodes("c1") if n.id == "u_reply").output == whole


def test_a_reply_that_fails_in_its_second_passage_shows_what_was_streamed(db, probe) -> None:
    calls = {"n": 0}

    async def stream(model, context, options):
        calls["n"] += 1
        yield EventStart(partial=_message([]))
        if calls["n"] == 1:
            async for event in _say("Looking."):
                yield event
            yield EventDone(reason="toolUse", message=_message(
                [TextContent(text="Looking."), ToolCall(id="call-1", name="passage_probe", arguments={})],
                stop_reason="toolUse"))
            return
        partial = _message([TextContent(text="")])
        yield EventTextStart(content_index=0, partial=partial)
        partial.content[0].text = "Found"
        yield EventTextDelta(content_index=0, delta="Found", partial=partial)
        raise ValueError("provider broke")

    events: list[dict] = []
    result = _turn("find it", stream, on_event=events.append)

    reply = _reply(db)
    assert result.failed is True
    assert _streamed(events) == "Looking.\n\nFound" == reply_text(json.loads(reply["extra"])["blocks"])
    # The failed row's transcript text is its passages, the error beside it.
    from openprogram.interfaces.server.transcript.rows import _assistant_row

    drawn = _assistant_row({**reply, "blocks": json.loads(reply["extra"])["blocks"]}, reply["id"], None, False)
    assert drawn["content"] == "Looking.\n\nFound"


# A reply that paused and went on


def test_a_resumed_reply_reads_as_every_passage_it_showed_with_one_blank_line_each(db) -> None:
    """A reply that paused for an answer goes on in the same row: the text
    the resumed loop returns is the text the tap's row holds, and neither
    repeats a separator the trace before the pause already has."""
    from types import SimpleNamespace

    from openprogram.runtime.conversation.turn_runtime.loop_runner import (
        _TurnTally, _seed_tally_from_continuation)
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    def tool(call_id: str, result) -> dict:
        return {"type": "tool", "tool": "passage_probe", "tool_call_id": call_id, "input": "{}",
                "result": result, "is_error": False}

    before = [{"type": "text", "text": "Checking A."}, tool("call-1", "found"),
              {"type": "text", "text": "Now B, which needs your word."}, tool("call-2", None)]
    paused = "Checking A.\n\nNow B, which needs your word."
    db.create_session("c1", "main")
    writer = SessionNodeWriter(db, "c1")
    writer.append(Call(id="u", role="user", output="start"))
    writer.append(Call(id="u_reply", role="llm", output=paused, predecessor="u",
                       metadata={"extra": json.dumps({"blocks": before, "stream_seq": 9})}))
    request = _owner_request(session_id="c1", agent_id="main", source="web", user_msg_id="u",
                             user_text="start", permission_mode="bypass")
    events: list[dict] = []
    tap = make_stream_tap(on_event=events.append, req=request, assistant_msg_id="u_reply",
                          placeholder_inserted=True, agentic_tool_names=set())

    def frame(event: dict) -> None:
        tap({"type": "chat_response", "data": {"type": "stream_event", "session_id": "c1",
                                               "msg_id": "u", "event": event}})

    frame({"type": "tool_result", "tool_call_id": "call-2", "tool": "passage_probe", "result": "allowed"})
    frame({"type": "text", "text": "B is "})
    frame({"type": "text", "text": "fine."})
    tap.flush()

    continuation = SimpleNamespace(
        display=tuple(before),
        assistant_message=_message([TextContent(text="Now B, which needs your word."),
                                    ToolCall(id="call-2", name="passage_probe", arguments={})],
                                   stop_reason="toolUse"),
        tool_results=())
    tally, ordered = _TurnTally(), []
    _seed_tally_from_continuation(tally, continuation, ordered)
    D.loop_runner._add_response_text(tally, _message([TextContent(text="B is fine.")], stop_reason="stop"))

    whole = paused + "\n\nB is fine."
    assert tally.reply.text == whole
    assert paused + _streamed(events) == whole
    assert [f["event"].get("separator") for f in _frames(events, "stream_event")
            if f["event"].get("type") == "text"] == ["\n\n", None]
    assert next(n for n in db.get_nodes("c1") if n.id == "u_reply").output == whole
    assert next(n for n in _reloaded(db).get_nodes("c1") if n.id == "u_reply").output == whole


# Any place a reply can end


_PIECES = ("A.", "B", " c", "d\n", "\n\ne", "f ", "\nh", "i\n\n", "中文。")


def _random_reply(rng) -> list[list[tuple[str, list[str]]]]:
    """Responses of text, shown reasoning and signature-only reasoning; all
    but the last end in a tool call."""
    responses = []
    for _ in range(rng.randint(1, 4)):
        parts = []
        for _ in range(rng.randint(0, 4)):
            kind = rng.choice(["text", "text", "text", "thinking", "signature"])
            parts.append((kind, [] if kind == "signature"
                          else [rng.choice(_PIECES) for _ in range(rng.randint(1, 3))]))
        responses.append(parts)
    responses[-1].append(("text", [rng.choice(["Z.", " z", "End"])]))
    return responses


def _content(parts) -> list:
    return [TextContent(text="".join(deltas)) if kind == "text"
            else ThinkingContent(thinking="".join(deltas), thinking_signature="sig")
            for kind, deltas in parts]


def _scripted(responses, *, cut: int, how: str, stop, voided: dict, shown: list):
    """A provider that streams ``responses``; after ``cut`` deltas it is
    stopped or fails. ``voided`` responses first stream text that a retry
    then takes back. ``shown`` gets every block the reader was sent."""
    from openprogram.models.providers.types import EventRetry
    from openprogram.runtime.execution.run_control.wakeups import set_and_fire

    state = {"call": 0, "deltas": 0}

    async def stream(model, context, options):
        number = state["call"]
        state["call"] += 1
        parts = responses[number]
        yield EventStart(partial=_message([]))
        for index, kind in enumerate(voided.get(number, ())):
            if kind == "text":
                yield EventTextStart(content_index=index, partial=_message([TextContent(text="")]))
                yield EventTextDelta(content_index=index, delta="VOID", partial=_message([TextContent(text="VOID")]))
            else:
                yield EventThinkingStart(content_index=index, partial=_message([ThinkingContent(thinking="")]))
                yield EventThinkingDelta(content_index=index, delta="void",
                                         partial=_message([ThinkingContent(thinking="void")]))
        if number in voided:
            yield EventRetry(attempt=1, reason="transport", restart=True)
            yield EventStart(partial=_message([]))
        done: list = []
        for index, (kind, deltas) in enumerate(parts):
            text = kind == "text"
            block = TextContent(text="") if text else ThinkingContent(thinking="", thinking_signature="sig")
            yield (EventTextStart if text else EventThinkingStart)(
                content_index=index, partial=_message([*done, block]))
            for delta in deltas:
                if text:
                    block.text += delta
                else:
                    block.thinking += delta
                shown.append((kind, delta))
                yield (EventTextDelta if text else EventThinkingDelta)(
                    content_index=index, delta=delta, partial=_message([*done, block]))
                state["deltas"] += 1
                if state["deltas"] == cut:
                    if how == "stop":
                        set_and_fire(stop)
                        await asyncio.Event().wait()
                    raise ValueError("provider broke")
            yield (EventTextEnd if text else EventThinkingEnd)(
                content_index=index, content=block.text if text else block.thinking,
                partial=_message([*done, block]))
            done.append(block)
        if number == len(responses) - 1:
            yield EventDone(reason="stop", message=_message(_content(parts), stop_reason="stop"))
        else:
            shown.append(("tool", ""))
            yield EventDone(reason="toolUse", message=_message(
                [*_content(parts), ToolCall(id=f"call-{number}", name="passage_probe", arguments={})],
                stop_reason="toolUse"))
    return stream


def _shown_text(shown: list) -> str:
    blocks: list[dict] = []
    for kind, text in shown:
        if kind in ("text", "thinking") and blocks and blocks[-1]["type"] == kind:
            blocks[-1]["text"] += text
        else:
            blocks.append({"type": kind, "text": text})
    return reply_text(blocks)


def _delta_reader(events: list[dict]) -> str:
    """The reply as a channel builds it: deltas with their separators, and a
    response sent again dropped back to where it began."""
    text, response_from = "", 0
    for frame in _frames(events, "stream_event"):
        event = frame["event"]
        if event.get("type") == "text":
            text += stream_text(event)
        elif event.get("type") == "tool_use":
            response_from = len(text)
        elif event.get("type") == "retry" and event.get("restart"):
            text = text[:response_from]
    return text


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(60))
def test_wherever_a_reply_ends_every_reader_has_the_same_text(db, probe, seed) -> None:
    """Stopped, failed, steered or sent again at any point: the stream, the
    result frame, the stored row, a cold reload and ``final_text`` agree."""
    import random

    from openprogram.interfaces.server.transcript.flat_rows import _text as searched_text
    from openprogram.interfaces.server.transcript.rows import _assistant_row

    rng = random.Random(seed)
    responses = _random_reply(rng)
    total = sum(len(deltas) for parts in responses for _kind, deltas in parts)
    how = rng.choice(["end", "stop", "error"])
    cut = rng.randint(1, total) if how != "end" else -1
    voided = {number: [rng.choice(["text", "thinking"]) for _ in range(rng.randint(1, 2))]
              for number in range(len(responses)) if rng.random() < 0.35}
    stop, shown, events, steers = threading.Event(), [], [], []
    if rng.random() < 0.4:
        def steer() -> None:
            steers.append({"command_id": f"input-{len(steers)}", "payload": {"message": "also this"}})
            shown.append(("steering", "also this"))
        probe["ran"] = steer
    stream = _scripted(responses, cut=cut, how=how, stop=stop, voided=voided, shown=shown)
    original = D._run_loop_blocking

    def run_loop(**arguments):
        arguments["execution_context"] = {
            **(arguments.get("execution_context") or {}),
            "canonical_execution": True, "steer_inputs": steers, "steer_consumed_ids": set()}
        return original(**{**arguments, "stream_fn": stream})

    with patch.object(D, "_run_loop_blocking", run_loop):
        result = D.process_user_turn(
            _owner_request(session_id="c1", user_text="go", agent_id="main", source="tui",
                           permission_mode="bypass"),
            cancel_event=stop, on_event=events.append)

    def drawn(reply: dict) -> str:
        blocks = json.loads(reply.get("extra") or "{}").get("blocks") or []
        return _assistant_row({**reply, "blocks": blocks}, reply["id"], None, False)["content"]

    reply, reloaded = _reply(db), _reply(_reloaded(db))
    blocks = json.loads(reply["extra"])["blocks"] if reply.get("extra") else []
    readers = {
        "shown": _shown_text(shown), "stream": _delta_reader(events),
        "blocks": reply_text(blocks), "transcript": drawn(reply), "reloaded transcript": drawn(reloaded),
    }
    if how != "error":
        # A failed reply's ``content`` is its error; its text is its blocks.
        frame = _frames(events, "result")[-1]
        readers.update({
            "final_text": result.final_text, "result frame": frame["content"],
            "result blocks": reply_text(frame["blocks"]), "row": reply["content"],
            "reload": reloaded["content"], "search": searched_text({**reply, "blocks": blocks}),
        })
    # ``final_text`` is trimmed at both ends; nothing else differs.
    assert len({text.strip() for text in readers.values()}) == 1, (readers, how, cut, voided, responses)
    assert "VOID" not in readers["blocks"]
