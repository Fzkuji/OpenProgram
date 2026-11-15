"""Context Assembly — dag/overview.md §7.

Three properties, one per section of the design:

A. ONE assembler. ``context.build_system_prompt`` is the single producer;
   what the dispatcher puts on ``AgentContext.system_prompt`` and what the
   engine budgets are the SAME string, not two independent assemblies.
B. The prompt is RECORDED. A ``context/system_prompt`` node is appended when
   the assembled text's hash moves, and not when it doesn't; those nodes are
   machinery and stay out of the chat views.
C. Memory prefetch lives in the USER TURN, so the system prompt is
   byte-stable across turns and the provider prefix cache survives.
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
from openprogram.runtime.context import components as C
from openprogram.runtime.context.system_prompt_node import (
    NODE_NAME,
    is_context_node,
    latest_recorded_prompt,
    prompt_hash,
    record_system_prompt,
)
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


# Fixtures / fakes (mirrors test_dispatcher_integration.py)

def _stub_model() -> Model:
    return Model(id="stub", name="stub", api="completion", provider="openai",
                 base_url="https://api.openai.com/v1")


def _build_partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000), usage=Usage(),
    )


def _final(text: str) -> AssistantMessage:
    msg = _build_partial(text)
    msg.usage = Usage(input_tokens=1, output_tokens=1)
    return msg


def _capturing_stream(sink: list):
    """A stream_fn that records the wire Context of every provider call."""
    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        sink.append(context)
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta="ok",
                             partial=_build_partial("ok"))
        yield EventTextEnd(content_index=0, content="ok",
                           partial=_build_partial("ok"))
        yield EventDone(reason="stop", message=_final("ok"))
    return _fn


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())


@pytest.fixture(autouse=True)
def no_memory_prefetch(monkeypatch: pytest.MonkeyPatch):
    """Default: memory recalls nothing, so tests that don't care about
    prefetch see a clean prompt. The prefetch tests patch it themselves."""
    class _Provider:
        def search(self, _text, **_kw): return ""
        def system_prompt(self): return ""
    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: _Provider())


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.parameters = {}


def _run_turn(text: str, sink: list, *, session_id="s1", tools=None):
    """One full dispatcher turn against a capturing fake provider."""
    orig = D._run_loop_blocking
    stream = _capturing_stream(sink)

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        res = D.process_user_turn(agent_turn_request.TurnRequest(
            session_id=session_id, user_text=text, agent_id="main",
            source="tui", tools_override=tools,
        ))
    # The turn has to actually finish. An event the provider schema
    # rejects makes every assertion below read a half-built turn that
    # died in the error path — and still pass, because the wire context
    # was captured before the stream blew up.
    assert not res.failed, res.error
    assert res.final_text == "ok"
    return res


# A. One assembler

def test_assembler_is_deterministic_for_the_same_inputs():
    agent = {"id": "main", "name": "bot", "system_prompt": "INLINE"}
    assert C.build_system_prompt(agent) == C.build_system_prompt(agent)


def test_the_working_directory_rides_the_environment_block():
    """The tool-runtime block the dispatcher used to append is part of the
    environment component; the tool list itself travels as schemas."""
    with_tools = C.build_system_prompt({"id": "main"}, tools=[_Tool("bash")])
    without = C.build_system_prompt({"id": "main"})
    assert "Working directory:" in with_tools and "Working directory:" in without
    assert "Available tools for this turn" not in with_tools


def test_assembler_absorbs_the_plan_mode_block():
    on = C.build_system_prompt({"id": "main"}, plan_mode=True)
    off = C.build_system_prompt({"id": "main"}, plan_mode=False)
    assert "<plan-mode>" in on and "exit_plan_mode" in on
    assert "<plan-mode>" not in off


def test_skills_index_is_in_the_assembled_prompt():
    """The skill system needs its index in the prompt to trigger on-demand
    loading — the dispatcher's hand-rolled prompt never had it."""
    l1 = {c.name for c in C._REGISTRY["L1"]}
    assert "capabilities" in l1


