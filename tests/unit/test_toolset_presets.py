"""Named presets must name tools that exist.

Resolution drops a name the registry does not know, silently — a preset is a
wish list, not a declaration. So a tool that is renamed leaves every preset
that listed it quietly handing out less than it says, with nothing to notice
it: ``toolset="memory"`` spent a while offering three of the six memory
tools because it still listed the names of a previous memory layer.
"""
from __future__ import annotations

from openprogram.functions import TOOLSETS, _all_agent_tools, agent_tools


def test_every_preset_names_a_registered_tool():
    registered = {tool.name for tool in _all_agent_tools()}
    stale = {
        preset: sorted(
            name for name in (entry.get("tools") or [])
            if name not in registered
        )
        for preset, entry in TOOLSETS.items()
    }
    assert not {preset: names for preset, names in stale.items() if names}


def test_the_memory_preset_offers_every_memory_tool():
    from openprogram.functions.tools.memory import MEMORY_TOOL_NAMES

    # The preset names these; this is about the names surviving resolution,
    # which is the step that used to drop three of the six on the floor.
    offered = {tool.name for tool in agent_tools(toolset="memory")}

    assert set(MEMORY_TOOL_NAMES) <= offered, sorted(
        set(MEMORY_TOOL_NAMES) - offered
    )
