"""Tool expansion must be DETERMINISTIC — the tools array sits at the prompt
cache prefix root (providers/cache_policy.py), so if the same intent expands
to a different order/set across turns, the whole prompt cache misses every
turn (silently, no error). These tests lock the property: same intent →
byte-identical tool name sequence.

Design: docs/design/runtime/tool-toggle-management.md §4.2 (hard constraint),
§6 step 1.
"""
from __future__ import annotations

from openprogram.functions import agent_tools, DEFAULT_TOOLS


def _names(tools):
    return [t.name for t in tools]


def test_default_expansion_deterministic():
    a = _names(agent_tools())
    b = _names(agent_tools())
    assert a == b, "DEFAULT_TOOLS expansion must be order-stable across calls"


def test_fresh_default_includes_playwright_browser_only():
    names = _names(agent_tools())
    assert "playwright_browser" in names
    assert "agent_browser" not in names


def test_default_names_arg_deterministic():
    a = _names(agent_tools(names=list(DEFAULT_TOOLS)))
    b = _names(agent_tools(names=list(DEFAULT_TOOLS)))
    assert a == b


def test_toolset_expansion_deterministic():
    # 'full' preset is the widest; expanding it twice must match exactly.
    a = _names(agent_tools(toolset="full"))
    b = _names(agent_tools(toolset="full"))
    assert a == b, "toolset expansion must be order-stable (cache prefix)"


def test_names_subset_preserves_request_order():
    # Explicit name list expands in the GIVEN order (so the caller controls
    # a stable order; no internal reshuffle).
    subset = ["read", "write", "bash"]
    got = _names(agent_tools(names=subset))
    # every requested+available name appears, and relative order matches input
    filtered = [n for n in subset if n in got]
    assert got == filtered


def test_repeated_expansion_no_drift_many_rounds():
    # Run several rounds; any nondeterminism (set iteration, dict churn)
    # would surface as a diff somewhere in the sequence.
    first = _names(agent_tools(toolset="full"))
    for _ in range(5):
        assert _names(agent_tools(toolset="full")) == first
