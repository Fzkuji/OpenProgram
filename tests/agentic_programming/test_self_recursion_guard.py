"""Self-recursion guard for @agentic_function (regression test).

An agentic harness (e.g. ``wiki_agent``) runs an inner agent loop via
``runtime.exec()`` with the FULL toolset, and the FULL toolset lists the
harness entry points themselves (gui_agent / research_agent /
wiki_agent — see ``openprogram.functions.TOOLSETS["full"]``). The inner
model could in principle call ``wiki_agent`` from inside ``wiki_agent``
→ unbounded nesting (the "7-layer nesting" root cause).

The guard is now two-layer (the old tool-policy *deny* that hid the
function from its own toolset was removed):

  1. Primary — situational steering: a prompt prefix injected by
     ``runtime._situational_prefix`` tells the inner model it is already
     running inside the function and must not call it. The function's
     own tool stays VISIBLE; the model is steered away.
  2. Backstop — recursion depth cap: the wrapper bumps a per-function
     name depth counter and raises ``RecursionError`` if the SAME
     function re-enters itself past ``_MAX_AGENTIC_RECURSION_DEPTH``.

These tests assert both layers directly (no live LLM):

  * the situational prefix carries the recursion warning + function name;
  * the depth backstop raises past the limit;
  * legitimate distinct sub-calls (A→B) are NOT collateral damage;
  * the depth contextvar is restored after return / raise;
  * the self-deny was removed (function name no longer in policy deny).
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.function import (
    agentic_function,
    _recursion_depth,
    _MAX_AGENTIC_RECURSION_DEPTH,
)
from openprogram.agentic_programming.runtime import (
    Runtime,
    _current_tool_policy,
    _situational_prefix,
)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


def _deny() -> list[str]:
    return list((_current_tool_policy.get(None) or {}).get("deny") or [])


def _depth(name: str) -> int:
    return (_recursion_depth.get(None) or {}).get(name, 0)


# --- Layer 1: situational steering prompt ---------------------------------

def test_situational_prefix_warns_against_self_call():
    text = _situational_prefix("wiki_agent", "Answer wiki questions.")
    # Names the function and warns about recursion.
    assert "wiki_agent" in text
    assert "do NOT call it" in text
    assert "recursion" in text.lower()
    # The docstring is demoted to the end, not the headline.
    assert "Answer wiki questions." in text
    assert text.index("recursion") < text.index("Answer wiki questions.")


def test_situational_prefix_handles_empty_doc():
    text = _situational_prefix("gui_agent", "")
    assert "gui_agent" in text
    assert "This function's job" not in text


# --- Self-deny removed: the tool stays visible ---------------------------

def test_self_name_NOT_denied_during_call(runtime):
    """The old deny mechanism is gone: a function's own name must no
    longer be force-injected into the tool-policy deny set."""
    seen = {}

    @agentic_function
    def wiki_agent(task, runtime=None):
        seen["deny"] = _deny()
        return "ok"

    assert wiki_agent("x", runtime=runtime) == "ok"
    assert "wiki_agent" not in seen["deny"]


# --- Layer 2: recursion-depth backstop -----------------------------------

def test_depth_increments_during_call(runtime):
    seen = {}

    @agentic_function
    def f(runtime=None):
        seen["depth"] = _depth("f")
        return "done"

    assert f(runtime=runtime) == "done"
    assert seen["depth"] == 1


def test_depth_backstop_raises_past_limit(runtime):
    """A runaway self-re-entry past the cap aborts instead of nesting
    forever."""
    calls = {"n": 0}

    @agentic_function
    def loop(runtime=None):
        calls["n"] += 1
        # Re-enter ourselves unconditionally — a runaway model.
        return loop(runtime=runtime)

    with pytest.raises(RecursionError) as exc:
        loop(runtime=runtime)
    assert "loop" in str(exc.value)
    assert str(_MAX_AGENTIC_RECURSION_DEPTH) in str(exc.value)
    # Bounded: it entered the body exactly up to the limit, no further.
    assert calls["n"] == _MAX_AGENTIC_RECURSION_DEPTH


def test_distinct_subcalls_not_collateral_damage(runtime):
    """A→B legitimate sub-call: distinct names have independent depth, so
    deep B nesting under A never trips A's limit and vice versa."""
    seen = {}

    @agentic_function
    def inner(runtime=None):
        seen["inner_depth"] = _depth("inner")
        seen["outer_depth_seen_from_inner"] = _depth("outer")
        return "inner"

    @agentic_function
    def outer(runtime=None):
        seen["outer_depth"] = _depth("outer")
        return inner(runtime=runtime)

    assert outer(runtime=runtime) == "inner"
    assert seen["outer_depth"] == 1
    assert seen["inner_depth"] == 1
    # inner does NOT inherit outer's count (per-name), and vice versa.
    assert seen["outer_depth_seen_from_inner"] == 1


def test_depth_restored_after_return(runtime):
    @agentic_function
    def f(runtime=None):
        return "done"

    before = _depth("f")
    f(runtime=runtime)
    assert _depth("f") == before


def test_depth_restored_after_exception(runtime):
    @agentic_function
    def boom(runtime=None):
        raise ValueError("kaboom")

    before = _depth("boom")
    with pytest.raises(ValueError):
        boom(runtime=runtime)
    assert _depth("boom") == before
