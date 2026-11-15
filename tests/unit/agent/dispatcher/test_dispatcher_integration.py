"""End-to-end integration tests for dispatcher.process_user_turn.

The pure unit tests in test_dispatcher.py mock _run_loop_blocking
entirely. These tests run the REAL _run_loop_blocking — including
the asyncio event loop, agent_loop() invocation, and the full
AgentEvent → chat_response envelope conversion — but with a fake
stream_fn that emits scripted AssistantMessageEvents instead of
hitting a provider over the network.

This is the closest we can get to "real" without paying the cost
of provider keys + network. Anything broken between dispatcher and
agent_loop would fail here; anything broken between agent_loop and
a specific provider stays out of scope (those have their own tests).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.session_db import SessionDB
from openprogram.models.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)


# ---------------------------------------------------------------------------
# Stream-fn fakes: produce scripted AssistantMessageEvent sequences
# ---------------------------------------------------------------------------

def _stub_model() -> Model:
    return Model(
        id="stub",
        name="stub",
        api="completion",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def _build_partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion",
        provider="openai",
        model="stub",
        timestamp=int(time.time() * 1000),
    )


def _build_final(text: str, *, input_tokens: int = 10,
                 output_tokens: int = 4) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="completion",
        provider="openai",
        model="stub",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


def make_text_stream_fn(chunks: list[str]):
    """A stream_fn that emits text deltas for `chunks` then EventDone.

    Signature must match `StreamFn` Protocol — agent_loop calls it as
    `fn(model, llm_context, stream_opts)` and iterates the resulting
    async generator.
    """
    full_text = "".join(chunks)

    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        partial = _build_partial("")
        yield EventStart(partial=partial)

        # Text section
        partial = _build_partial("")
        yield EventTextStart(content_index=0, partial=partial)
        accum = ""
        for chunk in chunks:
            accum += chunk
            partial = _build_partial(accum)
            yield EventTextDelta(content_index=0, delta=chunk, partial=partial)
        partial = _build_partial(accum)
        yield EventTextEnd(content_index=0, content=accum, partial=partial)

        yield EventDone(reason="stop", message=_build_final(full_text))

    return _fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr(
        "openprogram.runtime.context.session_db.default_db",
        lambda: db,
    )
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store",
                        lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    return db


@pytest.fixture
def captured() -> list[dict]:
    return []


@pytest.fixture
def collector(captured: list[dict]):
    return captured.append


# Inject a stub Model so dispatcher's _resolve_model doesn't need to
# touch the model registry at all.
@pytest.fixture(autouse=True)
def stub_model_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())


# Likewise stub the agent profile loader: we don't need real agents.
@pytest.fixture(autouse=True)
def stub_agent_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        D, "_load_agent_profile",
        lambda agent_id: {"id": agent_id, "system_prompt": "you are helpful"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_frozen_verifier_profile_reaches_real_loop_and_blocks_injected_browser(tmp_db, monkeypatch):
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.programs import get_agent_tool
    tmp_db.create_session("verifier", "main")
    tmp_db.append_message("verifier", {"id": "origin_u", "role": "user", "content": "update"})
    tmp_db.append_message("verifier", {
        "id": "origin_a", "role": "assistant", "content": "prepared", "predecessor": "origin_u",
    })
    tmp_db.update_session("verifier", head_id="origin_a")
    profile = {"id": "main", "system_prompt": "Frozen verifier", "tools": []}
    req = agent_turn_request.TurnRequest(
        session_id="verifier", user_text="check", agent_id="main", source="self_update_verify",
        profile_snapshot=profile, tools_override=[], model_override="frozen/model",
        branch_from=None, spawn_caller="origin_a", advance_head=False,
    )
    profile["system_prompt"] = "mutated"
    monkeypatch.setattr(D, "_load_agent_profile", lambda _: pytest.fail("loaded mutable profile"))
    original_model = D._resolve_model
    def resolve(profile, override=None):
        assert profile["system_prompt"] == "Frozen verifier"
        return original_model(profile, override)
    monkeypatch.setattr(D, "_resolve_model", resolve)
    monkeypatch.setattr(loop_runner, "_configure_browser_tool", lambda *_, **_kwargs: ([get_agent_tool("bash")], True))
    text_stream = make_text_stream_fn(["verified"])
    async def stream(model, context, options):
        assert not context.tools
        async for event in text_stream(model, context, options):
            yield event
    original_loop = D._run_loop_blocking
    monkeypatch.setattr(D, "_run_loop_blocking", lambda **kwargs: original_loop(**kwargs, stream_fn=stream))
    result = D.process_user_turn(req)
    assert not result.failed, result.error
    assert result.final_text == "verified"
    assert tmp_db.get_session("verifier")["head_id"] == "origin_a"
    messages = {row["id"]: row for row in tmp_db.get_messages("verifier")}
    assert not messages[result.user_msg_id]["predecessor"]
    assert messages[result.user_msg_id]["caller"] == "origin_a"
    assert messages[result.user_msg_id]["source"] == "self_update_verify"


def test_verifier_tools_reach_the_provider_without_tool_search(tmp_db, monkeypatch):
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.programs import get_agent_tool
    observe = get_agent_tool("self_update_observe")
    assert observe._defer, "The registry keeps the observer deferred for ordinary turns"
    tmp_db.create_session("verifier", "main")
    req = agent_turn_request.TurnRequest(
        session_id="verifier", user_text="check", agent_id="main", source="self_update_verify",
        profile_snapshot={"id": "main", "system_prompt": "Frozen verifier", "tools": []},
        tools_override=[], model_override="frozen/model", branch_from=None, advance_head=False,
    )
    monkeypatch.setattr(loop_runner, "_configure_browser_tool",
                        lambda *_, **_kwargs: ([get_agent_tool("read"), observe], False))
    text_stream = make_text_stream_fn(["verified"])
    async def stream(model, context, options):
        assert {tool.name for tool in context.tools} == {"read", "self_update_observe"}
        async for event in text_stream(model, context, options):
            yield event
    original_loop = D._run_loop_blocking
    monkeypatch.setattr(D, "_run_loop_blocking", lambda **kwargs: original_loop(**kwargs, stream_fn=stream))
    result = D.process_user_turn(req)
    assert not result.failed, result.error
    assert observe._defer, "Promotion copies the tool instead of changing the registry"


def test_real_loop_text_only(tmp_db: SessionDB, captured, collector) -> None:
    """One full turn with a streaming text reply, no tools.

    Verifies the whole dispatcher → agent_loop → fake stream_fn →
    event-conversion path runs cleanly and ends with the right text.
    """
    fake_stream = make_text_stream_fn(["Hel", "lo,", " wor", "ld"])

    # Patch _run_loop_blocking's default stream_fn=None signature: we
    # can't pass stream_fn into process_user_turn directly, so we wrap.
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
            on_event=collector,
        )

    assert result.failed is False
    assert result.final_text == "Hello, world"
    # Usage propagated from EventDone's message.usage
    assert result.usage["input_tokens"] >= 0
    assert result.usage["output_tokens"] >= 0
    assert result.usage["provider_request_count"] == 1
    assert result.usage["agent_iteration_count"] == 1

    # SessionDB should hold both turns
    msgs = tmp_db.get_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello, world"
    assert msgs[1]["execution_kind"] == "agent"
    assert msgs[1]["provider_request_count"] == 1
    assert msgs[1]["agent_iteration_count"] == 1

    # Stream events were forwarded to clients
    text_events = [
        e for e in captured
        if e["type"] == "chat_response"
        and e["data"].get("type") == "stream_event"
        and e["data"]["event"]["type"] == "text"
    ]
    deltas = "".join(e["data"]["event"]["text"] for e in text_events)
    assert deltas == "Hello, world"

    # Final result envelope
    final = [
        e for e in captured
        if e["type"] == "chat_response"
        and e["data"].get("type") == "result"
    ]
    assert len(final) == 1
    assert final[0]["data"]["content"] == "Hello, world"
    # The result carries the reply as a reload would show it.
    assert final[0]["data"]["assistant_msg_id"] == msgs[1]["id"]
    from openprogram.interfaces.server.persistence import aggregate_tool_messages
    reloaded = aggregate_tool_messages(tmp_db.get_messages("c1"))[1]
    assert final[0]["data"]["blocks"] == reloaded.get("blocks", [])
    # Every stream frame of the reply is numbered in order.
    seqs = [e["data"]["seq"] for e in captured
            if e["type"] == "chat_response" and e["data"].get("type") == "stream_event"]
    assert seqs == list(range(1, len(seqs) + 1))


def test_stream_tap_records_the_seq_its_content_reflects(tmp_db: SessionDB) -> None:
    """A reload mid-turn knows which stream frames its row already holds."""
    import json
    from openprogram.interfaces.server.persistence import aggregate_tool_messages
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    tmp_db.create_session("seq", "main")
    writer = SessionNodeWriter(tmp_db, "seq")
    writer.append(Call(id="u", role="user", output="hi"))
    writer.append(Call(id="u_reply", role="llm", output="", predecessor="u"))
    req = agent_turn_request.TurnRequest(
        session_id="seq", user_text="hi", agent_id="main", source="web", user_msg_id="u")

    def frame(event, msg_id="u", session_id="seq"):
        return {"type": "chat_response", "data": {
            "type": "stream_event", "session_id": session_id, "msg_id": msg_id,
            "event": event}}

    sent: list[dict] = []
    tap = make_stream_tap(on_event=sent.append, req=req, assistant_msg_id="u_reply",
                          placeholder_inserted=True, agentic_tool_names=set())
    tap(frame({"type": "text", "text": "Hel"}))
    tap(frame({"type": "text", "text": "x"}, msg_id="child", session_id="other"))
    tap(frame({"type": "text", "text": "lo"}))

    assert [f["data"].get("seq") for f in sent] == [1, None, 2]
    row = next(m for m in aggregate_tool_messages(tmp_db.get_messages("seq"))
               if m["id"] == "u_reply")
    assert (row["content"], row["stream_seq"]) == ("Hello", 2)

    # A resumed execution continues the reply's numbering.
    resumed: list[dict] = []
    tap = make_stream_tap(on_event=resumed.append, req=req, assistant_msg_id="u_reply",
                          placeholder_inserted=True, agentic_tool_names=set())
    tap(frame({"type": "text", "text": "!"}))
    assert resumed[0]["data"]["seq"] == 3
    node = next(n for n in tmp_db.get_nodes("seq") if n.id == "u_reply")
    assert json.loads(node.metadata["extra"])["stream_seq"] == 3


def test_real_loop_persists_session_meta(tmp_db: SessionDB) -> None:
    fake_stream = make_text_stream_fn(["done"])

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return D._run_loop_blocking.__wrapped__(  # type: ignore[attr-defined]
            req=req, history=history, on_event=on_event,
            cancel_event=cancel_event, stream_fn=fake_stream,
        ) if hasattr(D._run_loop_blocking, "__wrapped__") else None

    # Use the simpler patched approach
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _w):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                          source="wechat", peer_display="alice"),
        )
    sess = tmp_db.get_session("c1")
    assert sess["source"] == "wechat"
    assert sess["channel"] == "wechat"
    assert sess["head_id"] == result.assistant_msg_id


def test_real_loop_history_replay(tmp_db: SessionDB, captured, collector) -> None:
    """A second turn should see the first turn's user+assistant in
    the AgentContext.messages list when the loop runs."""

    # First turn
    orig = D._run_loop_blocking

    fake_stream = make_text_stream_fn(["first"])
    def _w1(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream)
    with patch.object(D, "_run_loop_blocking", _w1):
        D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
        )

    # Second turn — capture context.messages length seen by stream_fn
    seen_message_count = []

    async def _capturing_stream_fn(model, context, options):
        seen_message_count.append(len(context.messages))
        partial = _build_partial("")
        yield EventStart(partial=partial)
        partial = _build_partial("ok")
        yield EventTextStart(content_index=0, partial=partial)
        yield EventTextDelta(content_index=0, delta="ok", partial=partial)
        yield EventTextEnd(content_index=0, content="ok", partial=partial)
        yield EventDone(reason="stop", message=_build_final("ok"))

    def _w2(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing_stream_fn)
    with patch.object(D, "_run_loop_blocking", _w2):
        D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="follow up", agent_id="main", source="tui"),
        )

    # Context at LLM call time should have prior history (user, assistant)
    # plus the new user prompt = at least 3 messages.
    assert seen_message_count, "stream_fn should have been called"
    assert seen_message_count[0] >= 3


def test_caller_forks_sibling_branch(
    tmp_db: SessionDB, captured, collector,
) -> None:
    """User retries an old turn → dispatcher forks a sibling branch off
    the original parent. Old messages stay in the DB; the active branch
    (head_id walked back) only includes the new fork.

    This is the contract that retry / edit flows in webui rely on."""
    orig = D._run_loop_blocking

    # Turn 1: first message → reply "alpha"
    s1 = make_text_stream_fn(["alpha"])
    def _w1(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=s1)
    with patch.object(D, "_run_loop_blocking", _w1):
        r1 = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="ask one", agent_id="main", source="tui"),
        )

    # Turn 2: branch-fork from BEFORE turn 1's user message
    # (caller=None recreates the root-level fork case — matches
    # context/git/dag.py's "first-turn retry" semantics where the
    # forked branch shares the conversation root, not turn 1's user).
    s2 = make_text_stream_fn(["beta"])
    def _w2(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=s2)
    with patch.object(D, "_run_loop_blocking", _w2):
        r2 = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="ask one (retry)",
                          agent_id="main", source="tui",
                          branch_from=None),  # root-level fork
        )

    # Storage layer keeps both branches (4 messages total)
    all_msgs = tmp_db.get_messages("c1")
    assert len(all_msgs) == 4

    # Active branch (head walked back) should only contain the SECOND
    # turn — head moved to its assistant message after turn 2.
    active = tmp_db.get_branch("c1")
    active_ids = [m["id"] for m in active]
    assert active_ids == [r2.user_msg_id, r2.assistant_msg_id]

    # Turn 1's user/assistant messages still findable via get_messages
    # but not on the active branch.
    by_id = {m["id"]: m for m in all_msgs}
    assert r1.user_msg_id in by_id
    assert r1.assistant_msg_id in by_id


@pytest.mark.parametrize("override, sees_conversation", [([], False), (None, True)])
def test_a_clean_run_does_not_read_the_conversation(
    tmp_db: SessionDB, captured, collector, override, sees_conversation,
) -> None:
    """``history_override=[]`` (scheduled task, clean Job or sub-agent) starts
    from nothing; without it the turn reads the conversation as before."""
    seen_messages: list = []

    async def _capturing(model, ctx, opts):
        seen_messages.append(list(ctx.messages))
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("done"))

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing)

    tmp_db.create_session("c1", "main", title="t")
    tmp_db.append_message("c1", {
        "id": "x1", "role": "user", "content": "earlier conversation",
        "timestamp": 1.0, "predecessor": None,
    })
    tmp_db.append_message("c1", {
        "id": "x2", "role": "assistant", "content": "earlier reply",
        "timestamp": 2.0, "predecessor": "x1",
    })
    tmp_db.set_head("c1", "x2")

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="next",
                          agent_id="main", source="tui",
                          history_override=override),
        )

    assert seen_messages
    rendered = repr(seen_messages[0])
    assert ("earlier conversation" in rendered) is sees_conversation
    assert "next" in rendered


def test_user_already_persisted_skips_duplicate_user_msg(
    tmp_db: SessionDB, captured, collector,
) -> None:
    """webui pre-appends the user message before kicking off the
    dispatcher in a worker thread (so the WS chat_ack fires first).
    With user_already_persisted=True, dispatcher must NOT write a
    second user row, and agent_loop_continue should be used so the
    LLM context doesn't double the user turn."""
    # Pre-seed SessionDB: caller already wrote user msg + advanced head
    tmp_db.create_session("c1", "main", title="t")
    tmp_db.append_message("c1", {
        "id": "uExt", "role": "user", "content": "hello",
        "timestamp": 1.0, "predecessor": None,
    })
    tmp_db.set_head("c1", "uExt")
    from openprogram.runtime.conversation.turn_lifecycle import insert_placeholder
    assert insert_placeholder(
        tmp_db, "c1", "uExt_reply", "uExt", "web",
    )

    seen_messages: list = []

    async def _capturing(model, ctx, opts):
        seen_messages.append(list(ctx.messages))
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final("ack"))

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_capturing)

    with patch.object(D, "_run_loop_blocking", _w):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(
                session_id="c1", user_text="hello",
                agent_id="main", source="web",
                user_msg_id="uExt",
                user_already_persisted=True,
            ),
            on_event=collector,
        )

    # SessionDB still has exactly ONE user msg (no duplicate)
    msgs = tmp_db.get_messages("c1")
    user_rows = [m for m in msgs if m["role"] == "user"]
    assert len(user_rows) == 1
    assert user_rows[0]["id"] == "uExt"
    # Plus an assistant reply added by dispatcher
    assistant_rows = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_rows) == 1

    # context.messages seen by the LLM should have exactly 1 user
    # entry — not duplicated.
    assert seen_messages
    user_count = 0
    for m in seen_messages[0]:
        if hasattr(m, "role") and m.role == "user":
            user_count += 1
    assert user_count == 1, f"expected 1 user msg in LLM context, got {user_count}"

    # No chat_ack emitted (caller is responsible for that)
    acks = [e for e in captured if e.get("type") == "chat_ack"]
    assert len(acks) == 0

    assert result.user_msg_id == "uExt"
    assert result.failed is False


