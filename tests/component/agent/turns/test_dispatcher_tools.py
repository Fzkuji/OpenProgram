"""Dispatcher integration with the @tool registry.

These tests run the real ``_run_loop_blocking`` against a fake
``stream_fn`` that emits a ``ToolCall`` first, then a final text
reply on the second call. The full path exercised:

    dispatcher.process_user_turn
      → agent_loop._run_loop  (real)
        → stream_fn (fake, scripted)
        → _execute_tool_calls  (real, calls our @tool-registered tool)
      → AgentEventToolStart/End → chat_response envelopes
      → SessionDB persistence

What this catches that test_dispatcher_integration.py doesn't:

  * ToolCall → registry lookup wiring (was broken before — dispatcher
    used to import ``openprogram.programs.registry`` which doesn't exist)
  * tool_use / tool_result envelope shape (TUI/web depend on these)
  * approval gate consumes a typed durable wait outcome
  * the weight cap bounding a result once and keeping the whole text
  * cancel_event surfacing into the tool's ``cancel`` kwarg
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.permissions.approvals import approval as permissions_approval
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
    ToolCall,
    Usage,
)
from openprogram.programs._host import runtime as R
from openprogram.programs._host.runtime import function
from openprogram.runtime.conversation import model_tools


# ---------------------------------------------------------------------------
# Helpers shared with test_dispatcher_integration.py
# ---------------------------------------------------------------------------

def _stub_model() -> Model:
    return Model(
        id="stub",
        name="stub",
        api="completion",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def _owner_turn(**kwargs) -> agent_turn_request.TurnRequest:
    from openprogram.runtime.permissions.authority import local_owner_authority

    return agent_turn_request.TurnRequest(
        **kwargs,
        **local_owner_authority(),
    )


def _build_partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion",
        provider="openai",
        model="stub",
        timestamp=int(time.time() * 1000),
    )


def _build_final_text(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="completion",
        provider="openai",
        model="stub",
        usage=Usage(input=10, output=4),
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


def _build_final_with_tool(call_id: str, name: str, args: dict) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments=args)],
        api="completion",
        provider="openai",
        model="stub",
        usage=Usage(input=10, output=4),
        stop_reason="toolUse",
        timestamp=int(time.time() * 1000),
    )


def make_two_phase_stream(call_id: str, tool_name: str, tool_args: dict,
                          *, final_text: str = "ok"):
    """Stream-fn that emits a ToolCall the first time and a text reply
    the second. agent_loop calls stream_fn once per turn."""
    state = {"call": 0}

    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        state["call"] += 1
        if state["call"] == 1:
            yield EventStart(partial=_build_partial(""))
            yield EventDone(reason="toolUse",
                            message=_build_final_with_tool(call_id, tool_name, tool_args))
        else:
            yield EventStart(partial=_build_partial(""))
            yield EventTextStart(content_index=0, partial=_build_partial(""))
            yield EventTextDelta(content_index=0, delta=final_text,
                                 partial=_build_partial(final_text))
            yield EventTextEnd(content_index=0, content=final_text,
                               partial=_build_partial(final_text))
            yield EventDone(reason="stop", message=_build_final_text(final_text))

    return _fn


# ---------------------------------------------------------------------------
# Fixtures: isolated DB + agent profile + tool registry
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    return db


@pytest.fixture
def captured() -> list[dict]:
    return []


@pytest.fixture
def collector(captured: list[dict]):
    return captured.append


@pytest.fixture(autouse=True)
def stub_model_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch):
    """Each test gets a clean tool registry to register its own probe
    tool without interference from another test's @function decoration.

    Also disables the Layer 2 exposure filter for the duration of the
    test (via monkey-patching ``_exposed_set`` to return ``None``), so
    the dispatcher path keeps the ad-hoc probe tools these tests
    register.
    """
    saved_reg = dict(R._registry)
    saved_ts = {k: set(v) for k, v in R._toolset_membership.items()}
    saved_unsafe = {k: set(v) for k, v in R._unsafe_in_channel.items()}
    R._registry.clear()
    R._toolset_membership.clear()
    R._unsafe_in_channel.clear()
    R._cache.clear()
    import openprogram.programs as _functions
    monkeypatch.setattr(_functions, "_exposed_set", lambda: None)
    yield R
    R._registry.clear()
    R._toolset_membership.clear()
    R._unsafe_in_channel.clear()
    R._cache.clear()
    R._registry.update(saved_reg)
    R._toolset_membership.update(saved_ts)
    R._unsafe_in_channel.update(saved_unsafe)


def _stub_profile_with_tools(tool_names: list[str]):
    """Patch _load_agent_profile to expose a profile that whitelists
    ``tool_names`` so dispatcher's _resolve_tools picks them up."""
    return lambda agent_id: {
        "id": agent_id,
        "system_prompt": "you are helpful",
        "tools": tool_names,
    }


def _patched_run_loop(stream_fn):
    orig = D._run_loop_blocking

    def _wrap(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=stream_fn)

    return _wrap


