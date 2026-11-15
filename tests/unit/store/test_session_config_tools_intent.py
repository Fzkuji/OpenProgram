"""Tool intent (not snapshot) — the Agent configuration decides the tool
set; a session only overlays web search or a /skill's allowed-tools, and the
dispatcher expander honours both live.

Design: docs/reference/design/runtime/agent-configuration/tool-toggle-management.html.
"""
from __future__ import annotations

from openprogram.runtime.conversation.session_config import (
    SessionRunConfig,
    tools_override_from_config,
)


# ── tools_override_from_config: intent in → intent out ──

def test_no_config_follows_the_agent():
    assert tools_override_from_config(SessionRunConfig()) is None


def test_web_search_overlays_the_agent_tool_set():
    out = tools_override_from_config(SessionRunConfig(web_search=True))
    assert out == {"inherit": True, "web_search": True}


def test_skill_selection_kept_with_search_overlay():
    cfg = SessionRunConfig(tools_override=["read", "write"])
    assert tools_override_from_config(cfg) == ["read", "write"]
    cfg = SessionRunConfig(tools_override=["read"], web_search=True)
    assert tools_override_from_config(cfg) == ["read", "web_search"]


def test_web_search_overlay_adds_the_tool():
    """web_search intent → web_search actually appears in the expanded set."""
    from openprogram.runtime.conversation.model_tools import resolve_tools
    intent = tools_override_from_config(SessionRunConfig(web_search=True))
    resolved = resolve_tools({}, intent, source="web")
    assert "web_search" in {t.name for t in (resolved or [])}


def test_search_off_removes_tool_from_automatic_and_explicit_selections():
    from openprogram.runtime.conversation.model_tools import resolve_tools
    for cfg in (
        SessionRunConfig(web_search=False),
        SessionRunConfig(tools_override=['web_search', 'read'], web_search=False),
    ):
        tools = resolve_tools({}, tools_override_from_config(cfg), source='web')
        assert 'web_search' not in {t.name for t in tools or []}


def test_search_on_respects_agent_disabled_policy():
    from openprogram.runtime.conversation.model_tools import resolve_tools
    configs = [SessionRunConfig(web_search=True),
               SessionRunConfig(tools_override=['web_search'], web_search=True)]
    for profile in ({'tools': {'mode': 'none'}}, {'tools': {'mode': 'automatic', 'disabled': ['web_*']}}):
        for cfg in configs:
            tools = resolve_tools(profile, tools_override_from_config(cfg), source='web')
            assert 'web_search' not in {t.name for t in tools or []}


def test_explicit_search_reaches_cold_provider_request_without_loader():
    from openprogram.runtime.conversation.model_tools import resolve_tools
    from openprogram.programs import (
        agent_tools, apply_default_deferral, note_request_loaded, split_tools_for_dispatch,
    )
    apply_default_deferral()
    shared = next(t for t in agent_tools(names=["web_search"]) if t.name == "web_search")
    assert shared._defer
    note_request_loaded(())
    try:
        for cfg in (
            SessionRunConfig(tools_override=["web_search"], web_search=True),
            SessionRunConfig(web_search=True),
            SessionRunConfig(tools_override=["web_search"]),
        ):
            resolved = resolve_tools({}, tools_override_from_config(cfg), source="web")
            provider, catalog = split_tools_for_dispatch(resolved or [])
            assert "web_search" in {t.name for t in provider}
            assert "web_search" not in {name for name, _ in catalog}
            assert shared._defer, "Per-chat selection must not mutate the global registry"
    finally:
        note_request_loaded(())
