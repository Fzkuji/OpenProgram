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
from openprogram.programs import DEFAULT_TOOLS


# ── tools_override_from_config: intent in → intent out ──

def test_enabled_true_yields_dict_intent():
    out = tools_override_from_config(SessionRunConfig(tools_enabled=True))
    assert out == {"inherit": True}
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
    from openprogram.agent.internals._model_tools import resolve_tools
    intent = tools_override_from_config(SessionRunConfig(tools_enabled=True))
    resolved = resolve_tools({}, intent, source="web")
    names = {t.name for t in (resolved or [])}
    # the collaboration tools added to DEFAULT_TOOLS must be present
    assert "send_message" in names
    assert "list_agents" in names


def test_web_search_overlay_adds_the_tool():
    """web_search intent → web_search actually appears in the expanded set
    (the 改C requirement: overlay or it'd be silently missing)."""
    from openprogram.agent.internals._model_tools import resolve_tools
    intent = tools_override_from_config(
        SessionRunConfig(tools_enabled=True, web_search=True))
    resolved = resolve_tools({}, intent, source="web")
    names = {t.name for t in (resolved or [])}
    assert "web_search" in names


def test_search_off_removes_tool_from_automatic_and_explicit_selections():
    from openprogram.agent.internals._model_tools import resolve_tools
    for cfg in (
        SessionRunConfig(tools_enabled=True, web_search=False),
        SessionRunConfig(web_search=False),
        SessionRunConfig(tools_enabled=True, tools_override=['web_search', 'read'], web_search=False),
    ):
        tools = resolve_tools({}, tools_override_from_config(cfg), source='web')
        assert 'web_search' not in {t.name for t in tools or []}


def test_search_on_respects_agent_disabled_policy():
    from openprogram.agent.internals._model_tools import resolve_tools
    configs = [SessionRunConfig(tools_enabled=True, web_search=True),
               SessionRunConfig(tools_enabled=True, tools_override=['web_search'], web_search=True),
               SessionRunConfig(tools_enabled=True, tools_override={'preset': 'full'}, web_search=True)]
    for profile in ({'tools': {'mode': 'none'}}, {'tools': {'mode': 'automatic', 'disabled': ['web_*']}}):
        for cfg in configs:
            tools = resolve_tools(profile, tools_override_from_config(cfg), source='web')
            assert 'web_search' not in {t.name for t in tools or []}


def test_explicit_search_reaches_cold_provider_request_without_loader():
    from openprogram.agent.internals._model_tools import resolve_tools
    from openprogram.programs import (
        agent_tools, apply_default_deferral, freeze_turn_tools,
        install_loaded_deferred, release_turn_tools, split_tools_for_dispatch,
    )
    apply_default_deferral()
    shared = next(t for t in agent_tools(names=["web_search"]) if t.name == "web_search")
    assert shared._defer
    install_loaded_deferred()
    freeze_turn_tools([])
    try:
        for cfg in (
            SessionRunConfig(tools_enabled=True, tools_override=["web_search"], web_search=True),
            SessionRunConfig(tools_enabled=True, web_search=True),
            SessionRunConfig(tools_override=["web_search"]),
        ):
            resolved = resolve_tools({}, tools_override_from_config(cfg), source="web")
            provider, catalog = split_tools_for_dispatch(resolved or [])
            assert "web_search" in {t.name for t in provider}
            assert "web_search" not in {name for name, _ in catalog}
            assert shared._defer, "Per-chat selection must not mutate the global registry"
    finally:
        release_turn_tools()