def _durable_approval_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                           *, wait_id: str, tool: str, args: dict,
                           tool_call_id: str):
    """Create the durable approval safe point consumed by the wrapper."""
    import openprogram.runtime.execution as execution_module
    from openprogram.runtime.execution import RuntimeControlService
    from openprogram.runtime.execution.attempts import AttemptStore
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.model import CapabilitySet
    from openprogram.runtime.execution.store import ExecutionStore
    from openprogram.runtime.execution.waits import DurableWaitStore

    store = ExecutionStore(tmp_path / f"{wait_id}.db")
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    revision = store.create_revision(manifest={"entrypoint": "dispatcher-test"})
    execution = store.create_execution(
        execution_id=f"exec_{wait_id}", run_id=f"run_{wait_id}", session_id="c1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True, safe_point_kinds=("agent.wait.before_tool",),
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="dispatcher-test",
    )
    attempt, execution = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    wait = DurableWaitStore(store).open_wait(
        wait_id=wait_id, execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id, generation=attempt.generation,
        kind="approval",
        request={
            "prompt": f"允许执行 {tool}？", "options": ["允许", "拒绝"],
            "multi": False, "allow_custom": False, "detail": tool,
            "schema": {}, "questions": [], "tool": tool, "args": dict(args),
            "tool_call_id": tool_call_id,
        },
        policy_snapshot={
            "version": 1, "kind": "approval",
            "allowed_scopes": ["once", "always", "always_path"],
        },
        expires_at=time.time() + 60,
    )
    return store, RuntimeControlService(store, attempts, DriverRegistry()), wait


def _answer_wait(store, service, wait, answer):
    import asyncio

    execution = store.get_execution(wait.execution_id)
    assert execution is not None
    return asyncio.run(service.request_wait_answer(
        command_id=f"answer-{wait.wait_id}",
        execution_id=wait.execution_id,
        expected_version=execution.status_version,
        actor={"surface": "test"}, wait_id=wait.wait_id,
        generation=wait.claim_generation, answer=answer,
    ))


def _decline_wait(store, service, wait):
    import asyncio

    execution = store.get_execution(wait.execution_id)
    assert execution is not None
    return asyncio.run(service.request_wait_decline(
        command_id=f"decline-{wait.wait_id}",
        execution_id=wait.execution_id,
        expected_version=execution.status_version,
        actor={"surface": "test"}, wait_id=wait.wait_id,
        generation=wait.claim_generation,
    ))


def test_resolve_tools_filters_channel_unsafe_tools(fresh_registry) -> None:
    @function(name="safeprobe", description="Safe")
    def safeprobe() -> str:
        """Safe probe."""
        return "ok"

    @function(name="unsafeprobe", description="Unsafe", unsafe_in=["wechat"])
    def unsafeprobe() -> str:
        """Unsafe probe."""
        return "no"

    names = [
        t.name for t in model_tools.resolve_tools(
            {"tools": ["safeprobe", "unsafeprobe"]},
            source="wechat",
        ) or []
    ]

    assert names == ["safeprobe"]


def test_resolve_default_agent_tools_from_profile_dict(
    fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openprogram.programs as tools_pkg

    @function(name="safeprobe", description="Safe")
    def safeprobe() -> str:
        """Safe probe."""
        return "ok"

    @function(name="unsafeprobe", description="Unsafe", unsafe_in=["wechat"])
    def unsafeprobe() -> str:
        """Unsafe probe."""
        return "no"

    @function(name="newcatalogprobe", description="New catalog entry")
    def newcatalogprobe() -> str:
        return "new"

    monkeypatch.setattr(tools_pkg, "DEFAULT_TOOLS", ["safeprobe", "unsafeprobe"])

    names = [
        t.name for t in model_tools.resolve_tools(
            {"tools": {"disabled": []}},
            source="wechat",
        ) or []
    ]

    assert set(names) == {"safeprobe", "newcatalogprobe"}


def test_resolve_agent_tool_access_modes(fresh_registry) -> None:
    @function(name="catalog_read", description="Read catalog")
    def catalog_read() -> str:
        return "read"

    @function(name="catalog_research", description="Research catalog")
    def catalog_research() -> str:
        return "research"

    automatic = {
        t.name for t in model_tools.resolve_tools(
            {"tools": {"mode": "automatic"}}, source="web"
        ) or []
    }
    selected = {
        t.name for t in model_tools.resolve_tools(
            {"tools": {"mode": "selected", "allowed": ["catalog_research"]}},
            source="web",
        ) or []
    }
    none = model_tools.resolve_tools({"tools": {"mode": "none"}}, source="web")

    assert automatic == {"catalog_read", "catalog_research"}
    assert selected == {"catalog_research"}
    assert none == []


def test_inherit_override_uses_agent_policy(fresh_registry) -> None:
    @function(name="profile_read", description="Read")
    def profile_read() -> str:
        return "read"

    names = {
        t.name for t in model_tools.resolve_tools(
            {"tools": {"mode": "selected", "allowed": ["profile_read"]}},
            {"inherit": True},
            source="web",
        ) or []
    }
    assert names == {"profile_read"}


def test_selected_deferred_program_keeps_scoped_search_loader(fresh_registry) -> None:
    fresh_registry.register(fresh_registry.tool_search)

    @function(name="deferred_report", description="Report", defer=True)
    def deferred_report() -> str:
        return "report"

    names = {
        tool.name for tool in model_tools.resolve_tools(
            {"tools": {"mode": "selected", "allowed": ["deferred_report"]}},
            source="web",
        ) or []
    }
    assert names == {"tool_search", "deferred_report"}


def test_tool_use_event_runs_tool_and_emits_result(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @function(name="probe", description="Echo")
    def probe(text: str) -> str:
        """Echo input.

        Args:
            text: text to echo.
        """
        return f"PROBE:{text}"

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["probe"]))
    stream = make_two_phase_stream("call-1", "probe", {"text": "hi"},
                                    final_text="done")

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        result = D.process_user_turn(
            _owner_turn(session_id="c1", user_text="run probe", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )

    assert result.failed is False
    assert result.final_text == "done"
    assert any(tc["tool"] == "probe" for tc in result.tool_calls)

    tool_use = [e for e in captured
                if e["type"] == "chat_response"
                and e["data"].get("event", {}).get("type") == "tool_use"]
    tool_result = [e for e in captured
                   if e["type"] == "chat_response"
                   and e["data"].get("event", {}).get("type") == "tool_result"]
    assert len(tool_use) == 1
    assert tool_use[0]["data"]["event"]["tool"] == "probe"
    assert len(tool_result) == 1
    assert "PROBE:hi" in tool_result[0]["data"]["event"]["result"]


# ---------------------------------------------------------------------------
# Test 2: an oversized tool output is bounded for the model; the card shows
# the head of the tool's own output and how long the whole is
# ---------------------------------------------------------------------------

def test_oversized_tool_result_is_truncated(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @function(name="bigprobe", description="Big", max_result_weight=200)
    def bigprobe() -> str:
        """Returns a huge string."""
        return "x" * 50_000

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["bigprobe"]))
    stream = make_two_phase_stream("c", "bigprobe", {})

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            _owner_turn(session_id="c1", user_text="run", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )

    tool_results = [e for e in captured
                    if e["type"] == "chat_response"
                    and e["data"].get("event", {}).get("type") == "tool_result"]
    assert tool_results
    event = tool_results[0]["data"]["event"]
    # The card shows the head of the tool's own output, not the model's
    # head/tail/note, and says how long the whole output is.
    assert event["result"] == "x" * 4_000
    assert event["full_output"]["total_chars"] == 50_000
    assert event["full_output"]["shown_chars"] == 4_000


