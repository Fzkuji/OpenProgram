"""Spawn provenance invariant across the spawn entry points.

The canonical Job Agent input records the spawning node in
``turn_request.spawn_caller`` whenever a caller exists. ``branch_from``
separately records whether the new turn starts from a specific branch tip.

The sync agent() path (commit 1d1fe016) had dropped this; the async
runner already had it. These tests pin the entry points so a refactor
can't silently re-orphan a sub-branch at the root.

The canonical runner path invokes ``AgentProductionDriver`` with a
``TurnRequest``; patching its default runner captures the durable request
fields without bypassing admission and activation.
"""
from __future__ import annotations

from contextvars import copy_context

import pytest

from openprogram.agent.sub_agent_run import AgentTurnResult


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated SessionStore + a parent session with one committed turn."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "init")
    return s


@pytest.fixture
def captured_run(monkeypatch):
    """Replace run_agent_turn (+ the attach-pointer writer) so the spawn
    impls run without a real LLM and we can read back spawn_caller."""
    cap = {}

    def fake_run(**kwargs):
        cap.update(kwargs)
        return AgentTurnResult(head_id="head_x", final_text="(reply)")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None,
    )
    return cap


def _set_config(monkeypatch, **agent_keys):
    """Pin the ``agent.*`` budget settings config_schema exposes."""
    monkeypatch.setattr(
        "openprogram.setup._read_config", lambda: {"agent": dict(agent_keys)},
    )


def _run_with_ctx(fn, *, session_id, turn_id):
    """Run ``fn`` with the session/turn ContextVars the spawn impls read."""
    from openprogram.agent.run_control import _current_session_id
    from openprogram.store import _current_turn_id

    def _go():
        t1 = _current_session_id.set(session_id)
        t2 = _current_turn_id.set(turn_id)
        try:
            return fn()
        finally:
            _current_session_id.reset(t1)
            _current_turn_id.reset(t2)

    return copy_context().run(_go)


# ---- entry 1: sync agent() (agent.py _agent_impl) -----------------------

def test_agent_sync_clean_passes_spawn_caller(store, captured_run):
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    _run_with_ctx(
        lambda: _agent_impl(prompt="go", start_from="clean"),
        session_id="p1", turn_id="a1",
    )
    assert captured_run["branch_from"] is None
    assert captured_run["spawn_caller"] == "a1"


def test_agent_sync_clean_uses_call_id_when_turn_id_missing(store, captured_run):
    """Composer-launched @agentic_function has no assistant turn id.
    spawn_caller must be the function's DAG node (_call_id), not ROOT."""
    from openprogram.agent.run_control import _current_session_id
    from openprogram.agentic_programming.function import _call_id
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    from openprogram.store import _current_turn_id

    def _go():
        t1 = _current_session_id.set("p1")
        t2 = _current_turn_id.set("")
        t3 = _call_id.set("wf_node")
        try:
            return _agent_impl(prompt="go", start_from="clean")
        finally:
            _call_id.reset(t3)
            _current_turn_id.reset(t2)
            _current_session_id.reset(t1)

    copy_context().run(_go)
    assert captured_run["branch_from"] is None
    assert captured_run["spawn_caller"] == "wf_node"


def test_agent_sync_inherit_passes_no_spawn_caller(store, captured_run):
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    _run_with_ctx(
        lambda: _agent_impl(prompt="go", start_from="inherit"),
        session_id="p1", turn_id="a1",
    )
    assert captured_run["branch_from"] == "a1"
    assert captured_run["spawn_caller"] is None


def test_agent_sync_fork_passes_no_spawn_caller(store, captured_run):
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    _run_with_ctx(
        lambda: _agent_impl(prompt="go", start_from="p1:u1"),
        session_id="p1", turn_id="a1",
    )
    # fork off an existing node → branch_from set → no spawn_caller.
    assert captured_run["branch_from"] == "u1"
    assert captured_run["spawn_caller"] is None


