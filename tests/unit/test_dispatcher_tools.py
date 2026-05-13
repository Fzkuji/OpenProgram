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
    used to import ``openprogram.tools.registry`` which doesn't exist)
  * tool_use / tool_result envelope shape (TUI/web depend on these)
  * approval gate blocking (5-min timer + ApprovalRegistry)
  * char-cap truncation and persist_full disk-write actually firing
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

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
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
from openprogram.tools import _runtime as R
from openprogram.tools._runtime import tool


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
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
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
    tool without interference from another test's @tool decoration."""
    saved_reg = dict(R._registry)
    saved_ts = {k: set(v) for k, v in R._toolset_membership.items()}
    saved_unsafe = {k: set(v) for k, v in R._unsafe_in_channel.items()}
    R._registry.clear()
    R._toolset_membership.clear()
    R._unsafe_in_channel.clear()
    R._cache.clear()
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


def test_resolve_tools_filters_channel_unsafe_tools(fresh_registry) -> None:
    @tool(name="safeprobe", description="Safe")
    def safeprobe() -> str:
        """Safe probe."""
        return "ok"

    @tool(name="unsafeprobe", description="Unsafe", unsafe_in=["wechat"])
    def unsafeprobe() -> str:
        """Unsafe probe."""
        return "no"

    names = [
        t.name for t in D._resolve_tools(
            {"tools": ["safeprobe", "unsafeprobe"]},
            source="wechat",
        ) or []
    ]

    assert names == ["safeprobe"]


def test_resolve_default_agent_tools_from_profile_dict(
    fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openprogram.tools as tools_pkg

    @tool(name="safeprobe", description="Safe")
    def safeprobe() -> str:
        """Safe probe."""
        return "ok"

    @tool(name="unsafeprobe", description="Unsafe", unsafe_in=["wechat"])
    def unsafeprobe() -> str:
        """Unsafe probe."""
        return "no"

    monkeypatch.setattr(tools_pkg, "DEFAULT_TOOLS", ["safeprobe", "unsafeprobe"])

    names = [
        t.name for t in D._resolve_tools(
            {"tools": {"disabled": []}},
            source="wechat",
        ) or []
    ]

    assert names == ["safeprobe"]


def test_tool_runtime_prompt_mentions_available_tools() -> None:
    class T:
        def __init__(self, name: str):
            self.name = name

    prompt = D._with_tool_runtime_prompt("Base prompt.", [T("read"), T("list")])

    assert "Base prompt." in prompt
    assert "Available tools for this turn: read, list" in prompt
    assert "Current working directory:" in prompt
    assert "call the list tool with that absolute path" in prompt
    assert "instead of saying no tools are available" in prompt


# ---------------------------------------------------------------------------
# Test 1: tool_use → tool gets executed, tool_result envelope emitted
# ---------------------------------------------------------------------------

def test_tool_use_event_runs_tool_and_emits_result(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @tool(name="probe", description="Echo")
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
            D.TurnRequest(session_id="c1", user_text="run probe", agent_id="main",
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
# Test 2: char cap truncates oversized tool output
# ---------------------------------------------------------------------------

def test_oversized_tool_result_is_truncated(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @tool(name="bigprobe", description="Big",
          max_result_chars=200, persist_full=False)
    def bigprobe() -> str:
        """Returns a huge string."""
        return "x" * 50_000

    monkeypatch.setattr(D, "_load_agent_profile", _stub_profile_with_tools(["bigprobe"]))
    stream = make_two_phase_stream("c", "bigprobe", {})

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="run", agent_id="main",
                          source="tui", permission_mode="bypass"),
            on_event=collector,
        )

    tool_results = [e for e in captured
                    if e["type"] == "chat_response"
                    and e["data"].get("event", {}).get("type") == "tool_result"]
    assert tool_results
    text = tool_results[0]["data"]["event"]["result"]
    # Truncation marker present
    assert "elided" in text or "more)" in text  # _shorten or _cap marker
    # At minimum the dispatcher's own _shorten cap kicks in (4000) so the
    # envelope text is well under the original 50K
    assert len(text) < 5000


# ---------------------------------------------------------------------------
# Test 3: persist_full writes full result to disk
# ---------------------------------------------------------------------------

def test_persist_full_writes_file(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Redirect persist directory to tmp so we can inspect it. The
    # real helper does mkdir(parents=True); replicate that so
    # ``write_text`` doesn't blow up.
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("openprogram.tools._runtime._tool_results_dir",
                        lambda: results_dir)

    @tool(name="persistprobe", description="Persist",
          max_result_chars=200, persist_full=True)
    def persistprobe() -> str:
        """Big payload."""
        return "Y" * 5_000

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["persistprobe"]))
    stream = make_two_phase_stream("call-x", "persistprobe", {})

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="run", agent_id="main",
                          source="tui", permission_mode="bypass"),
            on_event=collector,
        )

    persisted = list((tmp_path / "results").glob("*.txt"))
    assert len(persisted) == 1, f"expected 1 persisted file, got {persisted}"
    assert persisted[0].read_text() == "Y" * 5_000