def test_dispatcher_sends_exactly_what_the_assembler_produced(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
):
    """Three-way identity: assembler output == AgentContext.system_prompt
    == the string the budget counted."""
    profile = {"id": "main", "system_prompt": "INLINE", "tools": []}
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: dict(profile))
    # The turn binds the session's project directory; AGENTS.md read there
    # would differ from the test process's cwd.
    monkeypatch.setattr("openprogram.runtime.context.agents_md.chain", lambda cwd=None: [])
    monkeypatch.setattr(C, "environment_block", lambda *a, **k: "<environment/>")

    budgeted: list[str] = []
    from openprogram.runtime.context.engine import ContextEngine  # noqa: F401
    import openprogram.runtime.context.budget as _budget
    orig_allocate = _budget.BudgetAllocator.allocate

    def _spy(self, *, context_window, system_prompt, history, tools=None, **kw):
        budgeted.append(system_prompt)
        return orig_allocate(self, context_window=context_window,
                             system_prompt=system_prompt, history=history,
                             tools=tools, **kw)

    monkeypatch.setattr(_budget.BudgetAllocator, "allocate", _spy)
    # The conversation's own turn keeps tools its profile does not list
    # (the browser screen tool, refused without a Page), so the assembler
    # is checked against the inputs this turn handed it.
    assembled: list = []
    orig_build = C.build_system_prompt

    def _build_spy(*args, **kwargs):
        out = orig_build(*args, **kwargs)
        assembled.append((args, kwargs, out))
        return out

    monkeypatch.setattr(C, "build_system_prompt", _build_spy)

    sink: list = []
    _run_turn("hi", sink)

    assert sink, "provider was never called"
    wire = sink[0].system_prompt
    assert budgeted, "budget never ran"
    # The engine budgeted the exact string that shipped.
    assert budgeted[0] == wire
    # And that string is the assembler's own output for this turn, the same
    # again when assembled from the same inputs.
    assert assembled, "the assembler never ran"
    args, kwargs, out = assembled[0]
    assert args[0] == profile
    assert wire == out == orig_build(*args, **kwargs)


def test_engine_budgets_the_prompt_it_was_handed():
    """prepare(system_prompt=...) must not re-assemble a second string."""
    from openprogram.runtime.context import resolve_engine_for
    engine = resolve_engine_for({"id": "main"})
    prep = engine.prepare(agent={"id": "main"}, session={"id": "nope"},
                          history=[], model=_stub_model(),
                          system_prompt="SENTINEL PROMPT")
    assert prep.system_prompt == "SENTINEL PROMPT"


def test_exec_runtime_uses_the_same_assembler():
    """A model call inside a function body gets the project background too."""
    from openprogram.runtime.agent.runtime import _exec_system_prompt
    out = _exec_system_prompt("FN INLINE", [_Tool("read")])
    assert "FN INLINE" in out
    assert "<environment>" in out
    # ...and it is the assembler that produced it, not a bespoke concat.
    assert "# How you work" in out


# B. The prompt is recorded as a node

def test_record_appends_on_hash_change_and_is_idempotent(tmp_db: SessionDB):
    sid = "rec1"
    first = record_system_prompt(tmp_db, sid, "PROMPT A")
    assert first, "first prompt must be recorded"
    assert latest_recorded_prompt(tmp_db, sid) == "PROMPT A"

    # Same text → same hash → no second node.
    assert record_system_prompt(tmp_db, sid, "PROMPT A") is None

    # Changed text → a new node, and the latest wins.
    second = record_system_prompt(tmp_db, sid, "PROMPT B")
    assert second and second != first
    assert latest_recorded_prompt(tmp_db, sid) == "PROMPT B"

    recorded = [n for n in tmp_db.get_nodes(sid) if n.name == NODE_NAME]
    assert len(recorded) == 2
    assert prompt_hash("PROMPT B") == recorded[-1].metadata["prompt_hash"]


def test_recorded_node_shape_matches_the_design(tmp_db: SessionDB):
    sid = "rec2"
    record_system_prompt(tmp_db, sid, "PROMPT")
    node = [n for n in tmp_db.get_nodes(sid) if n.name == NODE_NAME][0]
    assert node.role == "code"
    assert node.caller == "ROOT"
    assert not node.predecessor      # code nodes carry no conversation edge
    assert node.output == "PROMPT"


def test_recording_never_moves_the_branch_head(tmp_db: SessionDB):
    sink: list = []
    _run_turn("hi", sink, session_id="rec3")
    head_before = (tmp_db.get_session("rec3") or {}).get("head_id")
    record_system_prompt(tmp_db, "rec3", "A DIFFERENT PROMPT")
    assert (tmp_db.get_session("rec3") or {}).get("head_id") == head_before