# ---- entry 3: async runner (task/runner.py _run_one) --------------------

def test_runner_clean_passes_spawn_caller(store, monkeypatch):
    """The async worker calls the execution primitive with
    spawn_caller=caller_msg_id when context_mode=clean (branch_from=None).
    Drive a real task through the pool with a fake worker that records it."""
    cap = {}

    def fake_run(*, request, cancel_event, **_kwargs):
        cap["branch_from"] = request.branch_from
        cap["spawn_caller"] = request.spawn_caller
        return AgentTurnResult(head_id="head_ok", final_text="hello")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.job import get_runner
    runner = get_runner()
    try:
        tid = runner.spawn_job(
            session_id="p1", prompt="go", agent_id="main",
            context_mode="clean", caller_msg_id="a1",
        )
        final = runner.await_job(tid, timeout=5.0)
        assert final is not None
        assert cap["branch_from"] is None
        assert cap["spawn_caller"] == "a1"
    finally:
        runner_mod.shutdown_runner()


def test_runner_inherit_preserves_canonical_spawn_caller(store, monkeypatch):
    cap = {}

    def fake_run(*, request, cancel_event, **_kwargs):
        cap["branch_from"] = request.branch_from
        cap["spawn_caller"] = request.spawn_caller
        return AgentTurnResult(head_id="head_ok", final_text="hello")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_run),
    )
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.job import get_runner
    runner = get_runner()
    try:
        tid = runner.spawn_job(
            session_id="p1", prompt="go", agent_id="main",
            context_mode="inherit", caller_msg_id="a1", parent_msg_id="a1",
        )
        final = runner.await_job(tid, timeout=5.0)
        assert final is not None
        assert cap["branch_from"] == "a1"
        assert cap["spawn_caller"] == "a1"
    finally:
        runner_mod.shutdown_runner()


# ---- entry 1b: background agent() (_agent_impl run_in_background=True) ---

def test_agent_async_passes_caller_and_depth(store, monkeypatch):
    """The run_in_background=True branch must anchor the spawn to the calling turn
    (caller_msg_id) and carry the incremented chain depth — dropping
    caller_msg_id re-orphaned async spawns at ROOT (the c919c000 case)."""
    cap = {}

    def fake_async(**kw):
        cap.update(kw)
        return "t_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async,
    )
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    out = _run_with_ctx(
        lambda: _agent_impl(prompt="go", start_from="clean", run_in_background=True),
        session_id="p1", turn_id="a1",
    )
    assert "agent spawned async" in out
    assert cap["caller_msg_id"] == "a1"
    assert cap["chain_messages"] == 1
    # The child IS the new generation; the reply turn back on this lane
    # is not, so it is handed the spawner's own count.
    assert cap["chain_generations"] == 1
    assert cap["caller_chain_generations"] == 0


# ---- budget guards --------------------------------------------------------

