from __future__ import annotations

import asyncio

import pytest

from openprogram.programs import (
    DEFERRED_DEFAULT_TOOLS,
    RESIDENT_TOOLS,
    apply_default_deferral,
    agent_tools,
    deferred_catalog_text,
    loaded_tools,
    note_request_loaded,
    split_tools_for_dispatch,
    tool_search,
)
from openprogram.models.providers.types import ToolResultMessage


@pytest.fixture(autouse=True)
def _no_loaded_tools():
    """Every test starts with no request sent, so nothing is loaded."""
    from openprogram.programs._host.runtime import _allowed_tool_names, _request_loaded

    allowed_token = _allowed_tool_names.set(None)
    loaded_token = _request_loaded.set(frozenset())
    yield
    _request_loaded.reset(loaded_token)
    _allowed_tool_names.reset(allowed_token)


def _search(name: str, call_id: str = "call-1") -> ToolResultMessage:
    """The result message of ``tool_search select:<name>``, as the loop keeps it."""
    result = asyncio.run(tool_search.execute(call_id, {"select": f"select:{name}"}, None, None))
    return ToolResultMessage(tool_call_id=call_id, tool_name="tool_search",
                             content=result.content, details=result.details, timestamp=0)


def _split(tools, messages=()):
    return split_tools_for_dispatch(list(tools), loaded_tools(messages))


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
    apply_default_deferral()
    _, catalog = split_tools_for_dispatch(agent_tools(toolset="full"))
    catalog_names = {n for n, _ in catalog}
    missing = DEFERRED_DEFAULT_TOOLS - catalog_names
    assert not missing, f"deferred tools missing from catalog: {sorted(missing)}"
    # The prompt names the deferred tools, at most DEFERRED_NAMES_SHOWN of
    # them; schemas are returned by tool_search on demand.
    from openprogram.programs._host.runtime import DEFERRED_NAMES_SHOWN
    text = deferred_catalog_text(catalog)
    assert text.startswith(f"{len(catalog)} deferred tools are available")
    assert "Only these tools need loading" in text
    assert "Every tool already in your tools list is callable as it is" in text
    assert "tool_search" in text
    shown = [name for name, _ in catalog][:DEFERRED_NAMES_SHOWN]
    assert all(name in text for name in shown)


def test_default_web_prompt_advertises_deferred_memory_tools(monkeypatch):
    """Ordinary chats can discover memory without paying for its schemas."""
    from openprogram.state import config as user_config
    from openprogram.runtime.context.prompt_assembly.components import build_system_prompt

    monkeypatch.setattr(user_config, "read_config", lambda: {})
    apply_default_deferral()
    tools = agent_tools(source="web", only_available=True)
    provider_tools, catalog = split_tools_for_dispatch(tools)
    provider_names = {tool.name for tool in provider_tools}
    catalog_names = {name for name, _ in catalog}
    expected = {"memory_search", "memory_update", "memory_get"}

    assert expected <= {tool.name for tool in tools}
    assert not (expected & provider_names)
    assert expected <= catalog_names

    prompt = build_system_prompt(None, tools=tools)
    for name in expected:
        assert name in prompt


def test_disabled_memory_backend_removes_default_memory_tools(monkeypatch):
    from openprogram.state import config as user_config

    monkeypatch.setattr(
        user_config, "read_config", lambda: {"memory": {"backend": "none"}},
    )

    names = {
        tool.name for tool in agent_tools(source="web", only_available=True)
    }
    assert not {name for name in names if name.startswith("memory_")}


def test_tool_search_loads_a_deferred_default():
    """After tool_search, the tool moves into the provider array of the
    request whose messages hold its result."""
    apply_default_deferral()
    name = "scheduler"
    tools = agent_tools(toolset="full")
    before, _ = _split(tools)
    assert name not in {t.name for t in before}

    after, catalog = _split(tools, [_search(name)])
    assert name in {t.name for t in after}, "tool_search did not load the schema"
    assert name in {n for n, _ in catalog}, "the prompt's list must not change"


# --- The loaded set is read from the request's messages ---------------------
#
# Nothing holds what is loaded: a request loads what its own messages loaded.


def test_discovery_promotes_only_the_loaded_tool():
    """Explicit discovery updates the next provider request's tool array."""
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    before, cat_before = _split(tools)
    first = _search("scheduler")
    after, cat_after = _split(tools, [first])
    assert {t.name for t in after} == {t.name for t in before} | {"scheduler"}
    assert cat_after == cat_before
    repeated, _ = _split(tools, [first, _search("scheduler", "call-2")])
    assert [t.name for t in repeated] == [t.name for t in after]


def test_a_loaded_tool_joins_the_array_at_its_end_in_load_order():
    """What was sent before a load stays as it was: loaded tools come after
    every resident tool, in the order they were first loaded."""
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    before, _ = _split(tools)
    after, _ = _split(tools, [_search("scheduler"), _search("memory_get", "call-2")])
    names = [t.name for t in after]
    assert names[:len(before)] == [t.name for t in before]
    assert names[len(before):] == ["scheduler", "memory_get"]
    swapped, _ = _split(tools, [_search("memory_get"), _search("scheduler", "call-2")])
    assert [t.name for t in swapped][len(before):] == ["memory_get", "scheduler"]


