from __future__ import annotations

import asyncio

import pytest

from openprogram.programs import (
    DEFERRED_DEFAULT_TOOLS,
    RESIDENT_TOOLS,
    apply_default_deferral,
    agent_tools,
    deferred_catalog_text,
    freeze_turn_tools,
    install_loaded_deferred,
    release_turn_tools,
    split_tools_for_dispatch,
    tool_search,
)


@pytest.fixture(autouse=True)
def _no_frozen_turn():
    """Every test starts outside a turn, so the split reads the live loaded
    set unless the test freezes on purpose."""
    from openprogram.programs._runtime import _allowed_tool_names

    allowed_token = _allowed_tool_names.set(None)
    release_turn_tools()
    yield
    release_turn_tools()
    _allowed_tool_names.reset(allowed_token)


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
# These are in DEFAULT_TOOLS (the model may call them any time) but
# their schemas stay out of the per-turn request. The three properties
# below are exactly what makes that safe: absent from the provider array,
# represented by the bounded catalog notice, loadable via tool_search.


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
    # The prompt intentionally keeps only a bounded count and search guidance;
    # names and schemas are returned by tool_search on demand.
    text = deferred_catalog_text(catalog)
    assert text.startswith(f"{len(catalog)} deferred tools are available")
    assert "Their names and schemas are not loaded" in text
    assert "tool_search" in text
    assert not any(name in text for name in DEFERRED_DEFAULT_TOOLS)


def test_default_web_prompt_advertises_deferred_memory_tools(monkeypatch):
    """Ordinary chats can discover memory without paying for its schemas."""
    import openprogram.setup as setup
    from openprogram.context.components import build_system_prompt

    monkeypatch.setattr(setup, "_read_config", lambda: {})
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(source="web", only_available=True)
    provider_tools, catalog = split_tools_for_dispatch(tools)
    provider_names = {tool.name for tool in provider_tools}
    catalog_names = {name for name, _ in catalog}
    expected = {"memory_search", "memory_update", "memory_status"}

    assert expected <= {tool.name for tool in tools}
    assert not (expected & provider_names)
    assert expected <= catalog_names

    prompt = build_system_prompt(None, tools=tools)
    for name in expected:
        assert name in prompt


def test_disabled_memory_backend_removes_default_memory_tools(monkeypatch):
    import openprogram.setup as setup

    monkeypatch.setattr(
        setup, "_read_config", lambda: {"memory": {"backend": "none"}},
    )

    names = {
        tool.name for tool in agent_tools(source="web", only_available=True)
    }
    assert not {name for name in names if name.startswith("memory_")}


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


# --- Turn-boundary freeze ---------------------------------------------------
#
# The tools array is the root of both providers' cached prefix. Growing it
# the moment tool_search runs invalidates that prefix for the rest of the
# turn, so the array is pinned at the turn boundary instead.


def _load(name: str) -> None:
    asyncio.run(tool_search.execute(
        "call-1", {"select": f"select:{name}"}, None, None,
    ))


def test_tools_array_frozen_within_a_turn():
    """tool_search mid-turn must NOT change the provider array."""
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(toolset="full")

    freeze_turn_tools(list(tools))  # turn boundary
    before, cat_before = split_tools_for_dispatch(list(tools))

    _load("playwright_browser")

    after, cat_after = split_tools_for_dispatch(list(tools))
    assert [t.name for t in after] == [t.name for t in before], (
        "tools array changed mid-turn — cache prefix invalidated"
    )
    # The system-prompt catalog is part of the same prefix, so it holds too.
    assert cat_after == cat_before
    install_loaded_deferred()


def test_frozen_tool_enters_array_on_the_next_turn():
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    name = "playwright_browser"

    freeze_turn_tools(list(tools))
    _load(name)
    assert name not in {t.name for t in split_tools_for_dispatch(list(tools))[0]}

    freeze_turn_tools(list(tools))  # next turn boundary
    after, catalog = split_tools_for_dispatch(list(tools))
    assert name in {t.name for t in after}, "not promoted on the next turn"
    assert name not in {n for n, _ in catalog}, "still advertised as unloaded"
    install_loaded_deferred()


def test_tool_search_returns_the_schema_for_same_turn_use():
    """Availability must not wait for the array: the result text carries the
    full schema so the model can construct the call this turn."""
    install_loaded_deferred()
    apply_default_deferral()
    from openprogram.programs._runtime import _tool_search_impl

    text = _tool_search_impl("select:playwright_browser")
    assert "playwright_browser" in text
    # The parameter schema itself, not just the name + description.
    assert '"parameters"' in text
    assert '"properties"' in text
    # And it says the tool is callable right now.
    assert "THIS turn" in text
    assert "next turn" not in text
    install_loaded_deferred()


def test_tool_search_already_loaded_keyword_points_to_direct_call():
    install_loaded_deferred()
    apply_default_deferral()
    from openprogram.programs._runtime import _tool_search_impl

    first = _tool_search_impl("select:playwright_browser")
    assert "Loaded 1 deferred tool" in first
    again = _tool_search_impl("playwright_browser")
    assert "already loaded in this turn's tool list" in again
    assert "call it directly" in again
    assert "No deferred tools matched" not in again
    install_loaded_deferred()


def test_tool_search_true_miss_still_says_no_match():
    install_loaded_deferred()
    apply_default_deferral()
    from openprogram.programs._runtime import _tool_search_impl

    text = _tool_search_impl("zzzz_no_such_deferred_tool_qqq")
    assert text.startswith("No deferred tools matched query")
    install_loaded_deferred()


def test_frozen_but_unlisted_tool_still_dispatches():
    """A tool loaded mid-turn is absent from the provider array yet must
    still route — the dispatcher resolves by name against the full tool
    list, which is what agent_loop._execute_tool_calls does."""
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    name = "playwright_browser"

    freeze_turn_tools(list(tools))
    _load(name)

    provider_array = {t.name for t in split_tools_for_dispatch(list(tools))[0]}
    assert name not in provider_array, "precondition: not in the array yet"

    # Same lookup agent_loop._execute_tool_calls performs.
    resolved = next((t for t in tools if t.name == name), None)
    assert resolved is not None, "loaded tool must still be dispatchable"
    assert callable(resolved.execute)
    install_loaded_deferred()


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