_ROWS = "\n".join(f"row {i:04d}: " + "alpha bravo charlie " * 3 for i in range(320))



def _session_results(db, session_id: str) -> list[Path]:
    """Whole tool results kept in the conversation's own ``tool_results/``."""
    directory = Path(db._session_dir(session_id)) / "tool_results"  # noqa: SLF001
    return sorted(directory.glob("*.txt")) if directory.is_dir() else []


@pytest.mark.parametrize("shape", ["under_cap", "two_blocks", "over_cap_one_line"])
def test_next_turn_reads_a_tool_result_as_the_turn_that_ran_it_did(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str,
) -> None:
    # Every later request renders a tool result from what was stored. It
    # must carry the same blocks and bytes the turn that ran the tool sent,
    # on every provider: anything else hides content from the model and
    # changes the request after that point, so the provider reads all of
    # it again uncached.
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.state.store.session.weight import weight
    from openprogram.state.store.session.files.spill import DEFAULT_TOOL_OUTPUT_WEIGHT

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir",
                        lambda: results_dir)
    full = {
        "under_cap": _ROWS,
        "two_blocks": _ROWS,
        "over_cap_one_line": '{"steps": [' + ", ".join(
            f'{{"n": {i}, "note": "clicked the button"}}' for i in range(2_500)) + "]}",
    }[shape]
    assert 4_000 < len(full)

    @function(name="readprobe", description="Read")
    def readprobe():
        """Returns a long file."""
        if shape == "two_blocks":
            # A file tool's text with the AGENTS.md it reached appended.
            return AgentToolResult(content=[TextContent(text=full),
                                            TextContent(text="\n\n# AGENTS.md\nrules")])
        return full

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["readprobe"]))
    seen: list[list] = []

    def _recording(stream_fn):
        async def _fn(model, context, options):
            seen.append(list(context.messages))
            async for event in stream_fn(model, context, options):
                yield event
        return _fn

    def _results(messages) -> list:
        return [[(c.type, getattr(c, "text", None)) for c in m.content]
                for m in messages if getattr(m, "role", None) == "toolResult"]

    turn_one = _recording(make_two_phase_stream("call_read", "readprobe", {}))
    with patch.object(D, "_run_loop_blocking", _patched_run_loop(turn_one)):
        first = D.process_user_turn(
            _owner_turn(session_id="c1", user_text="read it", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )
    assert first.failed is False
    in_turn = _results(seen[-1])
    assert len(in_turn) == 1 and len(in_turn[0]) == 1
    sent = in_turn[0][0][1]
    if shape == "over_cap_one_line":
        # Bounded once, when made, with the whole text kept in a file.
        assert weight(sent) <= DEFAULT_TOOL_OUTPUT_WEIGHT < weight(full)
        # In the conversation's directory, never the profile's shared one.
        saved = _session_results(tmp_db, "c1")
        assert list(results_dir.iterdir()) == []
        assert len(saved) == 1 and saved[0].read_text() == full
        assert str(saved[0]) in sent
    else:
        assert full in sent

    async def _reply(model, context, options):
        seen.append(list(context.messages))
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(_reply)):
        second = D.process_user_turn(
            _owner_turn(session_id="c1", user_text="what was in it?", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )
    assert second.failed is False
    assert _results(seen[-1]) == in_turn

    # The client still gets a preview, and never the whole text.
    frames = [e["data"]["event"] for e in captured
              if e["type"] == "chat_response"
              and e["data"].get("event", {}).get("type") == "tool_result"]
    assert len(frames) == 1
    assert frames[0]["result"].startswith(sent[:4000])
    assert len(frames[0]["result"]) < 4_100
    assert "stored_result" not in frames[0]

    # The context estimate counts the result the requests carry, not the
    # preview the reply's card keeps.
    from openprogram.runtime.context.token_accounting import breakdown as breakdown_module
    from openprogram.runtime.context.token_accounting import session_stats
    counted: list = []

    def _capture(**kwargs):
        counted.append(kwargs["history"])
        return {"messages": 0, "tools": []}

    monkeypatch.setattr(breakdown_module, "compute_call_breakdown", _capture)
    session_stats.compute_breakdown("c1")
    assert _results(counted[-1]) == in_turn


def test_a_small_window_model_reads_its_share_of_a_result_in_both_turns(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A 32k-token local model: one result is bounded to 30% of its window,
    # in weight, and the next turn reads the same bytes.
    from openprogram.state.store.session.weight import weight

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir",
                        lambda: results_dir)
    small = _stub_model().model_copy(update={"context_window": 32_768})
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: small)
    full = _ROWS + "\n" + _ROWS
    assert 9_830 < weight(full)

    @function(name="readprobe", description="Read")
    def readprobe():
        """Returns a long file."""
        return full

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["readprobe"]))
    seen: list[list] = []

    def _results(messages) -> list:
        return [[getattr(c, "text", None) for c in m.content]
                for m in messages if getattr(m, "role", None) == "toolResult"]

    stream = make_two_phase_stream("call_read", "readprobe", {})

    async def _turn_one(model, context, options):
        seen.append(list(context.messages))
        async for event in stream(model, context, options):
            yield event

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(_turn_one)):
        first = D.process_user_turn(
            _owner_turn(session_id="c1", user_text="read it", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )
    assert first.failed is False
    [[sent]] = _results(seen[-1])
    assert weight(sent) <= 9_830
    [saved] = _session_results(tmp_db, "c1")
    assert list(results_dir.iterdir()) == []
    assert saved.read_text() == full and str(saved) in sent

    async def _reply(model, context, options):
        seen.append(list(context.messages))
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(_reply)):
        second = D.process_user_turn(
            _owner_turn(session_id="c1", user_text="what was in it?", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )
    assert second.failed is False
    assert _results(seen[-1]) == [[sent]]


# ---------------------------------------------------------------------------
# Test 3: a bounded result's whole text is written to disk
# ---------------------------------------------------------------------------

def test_a_bounded_result_keeps_its_whole_text_on_disk(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A running conversation keeps the whole text in its own directory;
    # the profile's shared directory (redirected here) stays empty.
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir",
                        lambda: results_dir)

    @function(name="persistprobe", description="Persist", max_result_weight=200)
    def persistprobe() -> str:
        """Big payload."""
        return "Y" * 5_000

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["persistprobe"]))
    stream = make_two_phase_stream("call-x", "persistprobe", {})

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            _owner_turn(session_id="c1", user_text="run", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
        )

    assert list(results_dir.glob("*.txt")) == []
    persisted = _session_results(tmp_db, "c1")
    assert len(persisted) == 1, f"expected 1 persisted file, got {persisted}"
    assert persisted[0].read_text() == "Y" * 5_000


