from __future__ import annotations

import asyncio

from openprogram.functions import (
    DEFERRED_DEFAULT_TOOLS,
    RESIDENT_TOOLS,
    apply_default_deferral,
    agent_tools,
    deferred_catalog_text,
    install_loaded_deferred,
    split_tools_for_dispatch,
    tool_search,
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


# --- DEFERRED_DEFAULT_TOOLS: available but not resident ---------------------
#
# These four are in DEFAULT_TOOLS (the model may call them any time) but
# their schemas stay out of the per-turn request. The three properties
# below are exactly what makes that safe: absent from the provider array,
# discoverable in the catalog, loadable via tool_search.


def test_deferred_defaults_absent_from_resident_schema():
    install_loaded_deferred()  # fresh session — nothing loaded yet
    apply_default_deferral()
    provider_tools, _ = split_tools_for_dispatch(agent_tools(toolset="full"))
    resident = {t.name for t in provider_tools}
    assert not (DEFERRED_DEFAULT_TOOLS & resident), (
        f"deferred tools leaked into the resident schema: "
        f"{sorted(DEFERRED_DEFAULT_TOOLS & resident)}"
    )
    # …and they are genuinely NOT in RESIDENT_TOOLS either.
    assert not (DEFERRED_DEFAULT_TOOLS & RESIDENT_TOOLS)


def test_deferred_defaults_appear_in_catalog_line():
    install_loaded_deferred()
    apply_default_deferral()
    _, catalog = split_tools_for_dispatch(agent_tools(toolset="full"))
    catalog_names = {n for n, _ in catalog}
    missing = DEFERRED_DEFAULT_TOOLS - catalog_names
    assert not missing, f"deferred tools missing from catalog: {sorted(missing)}"
    # The rendered catalog names each one, so the model can discover it.
    text = deferred_catalog_text(catalog)
    for name in DEFERRED_DEFAULT_TOOLS:
        assert name in text, name


def test_tool_search_loads_a_deferred_default():
    """After tool_search, the tool moves into the provider array."""
    install_loaded_deferred()
    apply_default_deferral()
    name = "playwright_browser"
    before, _ = split_tools_for_dispatch(agent_tools(toolset="full"))
    assert name not in {t.name for t in before}

    asyncio.run(tool_search.execute(
        "call-1", {"select": f"select:{name}"}, None, None,
    ))

    after, catalog = split_tools_for_dispatch(agent_tools(toolset="full"))
    assert name in {t.name for t in after}, "tool_search did not load the schema"
    assert name not in {n for n, _ in catalog}, "still listed as unloaded"
    install_loaded_deferred()  # reset so test order can't leak state


def test_deferred_catalog_component_reflects_deferral():
    """§7 assembler — the catalog the model actually sees in the prompt.

    Goes through ``build_system_prompt`` (not the raw component) so this
    also proves the ``deferred_catalog`` component is still registered and
    reached during assembly.
    """
    from openprogram.context.components import build_system_prompt

    install_loaded_deferred()
    apply_default_deferral()
    prompt = build_system_prompt(None, tools=agent_tools(toolset="full"))
    for name in DEFERRED_DEFAULT_TOOLS:
        assert name in prompt, f"{name} missing from assembled system prompt"