def test_toolset_change_keeps_the_recorded_prompt(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "",
                                          "tools": []})
    sink: list = []
    _run_turn("one", sink, session_id="tc1", tools=["read"])
    after_first = [n for n in tmp_db.get_nodes("tc1") if n.name == NODE_NAME]
    assert len(after_first) == 1

    # Same toolset → same prompt → no new node.
    _run_turn("two", sink, session_id="tc1", tools=["read"])
    assert len([n for n in tmp_db.get_nodes("tc1")
                if n.name == NODE_NAME]) == 1

    # A different loaded toolset changes the schemas, not the prompt: the
    # tool list is no longer repeated in the text, so the cached prefix holds.
    _run_turn("three", sink, session_id="tc1", tools=["read", "glob"])
    after_change = [n for n in tmp_db.get_nodes("tc1") if n.name == NODE_NAME]
    assert len(after_change) == 1


def test_context_nodes_are_hidden_from_the_chat_views(tmp_db: SessionDB):
    sink: list = []
    _run_turn("hi", sink, session_id="hid1")
    record_system_prompt(tmp_db, "hid1", "A DIFFERENT PROMPT")

    # Raw view has it...
    assert any(n.name == NODE_NAME for n in tmp_db.get_nodes("hid1"))
    # ...but no conversation view does.
    assert not any(is_context_node(m) for m in tmp_db.get_messages("hid1"))
    assert not any(is_context_node(m) for m in tmp_db.get_branch("hid1"))
    # And it is never offered as a branch to check out.
    branch_ids = {b["head_msg_id"] for b in tmp_db.list_branches("hid1")}
    prompt_ids = {n.id for n in tmp_db.get_nodes("hid1")
                  if n.name == NODE_NAME}
    assert not (branch_ids & prompt_ids)


# C. Memory prefetch lives in the user turn

@pytest.fixture
def fake_prefetch(monkeypatch: pytest.MonkeyPatch):
    """Memory that recalls a different block for every query — the shape
    that used to poison the system prompt on every single turn."""
    class _Provider:
        # Signature mirrors MemoryBackend.search: the dispatcher swallows a
        # TypeError here, so a stub that drifts from the real interface
        # fails as "no memory recalled" rather than as a broken stub.
        def search(self, text, *, session_id="", tier=None):
            return f"<memory-context>\nrecalled for: {text}\n</memory-context>"
        def system_prompt(self, *, tier=None): return ""
        def write(self, *a, **kw): return None
    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: _Provider())
    # The execution wrapper and the streaming read check consult the saved
    # Agent independently of D._load_agent_profile. Keep their real policy
    # checks enabled, but give this fake backend a matching, isolated policy.
    from openprogram.runtime.agent_registry import manager
    main = manager.AgentSpec(
        id="main", memory_policy_epoch=0,
        memory={"mode": "read_write", "read_spaces": ["legacy_global"],
                "write_space": "legacy_global"},
    )
    original_get = manager.get
    monkeypatch.setattr(manager, "get", lambda agent_id: main if agent_id == "main" else original_get(agent_id))
    monkeypatch.setattr("openprogram.state.memory.is_enabled", lambda: True)


def test_prefetch_renders_in_the_user_message_not_the_system_prompt(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch, fake_prefetch,
):
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "",
                                          "tools": []})
    sink: list = []
    _run_turn("what did we decide", sink, session_id="pf1")

    ctx = sink[0]
    assert "<memory-context>" not in (ctx.system_prompt or "")

    last_user = [m for m in ctx.messages if getattr(m, "role", "") == "user"][-1]
    text = _text_of(last_user)
    assert "<memory-context>" in text
    assert "recalled for: what did we decide" in text
    # The block is a PREFIX — the user's own words follow it.
    assert text.index("<memory-context>") < text.index("what did we decide")


@pytest.mark.parametrize("query, recalled", [("the standing goal", "recalled for: the standing goal"), ("", None)])
def test_a_turn_with_its_own_recall_query_recalls_by_it(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch, fake_prefetch, query, recalled,
):
    """A check-in's prompt is fixed instructions: recall follows ``recall_query``."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "", "tools": []})
    orig = D._run_loop_blocking
    stream = _capturing_stream([])

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=stream)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        D.process_user_turn(agent_turn_request.TurnRequest(
            session_id="recall1", user_text="fixed instructions", agent_id="main",
            source="tui", recall_query=query,
        ))
    written = next(row for row in tmp_db.get_messages("recall1") if row.get("role") == "user")
    assert "fixed instructions" not in str(written.get("memory_prefetch"))
    from openprogram.runtime.context import turn_context
    prefetch = turn_context.entry_text(turn_context.entries(written), "memory_prefetch")
    assert (recalled in prefetch) if recalled else prefetch == ""


def test_system_prompt_is_byte_identical_across_turns(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch, fake_prefetch,
):
    """The whole point of §7: two turns, two different recalled memories,
    one unchanged system prompt — so the provider's cached prefix survives."""
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "",
                                          "tools": []})
    sink: list = []
    _run_turn("first question", sink, session_id="pf2")
    _run_turn("second question", sink, session_id="pf2")

    assert len(sink) >= 2
    assert sink[0].system_prompt == sink[-1].system_prompt

    # ...and the prefetch really did differ between them, so the identity
    # above isn't vacuous.
    t0 = _text_of([m for m in sink[0].messages
                   if getattr(m, "role", "") == "user"][-1])
    t1 = _text_of([m for m in sink[-1].messages
                   if getattr(m, "role", "") == "user"][-1])
    assert "recalled for: first question" in t0
    assert "recalled for: second question" in t1


