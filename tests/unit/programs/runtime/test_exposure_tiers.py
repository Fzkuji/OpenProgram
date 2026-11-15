"""Every registered entry carries one exposure tier (program-model.md §5)."""
from __future__ import annotations

import copy

import openprogram.programs as programs
from openprogram.programs._host.runtime import (
    Tier, _open_applications, all_tools, as_resident, install_open_applications, owner_kind,
    split_tools_for_dispatch, tool_owner, tool_tier,
)


def test_every_registered_entry_has_a_tier():
    for tool in all_tools():
        assert isinstance(tool._tier, Tier), tool.name
        assert tool._defer is (tool._tier is Tier.DEFERRED), tool.name


def test_application_entries_are_deferred_and_tools_follow_the_resident_set():
    for tool in all_tools():
        if getattr(tool, "_is_agent_method", False):
            continue  # an Agent method states its own ``defer``
        kind = owner_kind(tool_owner(tool))
        if kind == "application":
            assert tool_tier(tool) is Tier.DEFERRED, tool.name
        elif tool_tier(tool) is not Tier.HIDDEN:
            expected = Tier.RESIDENT if tool.name in programs.RESIDENT_TOOLS | {"tool_search"} else Tier.DEFERRED
            assert tool_tier(tool) is expected, tool.name


def test_owner_kinds():
    assert owner_kind("") == owner_kind("tool:goal") == "tool"
    assert owner_kind("plugin:x") == "plugin"
    for owner in ("application:browser", "external:some_harness", "weekly_report"):
        assert owner_kind(owner) == "application"


def _deferred_tool():
    return next(tool for tool in all_tools() if tool_tier(tool) is Tier.DEFERRED)


def _application_entry(application: str):
    """A deferred entry as an application's package registers it."""
    entry = copy.copy(_deferred_tool())
    entry._program_owner = f"application:{application}"
    assert tool_tier(entry) is Tier.DEFERRED
    return entry


def test_opening_an_application_loads_none_of_its_entries():
    """Opening an application leaves its entries deferred, so the tools
    array is the same bytes before and after (deferred-tools A4); its
    catalog line says it is open and how to load them."""
    entry = _application_entry("notes")
    fetch = programs.get_agent_tool("web_fetch")
    assert split_tools_for_dispatch([entry, fetch], ())[0] == []
    token = install_open_applications({"notes"})
    try:
        assert split_tools_for_dispatch([entry, fetch], ())[0] == []
    finally:
        _open_applications.reset(token)
    line = programs.deferred_catalog([entry], {"notes"})["notes"]
    assert line.startswith("Open in a window.") and f"tool_search select:{entry.name}" in line


def test_a_resident_copy_leaves_the_registered_tool_deferred():
    registered = _deferred_tool()
    resident = as_resident(registered)
    assert resident is not registered and tool_tier(resident) is Tier.RESIDENT and not resident._defer
    assert tool_tier(registered) is Tier.DEFERRED


def test_programs_list_shows_tiers_and_builtin_settings():
    from openprogram.interfaces.cli.commands.programs import program_rows
    rows = {(r["kind"], r["name"]): r for r in program_rows()}
    assert rows[("tool", "bash")]["tiers"] == {"bash": "resident"}
    browser = rows[("application", "browser")]
    assert browser["tiers"] == {} and browser["enabled"] is True
    assert browser["note"] == "built in; registers no tools"
