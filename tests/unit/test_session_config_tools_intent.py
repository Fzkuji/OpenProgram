"""Tool intent (not snapshot) — session_config round-trips intent and heals
legacy snapshots, and the dispatcher expander honours the web_search overlay.

This is the regression suite for the "old sessions can't see newly-added
tools" bug. Design: docs/design/runtime/tool-toggle-management.md.
"""
from __future__ import annotations

from openprogram.agent.session_config import (
    SessionRunConfig,
    tools_override_from_config,
    _heal_snapshot,
)
from openprogram.functions import DEFAULT_TOOLS, agent_tools


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


# ── legacy snapshot healing ──

def test_heal_default_tools_snapshot():
    # A snapshot equal to DEFAULT_TOOLS heals back to {enabled: True}.
    assert _heal_snapshot(list(DEFAULT_TOOLS)) == {"enabled": True}


def test_heal_default_tools_with_web_search():
    # ±web_search is tolerated; web_search overlay handled separately.
    healed = _heal_snapshot([*DEFAULT_TOOLS, "web_search"])
    assert healed == {"enabled": True}


def test_legacy_default_snapshot_heals_via_config():
    # The whole path: a session whose tools_override is a DEFAULT_TOOLS
    # snapshot now yields a live intent, not the frozen list.
    cfg = SessionRunConfig(tools_enabled=True, tools_override=list(DEFAULT_TOOLS))
    out = tools_override_from_config(cfg)
    assert out == {"enabled": True}


def test_genuine_user_selection_kept():
    # A list that matches NO known preset is a real selection → kept as-is.
    picks = ["read", "write"]  # not equal to DEFAULT_TOOLS or any toolset
    cfg = SessionRunConfig(tools_enabled=True, tools_override=picks)
    out = tools_override_from_config(cfg)
    assert out == picks  # not healed, not dropped


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
