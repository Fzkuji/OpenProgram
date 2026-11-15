"""Self-recursion guard for Agent method (regression test).

An agentic harness (e.g. ``wiki_agent``) runs an inner agent loop via
``runtime.exec()`` with the FULL toolset, and the FULL toolset is every
exposed tool, harness entry points included (gui_agent /
wiki_agent). The inner
model could in principle call ``wiki_agent`` from inside ``wiki_agent``
→ unbounded nesting (the "7-layer nesting" root cause).

The guard is a recursion depth cap: the wrapper bumps a per-method
callable identity depth counter and raises ``RecursionError`` if the SAME
function re-enters itself past ``_MAX_AGENTIC_RECURSION_DEPTH``. The
function's own tool stays visible.

These tests assert the guard directly (no live LLM):

  * the depth backstop raises past the limit;
  * legitimate distinct sub-calls (A→B) are NOT collateral damage;
  * the depth contextvar is restored after return / raise;
  * the self-deny was removed (function name no longer in policy deny).
"""

from __future__ import annotations

from openprogram.runtime.agent import Agent

import pytest
import inspect

from openprogram.runtime.agent.call_state import (

    _recursion_depth,
    _MAX_AGENTIC_RECURSION_DEPTH,
)
from openprogram.runtime.agent.runtime import (
    Runtime,
    _current_tool_policy,
)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


def _deny() -> list[str]:
    return list((_current_tool_policy.get(None) or {}).get("deny") or [])


def _depth(method) -> int:
    source = inspect.unwrap(method)
    if inspect.ismethod(source):
        source = source.__func__
    return (_recursion_depth.get(None) or {}).get(source, 0)


def test_self_name_NOT_denied_during_call(runtime):
    """The old deny mechanism is gone: a function's own name must no
    longer be force-injected into the tool-policy deny set."""
    seen = {}

    class _WikiAgentAgent(Agent):
        method_options = {'wiki_agent': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'wiki_agent'}}

        def wiki_agent(self, task, runtime=None):
            seen["deny"] = _deny()
            return "ok"

    wiki_agent = _WikiAgentAgent().wiki_agent

    assert wiki_agent("x", runtime=runtime) == "ok"
    assert "wiki_agent" not in seen["deny"]


# --- Layer 2: recursion-depth backstop -----------------------------------

def test_depth_increments_during_call(runtime):
    seen = {}

    class _FAgent(Agent):
        method_options = {'f': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'f'}}

        def f(self, runtime=None):
            seen["depth"] = _depth(f)
            return "done"

    f = _FAgent().f

    assert f(runtime=runtime) == "done"
    assert seen["depth"] == 1


def test_depth_backstop_raises_past_limit(runtime):
    """A runaway self-re-entry past the cap aborts instead of nesting
    forever."""
    calls = {"n": 0}

    class _LoopAgent(Agent):
        method_options = {'loop': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'loop'}}

        def loop(self, runtime=None):
            calls["n"] += 1
            # Re-enter ourselves unconditionally — a runaway model.
            return loop(runtime=runtime)

    loop = _LoopAgent().loop

    with pytest.raises(RecursionError) as exc:
        loop(runtime=runtime)
    assert "loop" in str(exc.value)
    assert str(_MAX_AGENTIC_RECURSION_DEPTH) in str(exc.value)
    # Bounded: it entered the body exactly up to the limit, no further.
    assert calls["n"] == _MAX_AGENTIC_RECURSION_DEPTH


def test_distinct_subcalls_not_collateral_damage(runtime):
    """A→B legitimate sub-call: distinct methods have independent depth, so
    deep B nesting under A never trips A's limit and vice versa."""
    seen = {}

    class _InnerAgent(Agent):
        method_options = {'inner': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'inner'}}

        def inner(self, runtime=None):
            seen["inner_depth"] = _depth(inner)
            seen["outer_depth_seen_from_inner"] = _depth(outer)
            return "inner"

    inner = _InnerAgent().inner

    class _OuterAgent(Agent):
        method_options = {'outer': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'outer'}}

        def outer(self, runtime=None):
            seen["outer_depth"] = _depth(outer)
            return inner(runtime=runtime)

    outer = _OuterAgent().outer

    assert outer(runtime=runtime) == "inner"
    assert seen["outer_depth"] == 1
    assert seen["inner_depth"] == 1
    # inner does NOT inherit outer's count (per-method identity), and vice versa.
    assert seen["outer_depth_seen_from_inner"] == 1


def test_depth_restored_after_return(runtime):
    class _FAgent(Agent):
        method_options = {'f': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'f'}}

        def f(self, runtime=None):
            return "done"

    f = _FAgent().f

    before = _depth(f)
    f(runtime=runtime)
    assert _depth(f) == before


def test_depth_restored_after_exception(runtime):
    class _BoomAgent(Agent):
        method_options = {'boom': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'boom'}}

        def boom(self, runtime=None):
            raise ValueError("kaboom")

    boom = _BoomAgent().boom

    before = _depth(boom)
    with pytest.raises(ValueError):
        boom(runtime=runtime)
    assert _depth(boom) == before