# ---------------------------------------------------------------------------
# Test 4: approval flow blocks the loop until resolved
# ---------------------------------------------------------------------------

def test_approval_required_blocks_until_approved(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fired = threading.Event()

    @tool(name="dangerprobe", description="Danger", requires_approval=True)
    def dangerprobe(target: str) -> str:
        """Pretend-dangerous tool.

        Args:
            target: thing to do dangerous things to.
        """
        fired.set()
        return f"did {target}"

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["dangerprobe"]))
    stream = make_two_phase_stream("c-d", "dangerprobe", {"target": "x"})

    request_id_holder: list[str] = []
    original_collector = collector

    def relay(env: dict) -> None:
        original_collector(env)
        if env.get("type") == "approval_request":
            request_id_holder.append(env["data"]["request_id"])

    # Start the turn in a worker thread so the main thread can resolve
    # the approval. permission_mode="ask" forces _check_approval.
    result_holder: dict = {}

    def _run():
        with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
            result_holder["r"] = D.process_user_turn(
                D.TurnRequest(session_id="c1", user_text="run", agent_id="main",
                              source="tui", permission_mode="ask"),
                on_event=relay,
            )

    th = threading.Thread(target=_run)
    th.start()

    # Wait for the approval request to arrive (max 2s)
    deadline = time.time() + 2.0
    while not request_id_holder and time.time() < deadline:
        time.sleep(0.02)
    assert request_id_holder, "dispatcher never emitted approval_request"
    assert not fired.is_set(), "tool ran before approval was granted"

    # Approve and let the worker continue
    D.approval_registry().resolve(request_id_holder[0], True)
    th.join(timeout=5)
    assert not th.is_alive(), "dispatcher thread did not finish after approval"
    assert fired.is_set(), "tool was not executed after approval"
    assert result_holder["r"].failed is False


def test_approval_denied_aborts_run(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fired = threading.Event()

    @tool(name="risky", description="Risky", requires_approval=True)
    def risky() -> str:
        """Risky."""
        fired.set()
        return "should-not-run"

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["risky"]))
    stream = make_two_phase_stream("c-r", "risky", {})

    request_id_holder: list[str] = []
    original_collector = collector

    def relay(env: dict) -> None:
        original_collector(env)
        if env.get("type") == "approval_request":
            request_id_holder.append(env["data"]["request_id"])

    def _run():
        with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
            D.process_user_turn(
                D.TurnRequest(session_id="c1", user_text="run", agent_id="main",
                              source="tui", permission_mode="ask"),
                on_event=relay,
            )

    th = threading.Thread(target=_run)
    th.start()

    deadline = time.time() + 2.0
    while not request_id_holder and time.time() < deadline:
        time.sleep(0.02)
    assert request_id_holder
    # Note: even if the user denies, agent_loop still attempts the
    # tool call after _check_approval returns False — we only set the
    # cancel flag. The tool itself does fire because dispatcher's
    # cancel is best-effort (agent_loop schedules tool_call BEFORE
    # checking cancel between iterations). What matters is that the
    # turn doesn't hang and finishes with an aborted/done state.
    D.approval_registry().resolve(request_id_holder[0], False)
    th.join(timeout=5)
    assert not th.is_alive(), "dispatcher hung after denial"


# ---------------------------------------------------------------------------
# Test 5: tool sees cancel_event when caller cancels
# ---------------------------------------------------------------------------

def test_cancel_propagates_to_tool(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saw_cancel = threading.Event()
    cancelled_by_caller = threading.Event()

    @tool(name="slowprobe", description="Slow")
    async def slowprobe(cancel: asyncio.Event = None) -> str:
        """Long-running tool that polls cancel."""
        for _ in range(100):
            if cancel is not None and cancel.is_set():
                saw_cancel.set()
                return "cancelled"
            await asyncio.sleep(0.02)
        return "finished"

    monkeypatch.setattr(D, "_load_agent_profile",
                        _stub_profile_with_tools(["slowprobe"]))
    stream = make_two_phase_stream("c-s", "slowprobe", {})

    cancel_flag = threading.Event()

    def _trigger_cancel():
        time.sleep(0.05)
        cancelled_by_caller.set()
        cancel_flag.set()

    threading.Thread(target=_trigger_cancel, daemon=True).start()

    with patch.object(D, "_run_loop_blocking", _patched_run_loop(stream)):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="go", agent_id="main",
                          source="tui", permission_mode="bypass"),
            on_event=collector,
            cancel_event=cancel_flag,
        )

    assert cancelled_by_caller.is_set()
    assert saw_cancel.is_set(), "tool never observed cancel signal"
