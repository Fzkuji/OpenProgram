"""Tool intent (not snapshot) — session_config round-trips intent and heals
legacy snapshots, and the dispatcher expander honours the web_search overlay.

This is the regression suite for the "old sessions can't see newly-added
tools" bug. Design: docs/design/runtime/tool-toggle-management.md.
"""
from __future__ import annotations

from openprogram.agent.session_config import (
    SessionRunConfig,
    tools_override_from_config,
)
from openprogram.functions import DEFAULT_TOOLS


# ── tools_override_from_config: intent in → intent out ──

def test_enabled_true_yields_dict_intent():
    out = tools_override_from_config(SessionRunConfig(tools_enabled=True))
    assert isinstance(out, dict) and out.get("enabled") is True
    # crucially NOT a materialized list snapshot
    assert not isinstance(out, list)


def test_enabled_false_yields_empty():
    out = tools_override_from_config(SessionRunConfig(tools_enabled=False))
    assert out == []


def test_web_search_overlays_intent():
    out = tools_override_from_config(
        SessionRunConfig(tools_enabled=True, web_search=True))
    assert isinstance(out, dict) and out.get("web_search") is True


def test_toolset_intent_passthrough():
    out = tools_override_from_config(
        SessionRunConfig(tools_enabled=True, toolset="research"))
    assert isinstance(out, dict) and out.get("toolset") == "research"


def test_no_config_is_none():
    out = tools_override_from_config(SessionRunConfig())
    assert out is None  # fall back to agent profile


def test_dict_override_passthrough():
    cfg = SessionRunConfig(tools_override={"enabled": True, "toolset": "research"})
    out = tools_override_from_config(cfg)
    assert out == {"enabled": True, "toolset": "research"}


# ── explicit user selection (list) passed through verbatim ──

def test_explicit_selection_kept():
    # An explicit tool-name list (a genuine user selection, e.g. web-search
    # only) is passed through as-is — never materialized/expanded, never
    # rewritten.
    picks = ["read", "write"]
    cfg = SessionRunConfig(tools_enabled=True, tools_override=picks)
    out = tools_override_from_config(cfg)
    assert out == picks


def test_web_search_only_selection():
    # tools off + web_search on → the one-element ["web_search"] selection.
    cfg = SessionRunConfig(tools_enabled=True, tools_override=["web_search"])
    out = tools_override_from_config(cfg)
    assert out == ["web_search"]


# ── end-to-end: the bug's reproduction, now fixed ──

def test_intent_expands_to_live_tools_including_new_ones():
    """A session storing {enabled: True} expands to the CURRENT DEFAULT_TOOLS
    — so any tool added to DEFAULT_TOOLS later is automatically visible.
    This is exactly what the frozen snapshot could not do."""
    from openprogram.agent._model_tools import resolve_tools
    intent = tools_override_from_config(SessionRunConfig(tools_enabled=True))
    resolved = resolve_tools({}, intent, source="web")
    names = {t.name for t in (resolved or [])}
    # the collaboration tools added to DEFAULT_TOOLS must be present
    assert "message_branch" in names
    assert "list_sessions" in names


def test_web_search_overlay_adds_the_tool():
    """web_search intent → web_search actually appears in the expanded set
    (the 改C requirement: overlay or it'd be silently missing)."""
    from openprogram.agent._model_tools import resolve_tools
    intent = tools_override_from_config(
        SessionRunConfig(tools_enabled=True, web_search=True))
    resolved = resolve_tools({}, intent, source="web")
    names = {t.name for t in (resolved or [])}
    assert "web_search" in names
