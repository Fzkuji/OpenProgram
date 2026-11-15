"""A turn that overflows, or grows past the threshold, compacts between its
requests and goes on from where it was (context/compaction.html,
"Reactive" and "Mid-turn").

- After an overflow only the request that overflowed is sent again: tools
  that already ran do not run again, and the retried request starts with
  the summary and still holds this turn's own messages.
- One compaction per request, two per turn: a second overflow of the same
  request ends the turn with the provider's error.
- A Stop during the compaction ends the turn as stopped.
- At a step boundary over the threshold, the history before the turn is
  compacted once, durably, and the turn continues.
- ``TurnCompaction`` compacts the chain before the turn's message and puts
  the turn's own messages after the newly rendered history.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    EventDone,
    EventError,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig

MODEL = Model(id="stub", name="stub", api="completion", provider="fake",
              base_url="https://example.invalid", context_window=200_000, max_tokens=8_000)
SUMMARY = UserMessage(content=[TextContent(text="The earlier part of this conversation was compacted")],
                      timestamp=0)
OVERFLOW = "prompt is too long: 250000 tokens > 200000 maximum"


def _reply(*blocks, stop="stop") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api="completion", provider="fake",
                            model="stub", stop_reason=stop, timestamp=1)


def _overflowed() -> AssistantMessage:
    return _reply(stop="error").model_copy(update={"error_message": OVERFLOW})


def _tool(executed: list):
    async def execute(call_id, _args, _cancel, _on_update):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text="listing of the repository")])
    return AgentTool(name="ls", label="ls", description="list", parameters={"type": "object"},
                     execute=execute)


def _run(script, compact_turn, *, cancel: asyncio.Event | None = None, executed=None):
    """Drive one turn through ``script`` (one entry per request)."""
    requests: list[list] = []
    executed = executed if executed is not None else []

    def stream_fn(_model, context, _options):
        requests.append(list(context.messages))
        step = script[len(requests) - 1]

        async def events():
            if step == "overflow":
                yield EventError(reason="error", error=_overflowed())
                return
            yield EventStart(partial=step)
            yield EventDone(reason="toolUse" if step.stop_reason == "toolUse" else "stop", message=step)
        return events()

    async def run():
        stream = agent_loop(
            [UserMessage(content="list the repo, then report", timestamp=1)],
            AgentContext(tools=[_tool(executed)], memory_prefetch="",
                         messages=[UserMessage(content="an earlier request", timestamp=0),
                                   _reply(TextContent(text="an earlier answer"))]),
            AgentLoopConfig(model=MODEL, convert_to_llm=lambda messages: messages,
                            compact_turn=compact_turn),
            cancel,
            stream_fn=stream_fn,
        )
        async for _ in stream:
            pass
        return await stream.result()

    return asyncio.run(asyncio.wait_for(run(), 10)), requests, executed


def _compactor(calls: list, *, result="summary", on_call=None):
    async def compact_turn(messages, trigger):
        calls.append(trigger)
        if on_call is not None:
            on_call()
        if result is None or trigger == "mid_turn":
            return None
        # The history before the turn becomes the summary; the turn's own
        # messages (from its prompt on) follow unchanged.
        own = messages[2:]
        return [SUMMARY, *own]
    return compact_turn


def test_after_an_overflow_only_the_failed_request_goes_again():
    calls: list[str] = []
    asked = _reply(ToolCall(id="c1", name="ls", arguments={}), stop="toolUse")
    messages, requests, executed = _run(
        [asked, "overflow", _reply(TextContent(text="here is the report"))], _compactor(calls))
    assert executed == ["c1"], "the tool that already ran does not run again"
    assert calls == ["mid_turn", "reactive"]
    assert len(requests) == 3
    retried = requests[2]
    assert retried[0] is SUMMARY
    assert [getattr(m, "role", None) for m in retried[1:]] == ["user", "assistant", "toolResult"]
    assert sum(1 for m in retried if m.role == "user"
               and "list the repo" in str(m.content)) == 1, "the user message is sent once"
    assert messages[-1].content[0].text == "here is the report"
    assert not any(getattr(m, "stop_reason", None) == "error" for m in messages)


def test_a_request_that_overflows_twice_ends_the_turn_with_the_error():
    calls: list[str] = []
    messages, requests, _ = _run(["overflow", "overflow", "overflow"], _compactor(calls))
    assert calls == ["reactive"], "one compaction per request"
    assert len(requests) == 2
    assert messages[-1].stop_reason == "error" and OVERFLOW in messages[-1].error_message


def test_a_compaction_that_changes_nothing_ends_the_turn_with_the_error():
    calls: list[str] = []
    messages, requests, _ = _run(["overflow"], _compactor(calls, result=None))
    assert calls == ["reactive"] and len(requests) == 1
    assert messages[-1].stop_reason == "error"


def test_a_stop_during_the_compaction_ends_the_turn_as_stopped():
    holder: dict = {}
    requests: list = []
    calls: list[str] = []

    async def compact_turn(messages, trigger):
        calls.append(trigger)
        holder["cancel"].set()  # the person presses Stop while it summarises
        return [SUMMARY, *messages]

    def stream_fn(*_args):
        requests.append(1)

        async def events():
            yield EventError(reason="error", error=_overflowed())
        return events()

    async def go():
        holder["cancel"] = asyncio.Event()
        stream = agent_loop(
            [UserMessage(content="go", timestamp=1)],
            AgentContext(tools=[], memory_prefetch=""),
            AgentLoopConfig(model=MODEL, convert_to_llm=lambda m: m, compact_turn=compact_turn),
            holder["cancel"],
            stream_fn=stream_fn,
        )
        async for _ in stream:
            pass
        return await stream.result()

    messages = asyncio.run(asyncio.wait_for(go(), 5))
    assert calls == ["reactive"]
    assert requests == [1], "nothing is sent after the stop"
    assert not any(getattr(m, "stop_reason", None) == "error" for m in messages)


def test_a_step_over_the_threshold_compacts_once_and_goes_on():
    calls: list[str] = []
    replaced: list[int] = []

    async def compact_turn(messages, trigger):
        calls.append(trigger)
        replaced.append(len(messages))
        return [SUMMARY, *messages[2:]]

    steps = [_reply(ToolCall(id=f"c{i}", name="ls", arguments={}), stop="toolUse") for i in range(3)]
    _, requests, executed = _run([*steps, _reply(TextContent(text="done"))], compact_turn)
    assert executed == ["c0", "c1", "c2"]
    assert calls == ["mid_turn"], "the turn's history is compacted once"
    assert requests[1][0] is SUMMARY and requests[3][0] is SUMMARY


# --- TurnCompaction over a real store -------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.runtime.context.model.Context.turn_store", staticmethod(lambda _sid: store))
    store.create_session("c", "main")
    prev = None
    for i in range(6):
        store.append_message("c", {"id": f"u{i}", "role": "user", "content": f"request {i} " + "detail " * 400,
                                   "predecessor": prev})
        store.append_message("c", {"id": f"a{i}", "role": "assistant", "content": f"answer {i} " + "finding " * 400,
                                   "predecessor": f"u{i}", "status": "done"})
        prev = f"a{i}"
    store.append_message("c", {"id": "u6", "role": "user", "content": "the new request", "predecessor": "a5"})
    store.append_message("c", {"id": "a6", "role": "assistant", "content": "", "predecessor": "u6",
                               "status": "running"})
    store.set_head("c", "a6")
    yield store
    store.close()


def test_the_turn_compaction_covers_the_history_before_the_turn(store, monkeypatch):
    from openprogram.runtime.context.engine import DefaultContextEngine
    from openprogram.runtime.conversation.turn_runtime.turn_compaction import TurnCompaction
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter

    engine = DefaultContextEngine()

    async def recap(**_kwargs):
        return "recap of the older turns"

    monkeypatch.setattr(engine.summarizer, "_llm_summary", recap)
    monkeypatch.setattr(engine, "_occupancy_tokens", lambda *_a, **_k: 50_000)
    seen_heads: list = []
    original = engine.compact

    async def compact(**kwargs):
        seen_heads.append(kwargs["head_id"])
        kwargs["keep_recent_tokens"] = 10
        return await original(**kwargs)

    monkeypatch.setattr(engine, "compact", compact)
    prefix = engine.prepare(agent={}, session={"id": "c"}, history=[], model=None, head_id="a6").agent_messages
    req = SimpleNamespace(session_id="c", user_msg_id="u6", background=False, history_override=None)
    own = [UserMessage(content="the new request", timestamp=1), _reply(TextContent(text="working"))]
    callback = TurnCompaction(engine=engine, req=req, db=store, agent_profile={}, model=None,
                              tools=[], system_prompt="", render_kwargs={}, turn_tip="a6",
                              prefix_len=len(prefix), on_event=lambda _e: None,
                              cancel_event=threading.Event())
    result = asyncio.run(callback([*prefix, *own], "reactive"))
    assert seen_heads == ["a5"], "the chain before the turn's message"
    assert result is not None and result[-2:] == own
    assert "compacted into this summary" in result[0].content[0].text
    assert len(result) < len(prefix) + len(own)
    graph = SessionNodeWriter(store, "c").load()
    summary = [n for n in graph.nodes.values() if (n.metadata or {}).get("covers_ids")]
    assert len(summary) == 1 and summary[0].metadata["trigger"] == "reactive"
    assert not {"u6", "a6"} & set(summary[0].metadata["covers_ids"])
    assert callback.prefix_len == len(result) - len(own)


def test_a_turn_without_its_own_history_gets_no_turn_compaction():
    from openprogram.runtime.conversation.turn_runtime.turn_compaction import turn_compaction_for

    for req in (SimpleNamespace(background=True, history_override=None),
                SimpleNamespace(background=False, history_override=[])):
        assert turn_compaction_for(req, turn_tip="a1") is None