def test_a_request_without_the_search_does_not_load_the_tool():
    """A nested run, another branch, or an edit from before the search reads
    messages without the result, so the tool is not loaded there."""
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    loaded_elsewhere = _search("scheduler")
    assert "scheduler" in {t.name for t in _split(tools, [loaded_elsewhere])[0]}
    assert "scheduler" not in {t.name for t in _split(tools, [])[0]}


def test_a_failed_search_loads_nothing():
    apply_default_deferral()
    missing = _search("zzzz_no_such_tool_qqq")
    assert loaded_tools([missing]) == []


def test_loading_a_tool_leaves_the_system_prompt_unchanged():
    """The prompt is sent ahead of the conversation. A catalogue that dropped
    a tool once loaded changed it on the next turn, and the provider re-read
    the whole conversation uncached."""
    from openprogram.runtime.context.prompt_assembly.components import build_system_prompt

    apply_default_deferral()
    tools = agent_tools(toolset="full")
    before = build_system_prompt(None, tools=tools)
    assert "scheduler" in {t.name for t in _split(tools, [_search("scheduler")])[0]}
    assert build_system_prompt(None, tools=tools) == before


def test_tool_search_returns_the_schema_for_same_turn_use():
    """Availability must not wait for the array: the result text carries the
    full schema so the model can construct the call this turn."""
    apply_default_deferral()
    from openprogram.programs._host.runtime import _tool_search_impl

    text = _tool_search_impl("select:scheduler")
    assert "scheduler" in text
    # The parameter schema itself, not just the name + description.
    assert '"parameters"' in text
    assert '"properties"' in text
    # And it says the tool is callable right now.
    assert "THIS turn" in text
    assert "next turn" not in text


def test_tool_search_already_loaded_keyword_loads_it_again():
    """A tool the request already loaded is ranked and loaded like the
    others: its definition comes back (Claude Code returns the reference),
    never a 'no match'."""
    apply_default_deferral()
    from openprogram.programs._host.runtime import _tool_search_impl

    first = _tool_search_impl("select:scheduler")
    assert "Loaded 1 deferred tool" in first
    note_request_loaded(["scheduler"])  # the next request read it from its messages
    again = _tool_search_impl("scheduler")
    assert again.startswith("Loaded ")
    assert '{"name": "scheduler"' in again
    assert "No deferred tools matched" not in again


def test_tool_search_true_miss_still_says_no_match():
    apply_default_deferral()
    from openprogram.programs._host.runtime import _tool_search_impl

    # No query word appears in any tool (BM25 ranks "tool" or "deferred").
    text = _tool_search_impl("zzzzqqq xyzzyplugh")
    assert text.startswith("No deferred tools matched query")


def test_discovered_tool_keeps_the_resolved_dispatch_target():
    """Provider promotion keeps the existing resolved tool for dispatch."""
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    name = "scheduler"
    provider_array = {t.name for t in _split(tools, [_search(name)])[0]}
    assert name in provider_array, "discovered tool must be in the next request"

    # Same lookup agent_loop._execute_tool_calls performs.
    resolved = next((t for t in tools if t.name == name), None)
    assert resolved is not None, "loaded tool must still be dispatchable"
    assert callable(resolved.execute)


def test_deferred_catalog_component_reflects_deferral():
    """§7 assembler — the catalog the model actually sees in the prompt.

    Goes through ``build_system_prompt`` (not the raw component) so this
    also proves the ``deferred_catalog`` component is still registered and
    reached during assembly.
    """
    from openprogram.runtime.context.prompt_assembly.components import build_system_prompt

    apply_default_deferral()
    prompt = build_system_prompt(None, tools=agent_tools(toolset="full"))
    for name in DEFERRED_DEFAULT_TOOLS:
        assert name in prompt, f"{name} missing from assembled system prompt"


def test_a_tool_name_loads_exactly_that_tool():
    """``select:scheduler`` and a query that is the name ``scheduler`` both
    load the one tool: other tools that merely mention scheduling (``cron``,
    ``remind``) are what a keyword query is for. The argument is itself
    called ``select``, so a model asking for one tool writes either form."""
    apply_default_deferral()
    from openprogram.programs._host.runtime import _tool_search_run

    for query in ("select:scheduler", "select: scheduler", "scheduler"):
        text, loaded = _tool_search_run(query)
        assert [t.name for t in loaded] == ["scheduler"], query
        assert text.startswith("Loaded 1 deferred tool."), query
        assert "matching" not in text.splitlines()[0], query

    _text, loaded = _tool_search_run("select:scheduler,cron")
    assert [t.name for t in loaded] == ["scheduler", "cron"]


def test_a_keyword_query_still_matches_several_tools():
    apply_default_deferral()
    from openprogram.programs._host.runtime import _tool_search_run

    text, loaded = _tool_search_run("scheduler recurring")
    assert len(loaded) > 1
    assert "matching 'scheduler recurring'" in text