# ---------------------------------------------------------------------------
# Test 4: approval flow blocks the loop until resolved
# ---------------------------------------------------------------------------

def test_approval_required_consumes_typed_durable_answer(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper runs only after a prepublished typed wait is resolved."""
    fired = threading.Event()

    @function(name="dangerprobe", description="Danger", requires_approval=True)
    def dangerprobe(target: str) -> str:
        """Pretend-dangerous tool.

        Args:
            target: thing to do dangerous things to.
        """
        fired.set()
        return f"did {target}"

    store, service, wait = _durable_approval_wait(
        tmp_path, monkeypatch, wait_id="wait_dangerprobe_allow",
        tool="dangerprobe", args={"target": "x"},
        tool_call_id="call-dangerprobe",
    )
    _answer_wait(store, service, wait, {"answer": "允许", "scope": "once"})
    from openprogram.runtime.conversation.run_control import reset_preapproved_wait_id, set_preapproved_wait_id
    from openprogram.runtime.execution.run_control import (
        reset_current_execution_id,
        set_current_execution_id,
    )
    req = _owner_turn(
        session_id="c1", user_text="run", agent_id="main", source="tui",
        permission_mode="ask",
    )
    wrapped = permissions_approval.wrap_with_approval(
        R._registry["dangerprobe"], req, lambda _event: None,
    )
    token = set_preapproved_wait_id(wait.wait_id)
    execution_token = set_current_execution_id(wait.execution_id)
    try:
        result = asyncio.run(wrapped.execute(
            "call-dangerprobe", {"target": "x"}, None, lambda _update: None,
        ))
    finally:
        reset_current_execution_id(execution_token)
        reset_preapproved_wait_id(token)
    assert fired.is_set(), f"tool was not executed after approval: {result!r}"
    assert result.is_error is False
    assert result.content[0].text == "did x"


def test_approval_denied_aborts_tool_before_execution(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fired = threading.Event()

    @function(name="risky", description="Risky", requires_approval=True)
    def risky() -> str:
        """Risky."""
        fired.set()
        return "should-not-run"

    store, service, wait = _durable_approval_wait(
        tmp_path, monkeypatch, wait_id="wait_risky_decline",
        tool="risky", args={}, tool_call_id="call-risky",
    )
    _decline_wait(store, service, wait)
    from openprogram.runtime.conversation.run_control import reset_preapproved_wait_id, set_preapproved_wait_id
    req = _owner_turn(
        session_id="c1", user_text="run", agent_id="main", source="tui",
        permission_mode="ask",
    )
    wrapped = permissions_approval.wrap_with_approval(R._registry["risky"], req, lambda _event: None)
    token = set_preapproved_wait_id(wait.wait_id)
    try:
        result = asyncio.run(wrapped.execute(
            "call-risky", {}, None, lambda _update: None,
        ))
    finally:
        reset_preapproved_wait_id(token)
    assert result.is_error is True
    assert result.content[0].text.startswith("[blocked by mode]")
    assert "you declined it" in result.content[0].text
    assert not fired.is_set()


def test_self_update_prepare_forces_one_shot_approval_in_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.runtime.permissions.approvals import approval as _approval
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.models.providers.types import TextContent

    fired = threading.Event()

    async def execute(_call_id, _args, _cancel, _on_update):
        fired.set()
        return AgentToolResult(content=[TextContent(text="prepared")])

    tool = AgentTool(
        name="self_update", description="prepare", parameters={},
        label="prepare", execute=execute,
    )
    req = _owner_turn(
        session_id="c1", user_text="update", agent_id="main", source="web",
        permission_mode="bypass",
    )
    monkeypatch.setattr(
        _approval, "await_user_approval",
        lambda **_kwargs: asyncio.sleep(0, result=(True, None, "always")),
    )
    persisted: list[tuple] = []
    monkeypatch.setattr(
        _approval, "_persist_always_allow_rule",
        lambda *args: persisted.append(args) or True,
    )

    wrapped = _approval.wrap_with_approval(tool, req, lambda _event: None)
    result = asyncio.run(wrapped.execute("call", {}, None, None))

    assert fired.is_set() is True
    assert result.is_error is False
    assert persisted == []


@pytest.mark.parametrize("turn_committed", [False, True])
def test_dispatcher_releases_self_update_only_after_durable_turn_and_idle_status(
    tmp_db: SessionDB,
    captured,
    collector,
    monkeypatch: pytest.MonkeyPatch,
    turn_committed: bool,
) -> None:
    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools([]))
    monkeypatch.setattr(D, "finalize_turn", lambda **_kwargs: turn_committed)
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        D,
        "release_prepared_update",
        lambda session_id, assistant_id: released.append((session_id, assistant_id)),
    )
    stream_state = {"used": False}

    async def final_only(model, context, options):
        del model, context, options
        assert stream_state["used"] is False
        stream_state["used"] = True
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(
            content_index=0, delta="done", partial=_build_partial("done")
        )
        yield EventTextEnd(
            content_index=0, content="done", partial=_build_partial("done")
        )
        yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(final_only)):
        result = D.process_user_turn(
            _owner_turn(
                session_id="c1", user_text="update", agent_id="main",
                source="web", permission_mode="bypass",
            ),
            on_event=collector,
        )

    expected = [("c1", result.assistant_msg_id)] if turn_committed else []
    assert released == expected


def test_dispatcher_does_not_release_when_assistant_persistence_fails(
    tmp_db: SessionDB,
    captured,
    collector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools([]))
    monkeypatch.setattr(
        D,
        "persist_assistant_message",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("assistant write failed")),
    )
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        D,
        "release_prepared_update",
        lambda session_id, assistant_id: released.append((session_id, assistant_id)),
    )

    async def final_only(model, context, options):
        del model, context, options
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(final_only)):
        with pytest.raises(OSError, match="assistant write failed"):
            D.process_user_turn(
                _owner_turn(
                    session_id="c1", user_text="update", agent_id="main",
                    source="web", permission_mode="bypass",
                ),
                on_event=collector,
            )

    assert released == []


def test_dispatcher_does_not_release_when_finished_status_is_not_durable(
    tmp_db: SessionDB,
    captured,
    collector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools([]))
    monkeypatch.setattr(D, "finalize_turn", lambda **_kwargs: True)
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        D,
        "release_prepared_update",
        lambda session_id, assistant_id: released.append((session_id, assistant_id)),
    )
    update_session = tmp_db.update_session

    def fail_finished_status(session_id: str, **fields):
        if fields.get("status") in {"idle", "done"}:
            raise OSError("status write failed")
        return update_session(session_id, **fields)

    monkeypatch.setattr(tmp_db, "update_session", fail_finished_status)

    async def final_only(model, context, options):
        del model, context, options
        yield EventStart(partial=_build_partial(""))
        yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(final_only)):
        D.process_user_turn(
            _owner_turn(
                session_id="c1", user_text="update", agent_id="main",
                source="web", permission_mode="bypass",
            ),
            on_event=collector,
        )

    assert released == []


# ---------------------------------------------------------------------------
# Test 5: tool sees cancel_event when caller cancels
# ---------------------------------------------------------------------------

def test_cancel_propagates_to_tool(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.runtime.agent.loop import lifecycle
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.execution.run_control.wakeups import set_and_fire

    saw_cancel = threading.Event()
    finished = threading.Event()
    cancelled_by_caller = threading.Event()
    tool_started = threading.Event()

    # Only the wakeup a Stop fires may reach the tool: the polling backstop
    # (for code that sets the signal without firing it) would otherwise
    # deliver it later and hide a Stop that is not delivered at once.
    monkeypatch.setattr(loop_runner, "_STOP_BACKSTOP_S", 60.0)
    monkeypatch.setattr(lifecycle, "_STOP_BACKSTOP_S", 60.0)

    @function(name="slowprobe", description="Slow")
    async def slowprobe(cancel: asyncio.Event = None) -> str:
        """Long-running tool that polls cancel."""
        tool_started.set()
        try:
            for _ in range(100):
                if cancel is not None and cancel.is_set():
                    saw_cancel.set()
                    return "cancelled"
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            # A stop cuts the running call at once (``run_control.wakeups``);
            # a tool that polls its signal may see it first.
            saw_cancel.set()
            raise
        finished.set()
        return "finished"

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["slowprobe"]))
    stream = make_two_phase_stream("c-s", "slowprobe", {})

    cancel_flag = threading.Event()

    def _trigger_cancel():
        # Stop while the tool runs. A Stop that comes before the first model
        # call ends the turn before any tool starts, so a fixed delay would
        # race the turn's start (a cold first turn takes over a second).
        if not tool_started.wait(30):
            return
        cancelled_by_caller.set()
        set_and_fire(cancel_flag)

    threading.Thread(target=_trigger_cancel, daemon=True).start()

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            _owner_turn(session_id="c1", user_text="go", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
            cancel_event=cancel_flag,
        )

    assert cancelled_by_caller.is_set()
    assert saw_cancel.is_set(), "tool never observed cancel signal"
    assert not finished.is_set()


def test_agent_spawn_bypass_hard_constraint_is_installed_in_dispatcher(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = {"n": 0}

    @function(name="bash", description="Shell probe")
    def bash_probe(command: str) -> str:
        """Execute a shell probe."""
        executed["n"] += 1
        return command

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["bash"]))
    stream = make_two_phase_stream("spawn-call", "bash", {"command": "echo x"})

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        result = D.process_user_turn(
            agent_turn_request.TurnRequest(session_id="c1", user_text="run", agent_id="worker",
                          source="agent_spawn", permission_mode="bypass"),
            on_event=collector,
        )

    assert result.failed is False
    assert executed["n"] == 0
    tool_results = [
        e["data"]["event"]["result"] for e in captured
        if e["type"] == "chat_response"
        and e["data"].get("event", {}).get("type") == "tool_result"
    ]
    assert any("[blocked by identity]" in text and "did not run" in text
               for text in tool_results)


def test_discovered_schema_survives_next_chat_turn_but_not_other_session(
    tmp_db, collector, fresh_registry, monkeypatch,
):
    fresh_registry.register(fresh_registry.tool_search)

    @function(name="weekly_probe", description="A report", defer=True)
    def weekly_probe() -> str:
        return "report"

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["tool_search", "weekly_probe"]))
    phase = {"discover": True}
    seen = []
    refused = []
    from openprogram.programs._host import runtime as tool_runtime
    original_loaded = tool_runtime._request_loaded.get()

    async def stream(model, context, options):
        seen.append({t.name for t in context.tools or []})
        refused.append({t.name for t in context.tools or [] if getattr(t, "_unavailable", False)})
        if phase["discover"]:
            phase["discover"] = False
            message = _build_final_with_tool("discover", "tool_search", {"select": "select:weekly_probe"})
            yield EventDone(reason="toolUse", message=message)
        else:
            yield EventDone(reason="stop", message=_build_final_text("done"))

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        for sid in ("discover-session", "discover-session", "other-session"):
            result = D.process_user_turn(
                _owner_turn(session_id=sid, user_text="report", agent_id="main", source="tui", permission_mode="bypass"),
                on_event=collector,
            )
            assert not result.failed
            if sid == "discover-session":
                loaded_branch = result.assistant_msg_id
        result = D.process_user_turn(
            _owner_turn(session_id="discover-session", user_text="fork", agent_id="main",
                        source="tui", permission_mode="bypass", branch_from=None),
            on_event=collector,
        )
        assert not result.failed
        monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["tool_search"]))
        result = D.process_user_turn(
            _owner_turn(session_id="discover-session", user_text="restricted", agent_id="main",
                        source="tui", permission_mode="bypass", branch_from=loaded_branch),
            on_event=collector,
        )
        assert not result.failed
    assert "weekly_probe" not in seen[0]
    assert "weekly_probe" in seen[1]
    assert "weekly_probe" in seen[2]
    assert "weekly_probe" not in seen[3]
    assert "weekly_probe" not in seen[4]
    assert not refused[1] and not refused[2]
    # The branch that loaded it keeps it after the Agent drops the tool: the
    # array does not shrink, and its calls are refused (``unavailable_tool``).
    assert "weekly_probe" in seen[5]
    assert refused[5] == {"weekly_probe"}
    assert tool_runtime._request_loaded.get() is original_loaded


def test_consumed_steer_keeps_ordered_boundary(tmp_db, monkeypatch, fresh_registry):
    """Real loop consumes input between two provider requests and persists it."""
    from openprogram.state.store.session.nodes import Call
    from openprogram.state.store import SessionNodeWriter
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    monkeypatch.setattr(D, '_load_agent_profile', _stub_profile_with_tools(['steer_probe']))
    tmp_db.create_session('steer-order', 'main')
    writer = SessionNodeWriter(tmp_db, 'steer-order')
    writer.append(Call(id='u',role='user',output='start'))
    writer.append(Call(id='u_reply',role='llm',output='',predecessor='u'))
    queue=[]
    @function(name='steer_probe', description='test boundary')
    def probe():
        queue.append({'command_id':'input-1','payload':{'message':'NEW INPUT'}})
        return 'tool done'
    calls=0
    async def stream(model,context,options):
        nonlocal calls
        calls+=1
        if calls==1:
            message=_build_final_with_tool('tool-1','steer_probe',{})
            message.content.insert(0,TextContent(text='BEFORE'))
            yield EventStart(partial=_build_partial())
            yield EventTextStart(content_index=0,partial=_build_partial())
            yield EventTextDelta(content_index=0,delta='BEFORE',partial=_build_partial('BEFORE'))
            yield EventTextEnd(content_index=0,content='BEFORE',partial=_build_partial('BEFORE'))
            yield EventDone(reason='toolUse',message=message)
        else:
            assert any('NEW INPUT' in str(m.content) for m in context.messages)
            yield EventStart(partial=_build_partial())
            yield EventTextStart(content_index=0,partial=_build_partial())
            yield EventTextDelta(content_index=0,delta='AFTER',partial=_build_partial('AFTER'))
            yield EventTextEnd(content_index=0,content='AFTER',partial=_build_partial('AFTER'))
            yield EventDone(reason='stop',message=_build_final_text('AFTER'))
    req=_owner_turn(session_id='steer-order',agent_id='main',source='web',user_msg_id='u',user_text='start',permission_mode='bypass')
    events=[];ordered=[]
    tap=make_stream_tap(on_event=events.append,req=req,assistant_msg_id='u_reply',placeholder_inserted=True,agentic_tool_names=set())
    D._run_loop_blocking(req=req,history=[],on_event=tap,cancel_event=None,stream_fn=stream,
        assistant_msg_id='u_reply',ordered_blocks_out=ordered,execution_context={'canonical_execution':True,'steer_inputs':queue,'steer_consumed_ids':set()})
    assert [b['type'] for b in ordered]==['text','tool','steering','text']
    assert [b['type'] for b in tap.blocks]==['text','tool','steering','text']
    import json
    node=next(n for n in tmp_db.get_nodes('steer-order') if n.id=='u_reply')
    assert [b['type'] for b in json.loads(node.metadata['extra'])['blocks']]==['text','tool','steering','text']
    user=next(e['data'] for e in events if e.get('data',{}).get('type')=='user_message')
    assert user['assistant_msg_id']=='u_reply'


def test_withdrawn_steer_is_dropped_and_the_next_input_still_delivered(tmp_db, monkeypatch, fresh_registry):
    """A steer taken back while it sat in the turn's queue is never written."""
    import contextlib
    from openprogram.state.store.session.nodes import Call
    from openprogram.state.store import SessionNodeWriter
    monkeypatch.setattr(D, '_load_agent_profile', _stub_profile_with_tools(['steer_probe']))
    tmp_db.create_session('steer-withdrawn', 'main')
    writer = SessionNodeWriter(tmp_db, 'steer-withdrawn')
    writer.append(Call(id='u', role='user', output='start'))
    writer.append(Call(id='u_reply', role='llm', output='', predecessor='u'))
    queue = []

    @function(name='steer_probe', description='test boundary')
    def probe():
        queue.append({'command_id': 'input-1', 'payload': {'message': 'TAKEN BACK'}})
        queue.append({'command_id': 'input-2', 'payload': {'message': 'STILL DUE'}})
        return 'tool done'

    class Withdrawn(RuntimeError):
        code = 'steer_withdrawn'

    @contextlib.contextmanager
    def persist(command_id, _message_id):
        if command_id == 'input-1':
            raise Withdrawn('taken back')
        yield

    seen = []

    async def stream(model, context, options):
        seen.append(' '.join(str(m.content) for m in context.messages))
        if len(seen) == 1:
            yield EventStart(partial=_build_partial())
            yield EventDone(reason='toolUse', message=_build_final_with_tool('tool-1', 'steer_probe', {}))
        else:
            yield EventStart(partial=_build_partial())
            yield EventTextStart(content_index=0, partial=_build_partial())
            yield EventTextDelta(content_index=0, delta='AFTER', partial=_build_partial('AFTER'))
            yield EventTextEnd(content_index=0, content='AFTER', partial=_build_partial('AFTER'))
            yield EventDone(reason='stop', message=_build_final_text('AFTER'))

    req = _owner_turn(session_id='steer-withdrawn', agent_id='main', source='web', user_msg_id='u',
                      user_text='start', permission_mode='bypass')
    consumed = set()
    D._run_loop_blocking(req=req, history=[], on_event=lambda _event: None, cancel_event=None, stream_fn=stream,
        assistant_msg_id='u_reply', ordered_blocks_out=[],
        execution_context={'canonical_execution': True, 'steer_inputs': queue,
                           'steer_consumed_ids': consumed, 'persist_steer': persist})

    assert len(seen) == 2
    assert 'STILL DUE' in seen[1] and 'TAKEN BACK' not in seen[1]
    assert queue == [] and consumed == {'input-2'}
    assert not any(node.output == 'TAKEN BACK' for node in tmp_db.get_nodes('steer-withdrawn'))


@pytest.mark.parametrize("same_session", [False, True])
def test_full_result_artifacts_survive_reused_provider_ids(
    tmp_db, captured, collector, fresh_registry, monkeypatch, tmp_path, same_session,
):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(R, "_tool_results_dir", lambda: results_dir)

    @function(name="persistprobe", description="Persist", max_result_weight=500)
    def persistprobe(value: str) -> str:
        return value * 5000

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["persistprobe"]))
    first_path = None
    for index, value in enumerate(["A", "B"]):
        session_id = "artifact-A" if same_session or index == 0 else "artifact-B"
        stream = make_two_phase_stream("call_0_persistprobe", "persistprobe", {"value": value})
        with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
            result = D.process_user_turn(
                _owner_turn(session_id=session_id, user_text="run " + value, agent_id="main",
                            source="tui", permission_mode="bypass"),
                on_event=collector,
            )
        assert result.failed is False
        if first_path is None:
            first_path, = _session_results(tmp_db, "artifact-A")
            assert first_path.read_text() == "A" * 5000

    # The card shows the tool's own output; the model's text names the file.
    receipts = [e["data"]["event"] for e in captured
                if e["type"] == "chat_response"
                and e["data"].get("event", {}).get("type") == "tool_result"]
    assert len(receipts) == 2
    assert receipts[0]["result"] == "A" * 4_000 and receipts[0]["full_output"]["total_chars"] == 5_000
    sent = [node.output for sid in {"artifact-A", "artifact-B"}
            for node in tmp_db.get_nodes(sid) if node.metadata.get("tool_call_id")]
    assert any(str(first_path) in text for text in sent)
    assert first_path.read_text() == "A" * 5000
    assert list(results_dir.glob("*.txt")) == []
    paths = _session_results(tmp_db, "artifact-A") + (
        [] if same_session else _session_results(tmp_db, "artifact-B"))
    assert len(paths) == 2
    second_path, = [path for path in paths if path != first_path]
    assert any(str(second_path) in text for text in sent)
    assert second_path.read_text() == "B" * 5000