def test_prefetch_is_stamped_on_the_user_node_for_replay(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch, fake_prefetch,
):
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "",
                                          "tools": []})
    sink: list = []
    _run_turn("remember this", sink, session_id="pf3")

    users = [n for n in tmp_db.get_nodes("pf3")
             if n.role == "user" and (n.metadata or {}).get("display") != "root"]
    assert users
    stamped = (users[-1].metadata or {}).get("memory_prefetch") or ""
    assert "recalled for: remember this" in stamped


def test_inject_prefetch_prefixes_the_last_user_message():
    from openprogram.runtime.agent.loop import _inject_memory_prefetch
    msgs = [{"role": "user", "content": "old"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "new"}]
    assert _inject_memory_prefetch(msgs, "<memory-context>M</memory-context>")
    assert msgs[0]["content"] == "old"           # earlier turns untouched
    assert msgs[2]["content"].startswith("<memory-context>M</memory-context>")
    assert msgs[2]["content"].endswith("new")


def test_inject_prefetch_handles_block_content():
    from openprogram.runtime.agent.loop import _inject_memory_prefetch
    msgs = [{"role": "user",
             "content": [{"type": "image"}, {"type": "text", "text": "hi"}]}]
    assert _inject_memory_prefetch(msgs, "<memory-context>M</memory-context>")
    assert msgs[0]["content"][1]["text"].endswith("hi")
    assert msgs[0]["content"][1]["text"].startswith("<memory-context>")


def test_inject_prefetch_is_a_noop_without_a_user_message():
    from openprogram.runtime.agent.loop import _inject_memory_prefetch
    msgs = [{"role": "assistant", "content": "only me"}]
    assert _inject_memory_prefetch(msgs, "M") is False
    assert msgs[0]["content"] == "only me"


def test_inject_prefetch_never_edits_the_shared_message():
    # The default converter hands the stored messages through unchanged; a
    # block written into them would be stored and added again every round.
    from openprogram.models.providers.types import TextContent, UserMessage
    from openprogram.runtime.agent.loop import _inject_memory_prefetch
    stored = [UserMessage(content=[TextContent(text="hi")], timestamp=0),
              {"role": "user", "content": [{"type": "text", "text": "yo"}]}]
    for original in stored:
        request = [original]
        assert _inject_memory_prefetch(request, "NOTE")
        assert request[0] is not original
        again = [original]
        assert _inject_memory_prefetch(again, "NOTE")
        assert _text_of(again[0]).count("NOTE") == 1
        assert _text_of(original) in ("hi", "yo")


def _text_of(msg) -> str:
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        t = getattr(c, "text", None)
        if t is None and isinstance(c, dict):
            t = c.get("text")
        if t:
            parts.append(str(t))
    return "\n".join(parts)


def test_the_block_stays_on_the_turns_message_when_the_loop_adds_a_note():
    # A progress note or a "continue" instruction is a user-role message added
    # after the turn's own; the block must not move onto it, or the prefix the
    # provider cached up to the turn's message stops matching.
    from openprogram.runtime.agent.loop import _inject_memory_prefetch
    first = [{"role": "user", "content": "do the task"}]
    assert _inject_memory_prefetch(first, "NOTE", "do the task")
    later = [{"role": "user", "content": "do the task"},
             {"role": "assistant", "content": "working"},
             {"role": "user", "content": "say where things stand"}]
    assert _inject_memory_prefetch(later, "NOTE", "do the task")
    assert later[0] == first[0]
    assert later[2]["content"] == "say where things stand"
    # A request that no longer holds the turn's message falls back to the last one.
    gone = [{"role": "user", "content": "summary of earlier work"}]
    assert _inject_memory_prefetch(gone, "NOTE", "do the task")
    assert gone[0]["content"].startswith("NOTE")