def test_provider_error_persists_as_system_message(
    tmp_db: SessionDB, captured, collector,
) -> None:
    """If stream_fn raises, dispatcher should catch and emit an error
    envelope, NOT crash the worker."""

    async def _angry_stream(model, context, options):
        # Yield once so async generator semantics are exercised, then raise.
        if False:  # pragma: no cover
            yield None
        raise RuntimeError("provider boom")

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_angry_stream)

    with patch.object(D, "_run_loop_blocking", _w):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
            on_event=collector,
        )

    assert result.failed is True
    assert "boom" in (result.error or "").lower()

    err_events = [
        e for e in captured
        if e["type"] == "chat_response" and e["data"].get("type") == "error"
    ]
    assert len(err_events) == 1
    assert "boom" in err_events[0]["data"]["content"].lower()


def make_error_stream_fn(error_message: str, *, error_reason: str | None = None):
    """A stream_fn that fails mid-stream the way a provider 4xx does:
    EventStart then EventError carrying stop_reason='error'."""
    from openprogram.models.providers.types import EventError

    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_build_partial(""))
        err = AssistantMessage(
            content=[TextContent(text="")],
            api="completion",
            provider="deepseek",
            model="stub",
            usage=Usage(),
            stop_reason="error",
            error_message=error_message,
            error_reason=error_reason,
            timestamp=int(time.time() * 1000),
        )
        yield EventError(reason="error", error=err)

    return _fn


def test_real_loop_stream_error_surfaces_to_client(
    tmp_db: SessionDB, captured, collector,
) -> None:
    """A provider error EVENT (HTTP 4xx surfaced in-stream, no exception)
    must fail the turn and reach the client as an error envelope — not
    end as a silent success with an empty assistant bubble."""
    fake_stream = make_error_stream_fn(
        "Error code: 402 - Insufficient Balance",
        error_reason="authz",
    )
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="cerr", user_text="hi",
                          agent_id="main", source="tui"),
            on_event=collector,
        )

    assert result.failed is True
    assert "Insufficient Balance" in (result.error or "")
    assert result.error_reason == "authz"

    error_envelopes = [
        e for e in captured
        if e["type"] == "chat_response" and e["data"].get("type") == "error"
    ]
    assert len(error_envelopes) == 1
    assert "Insufficient Balance" in error_envelopes[0]["data"]["content"]