@pytest.mark.parametrize("turn_committed", [True, False])
def test_resumed_turn_releases_its_prepared_update(monkeypatch, turn_committed):
    # A self_update prepare approved from its card finishes in the resumed turn.
    from types import SimpleNamespace

    from openprogram.runtime.conversation import turn_runtime as T
    from openprogram.runtime.conversation.turn_runtime import finalize, persistence, turn_writer
    from openprogram.runtime.context import Context
    from openprogram.runtime.context.compaction import persistence as context_persistence
    from openprogram.state.memory import policy

    class Store:
        statuses: list[str] = []

        def get_session(self, _session_id):
            return {"id": "c1"}

        def message_exists(self, _session_id, _message_id):
            return True

        def update_session(self, _session_id, **fields):
            self.statuses.append(fields["status"])

    store = Store()
    monkeypatch.setattr(Context, "turn_store", staticmethod(lambda _session_id: store))
    monkeypatch.setattr(context_persistence, "rendered_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(policy, "bind_history", lambda *args: None)
    monkeypatch.setattr(T.TurnBindings, "bind",
                        classmethod(lambda cls, **kwargs: SimpleNamespace(release=lambda **kw: None)))
    monkeypatch.setattr(T, "make_stream_tap",
                        lambda **kwargs: SimpleNamespace(flush=lambda: None, blocks=[], initial=None))
    monkeypatch.setattr(T, "_run_loop_blocking", lambda **kwargs: ("done", {}, []))
    monkeypatch.setattr(persistence, "persist_assistant_message",
                        lambda **kwargs: ({"id": "a1"}, [], [], {}))
    monkeypatch.setattr(finalize, "finalize_turn", lambda **kwargs: turn_committed)
    monkeypatch.setattr(turn_writer.TurnWriter, "head_for_finalize", lambda self, _id: "a1")
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(T, "release_prepared_update",
                        lambda session_id, assistant_id: released.append((session_id, assistant_id)))
    request = SimpleNamespace(session_id="c1", advance_head=True, source="web", last_round_text="")
    continuation = SimpleNamespace(
        request=request, assistant_message_id="a1",
        user_message_id="u1",
    )

    T.execute_continuation.__wrapped__(continuation)

    assert store.statuses == ["done"]
    assert released == ([("c1", "a1")] if turn_committed else [])


@pytest.mark.parametrize("takes_images", [True, False])
def test_steer_images_reach_a_model_that_takes_images(tmp_db, monkeypatch, fresh_registry, tmp_path, takes_images):
    """A steer that carried a picture hands it to the model with its text."""
    from openprogram.models.providers.types import ImageContent
    from openprogram.state.store.session.nodes import Call
    from openprogram.state.store import SessionNodeWriter
    from openprogram.runtime.conversation.turn_runtime.stream_tap import make_stream_tap
    monkeypatch.setattr(D, '_load_agent_profile', _stub_profile_with_tools(['steer_probe']))
    resolve = D._resolve_model

    def model_with_input(*args, **kwargs):
        model = resolve(*args, **kwargs)
        return model.model_copy(update={'input': ['text', 'image'] if takes_images else ['text']})
    monkeypatch.setattr(D, '_resolve_model', model_with_input)
    picture = tmp_path / 'shot.png'
    picture.write_bytes(b'PNGDATA')
    tmp_db.create_session('steer-image', 'main')
    writer = SessionNodeWriter(tmp_db, 'steer-image')
    writer.append(Call(id='u', role='user', output='start'))
    writer.append(Call(id='u_reply', role='llm', output='', predecessor='u'))
    queue = []

    @function(name='steer_probe', description='test boundary')
    def probe():
        queue.append({'command_id': 'input-img', 'payload': {
            'message': 'LOOK', 'images': [{'path': str(picture), 'media_type': 'image/png'}]}})
        return 'tool done'
    seen = []
    calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield EventStart(partial=_build_partial())
            yield EventDone(reason='toolUse', message=_build_final_with_tool('tool-1', 'steer_probe', {}))
        else:
            steer = next(m for m in context.messages if 'LOOK' in str(m.content))
            seen.extend(block for block in steer.content if isinstance(block, ImageContent))
            yield EventStart(partial=_build_partial())
            yield EventDone(reason='stop', message=_build_final_text('OK'))
    req = _owner_turn(session_id='steer-image', agent_id='main', source='web', user_msg_id='u',
                      user_text='start', permission_mode='bypass')
    tap = make_stream_tap(on_event=lambda _e: None, req=req, assistant_msg_id='u_reply',
                          placeholder_inserted=True, agentic_tool_names=set())
    D._run_loop_blocking(req=req, history=[], on_event=tap, cancel_event=None, stream_fn=stream,
        assistant_msg_id='u_reply', execution_context={
            'canonical_execution': True, 'steer_inputs': queue, 'steer_consumed_ids': set()})
    if takes_images:
        assert [(block.data, block.mime_type) for block in seen] == [('UE5HREFUQQ==', 'image/png')]
    else:
        assert seen == []
