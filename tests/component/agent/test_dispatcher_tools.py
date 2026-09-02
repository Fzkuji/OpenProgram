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
from openprogram.programs import _runtime as R
from openprogram.programs._runtime import function


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


def _owner_turn(**kwargs) -> D.TurnRequest:
    from openprogram.agent.authority import local_owner_authority

    return D.TurnRequest(
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
                           *, wait_id: str):
    """Create the durable approval safe point consumed by the wrapper."""
    import openprogram.execution as execution_module
    from openprogram.execution import RuntimeControlService
    from openprogram.execution.attempts import AttemptStore
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.model import CapabilitySet
    from openprogram.execution.store import ExecutionStore
    from openprogram.execution.waits import DurableWaitStore

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
        owner_id="dispatcher-test", ttl_seconds=30,
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
            "prompt": "允许执行 dangerprobe？", "options": ["允许", "拒绝"],
            "multi": False, "allow_custom": False, "detail": "dangerprobe",
            "schema": {}, "questions": [], "tool": "dangerprobe", "args": {},
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
        t.name for t in D._resolve_tools(
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
        t.name for t in D._resolve_tools(
            {"tools": {"mode": "automatic"}}, source="web"
        ) or []
    }
    selected = {
        t.name for t in D._resolve_tools(
            {"tools": {"mode": "selected", "allowed": ["catalog_research"]}},
            source="web",
        ) or []
    }
    none = D._resolve_tools({"tools": {"mode": "none"}}, source="web")

    assert automatic == {"catalog_read", "catalog_research"}
    assert selected == {"catalog_research"}
    assert none == []


def test_inherit_override_uses_agent_policy(fresh_registry) -> None:
    @function(name="profile_read", description="Read")
    def profile_read() -> str:
        return "read"

    names = {
        t.name for t in D._resolve_tools(
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
        tool.name for tool in D._resolve_tools(
            {"tools": {"mode": "selected", "allowed": ["deferred_report"]}},
            source="web",
        ) or []
    }
    assert names == {"tool_search", "deferred_report"}


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
# Test 2: char cap truncates oversized tool output
# ---------------------------------------------------------------------------

def test_oversized_tool_result_is_truncated(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @function(name="bigprobe", description="Big",
          max_result_chars=200, persist_full=False)
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
    monkeypatch.setattr("openprogram.programs._runtime._tool_results_dir",
                        lambda: results_dir)

    @function(name="persistprobe", description="Persist",
          max_result_chars=200, persist_full=True)
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

    persisted = list((tmp_path / "results").glob("*.txt"))
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
    )
    _answer_wait(store, service, wait, {"answer": "允许", "scope": "once"})
    from openprogram.agent.run_control import reset_preapproved_wait_id, set_preapproved_wait_id
    req = _owner_turn(
        session_id="c1", user_text="run", agent_id="main", source="tui",
        permission_mode="ask",
    )
    wrapped = D._wrap_with_approval(
        R._registry["dangerprobe"], req, lambda _event: None,
    )
    token = set_preapproved_wait_id(wait.wait_id)
    try:
        result = asyncio.run(wrapped.execute(
            "call-dangerprobe", {"target": "x"}, None, lambda _update: None,
        ))
    finally:
        reset_preapproved_wait_id(token)
    assert fired.is_set(), "tool was not executed after approval"
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
    )
    _decline_wait(store, service, wait)
    from openprogram.agent.run_control import reset_preapproved_wait_id, set_preapproved_wait_id
    req = _owner_turn(
        session_id="c1", user_text="run", agent_id="main", source="tui",
        permission_mode="ask",
    )
    wrapped = D._wrap_with_approval(R._registry["risky"], req, lambda _event: None)
    token = set_preapproved_wait_id(wait.wait_id)
    try:
        result = asyncio.run(wrapped.execute(
            "call-risky", {}, None, lambda _update: None,
        ))
    finally:
        reset_preapproved_wait_id(token)
    assert result.is_error is True
    assert "denied" in result.content[0].text
    assert not fired.is_set()


# ---------------------------------------------------------------------------
# Test 5: tool sees cancel_event when caller cancels
# ---------------------------------------------------------------------------

def test_cancel_propagates_to_tool(
    tmp_db: SessionDB, captured, collector, fresh_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saw_cancel = threading.Event()
    cancelled_by_caller = threading.Event()

    @function(name="slowprobe", description="Slow")
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
            _owner_turn(session_id="c1", user_text="go", agent_id="main",
                        source="tui", permission_mode="bypass"),
            on_event=collector,
            cancel_event=cancel_flag,
        )

    assert cancelled_by_caller.is_set()
    assert saw_cancel.is_set(), "tool never observed cancel signal"


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
            D.TurnRequest(session_id="c1", user_text="run", agent_id="worker",
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
    assert any("hard constraint" in text and "denied" in text
               for text in tool_results)
