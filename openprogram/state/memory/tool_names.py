"""The names of the memory tools, as one group.

The memory policy (``state.memory.policy``) decides whether a turn may read
or write memory, and everything that names the memory tools as a group
needs their names: the turn that refuses them while memory is off
(``runtime.conversation.turn_runtime.loop_runner``), the toolset preset,
the Functions page grouping, setup's switch that hides them without a
backing store. The tools themselves (``programs.tools.knowledge.memory``)
register under these names. Written once here, below all of them, so a
renamed tool cannot leave one of those lists quietly pointing at nothing.
"""
from __future__ import annotations

SEARCH_NAME = "memory_search"
GET_NAME = "memory_get"
UPDATE_NAME = "memory_update"

MEMORY_TOOL_NAMES: tuple[str, ...] = (SEARCH_NAME, GET_NAME, UPDATE_NAME)

__all__ = ["SEARCH_NAME", "GET_NAME", "UPDATE_NAME", "MEMORY_TOOL_NAMES"]
