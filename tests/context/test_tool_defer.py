from __future__ import annotations

from openprogram.functions import (
    RESIDENT_TOOLS,
    apply_default_deferral,
    agent_tools,
    split_tools_for_dispatch,
)


def test_resident_tools_not_deferred():
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    by = {t.name: t for t in tools}
    for name in ("bash", "read", "write", "edit", "tool_search"):
        if name in by:
            assert getattr(by[name], "_defer", False) is False, name


def test_cold_tools_deferred_and_split():
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    provider_tools, catalog = split_tools_for_dispatch(tools)
    provider_names = {t.name for t in provider_tools}
    catalog_names = {n for n, _ in catalog}
    assert "web_search" in catalog_names or "web_search" not in provider_names
    assert len(provider_names) < len(tools)


def test_apply_is_idempotent():
    apply_default_deferral()
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    by = {t.name: t for t in tools}
    if "bash" in by:
        assert getattr(by["bash"], "_defer", False) is False