def _spawn_at(counts: dict, fn=None):
    """Call ``_agent_impl`` with the chain counters bound to ``counts``
    (keys: generations, messages), the way the runner binds them on a
    child or a follow-up turn."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_generations, set_chain_messages,
    )
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl

    def _call():
        tokens = [
            set_chain_generations(counts.get("generations", 0)),
            set_chain_messages(counts.get("messages", 0)),
        ]
        try:
            return (fn or (lambda: _agent_impl(
                prompt="go", start_from="clean")))()
        finally:
            for tok in tokens:
                tok.var.reset(tok)

    return _run_with_ctx(_call, session_id="p1", turn_id="a1")


def test_agent_refuses_at_max_spawn_depth(store, captured_run):
    """The generation budget (MAX_SPAWN_DEPTH=1) is deliberately tighter
    than the message budget: only the main agent may agent(); a spawned
    agent delegating again gets refused."""
    from openprogram.programs.tools.agents.agent.agent.agent import MAX_SPAWN_DEPTH
    out = _spawn_at({"generations": MAX_SPAWN_DEPTH})
    assert "[agent refused]" in out and "generations" in out
    assert "spawn_caller" not in captured_run  # never reached the spawn


def test_agent_spawned_agent_cannot_redelegate(store, captured_run):
    """One generation in (a spawned agent) must NOT agent() again — it
    does the work itself with its own tools."""
    out = _spawn_at({"generations": 1})
    assert "[agent refused]" in out
    assert "spawn_caller" not in captured_run


def test_spawn_depth_zero_means_unlimited(store, captured_run, monkeypatch):
    """agent.max_spawn_depth=0 turns the generation guard off entirely —
    a chain 50 generations in still spawns."""
    _set_config(monkeypatch, max_spawn_depth=0)
    out = _spawn_at({"generations": 50})
    assert "[agent refused]" not in out
    assert captured_run["spawn_caller"] == "a1"   # the spawn really ran


def test_messages_alone_can_refuse_a_spawn(store, captured_run):
    """A spawn hands the child a message, so a chain out of messages
    cannot spawn either, however many generations it has left."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        MAX_MESSAGES,
    )
    out = _spawn_at({"generations": 0, "messages": MAX_MESSAGES})
    assert "[agent refused]" in out and "messages" in out
    assert "spawn_caller" not in captured_run


def test_the_two_budgets_do_not_spend_each_other(store, captured_run):
    """Messages spent well past the generation limit still leave the
    generation budget intact: this is the state a coordinator is in after
    reading a worker's result, and it must be able to spawn again."""
    out = _spawn_at({"generations": 0, "messages": 5})
    assert "[agent refused]" not in out
    assert captured_run["spawn_caller"] == "a1"


def test_agent_sync_child_sees_both_counts_incremented(store, monkeypatch):
    """The sync path binds both counts + 1 around the child turn, so a
    chain of agent()-inside-agent() trips the generation guard instead of
    recursing forever (each generation used to start back at 0)."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        current_chain_generations, current_chain_messages,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult as _R
    seen = {}

    def fake_run(**kw):
        seen["messages"] = current_chain_messages()
        seen["generations"] = current_chain_generations()
        return _R(head_id="h", final_text="(reply)")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None,
    )
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
    _run_with_ctx(
        lambda: _agent_impl(prompt="go", start_from="clean"),
        session_id="p1", turn_id="a1",
    )
    assert seen == {"messages": 1, "generations": 1}


# ---- tool exposure follows the remaining budget ---------------------------

_TOOLS_UNDER_TEST = ["agent", "read"]


def _spawn_tool_names(depth: int) -> set:
    """Tool names the dispatcher would hand a turn whose chain has
    passed ``depth`` messages. Goes through ``resolve_tools`` — the very
    resolver loop_runner calls, in the same execution context the depth
    is bound in, which is what makes a ContextVar-backed ``can_use``
    hook work at render time."""
    from contextvars import copy_context
    from openprogram.agent.internals._model_tools import resolve_tools
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        set_chain_messages,
    )

    def _go():
        set_chain_messages(depth)
        tools = resolve_tools(
            {"tools": list(_TOOLS_UNDER_TEST)}, source="agent_spawn",
        )
        return {t.name for t in (tools or [])}

    return copy_context().run(_go)


def test_spawn_tools_visible_while_budget_remains():
    """A spawned agent keeps the canonical agent tool while messages remain."""
    names = _spawn_tool_names(1)
    assert "agent" in names


def test_spawn_tools_disappear_when_budget_spent():
    """Both budgets spent → the agent tool leaves the listing entirely."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        MAX_MESSAGES,
    )
    names = _spawn_tool_names(MAX_MESSAGES)
    assert "agent" not in names
    # Everything else is untouched.
    assert "read" in names


def test_message_budget_zero_keeps_tools_forever(monkeypatch):
    """agent.max_messages=0 = no limit, so the agent tool stays visible."""
    _set_config(monkeypatch, max_messages=0)
    assert "agent" in _spawn_tool_names(999)
