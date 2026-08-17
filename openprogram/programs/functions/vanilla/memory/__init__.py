"""Memory functions — agent bundle for the Markdown memory workspace.

Functions exposed (all self-register via @function on import):

  memory_search — find paragraphs by meaning
  memory_grep   — find an exact string
  memory_get    — read a file, a section, or one block
  memory_browse — list what memory holds
  memory_update — correct or add one thing
  memory_promote — trust and distill one pending unpaired-group source
  memory_status — size, revision, writer health, and pending turns

Recording the conversation is not among them. That happens in the
background once enough has been said; see ``openprogram/memory``.
"""
from ...._runtime import function
from .memory import (
    SEARCH_NAME, SEARCH_SPEC, memory_search,
    GREP_NAME, GREP_SPEC, memory_grep,
    GET_NAME, GET_SPEC, memory_get,
    BROWSE_NAME, BROWSE_SPEC, memory_browse,
    UPDATE_NAME, UPDATE_SPEC, memory_update,
    PROMOTE_NAME, PROMOTE_SPEC, memory_promote,
    STATUS_NAME, STATUS_SPEC, memory_status,
)


def _register(name, spec, fn, *, max_chars=20_000):
    function(
        name=name,
        description=spec["description"],
        parameters=spec["parameters"],
        toolset=["core"],
        max_result_chars=max_chars,
    )(fn)


_register(SEARCH_NAME, SEARCH_SPEC, memory_search, max_chars=30_000)
_register(GREP_NAME, GREP_SPEC, memory_grep, max_chars=20_000)
_register(GET_NAME, GET_SPEC, memory_get, max_chars=30_000)
_register(BROWSE_NAME, BROWSE_SPEC, memory_browse, max_chars=30_000)
_register(UPDATE_NAME, UPDATE_SPEC, memory_update, max_chars=8_000)
_register(PROMOTE_NAME, PROMOTE_SPEC, memory_promote, max_chars=8_000)
_register(STATUS_NAME, STATUS_SPEC, memory_status, max_chars=8_000)


# What "the memory tools" means, for everything that has to name them as a
# group: the toolset preset, the Functions page grouping, and the switch
# that hides them when there is no backing store. Written once here so a
# renamed tool cannot leave one of those lists quietly pointing at nothing.
MEMORY_TOOL_NAMES: tuple[str, ...] = (
    SEARCH_NAME, GREP_NAME, GET_NAME, BROWSE_NAME, UPDATE_NAME, PROMOTE_NAME,
    STATUS_NAME,
)


__all__ = [
    "SEARCH_NAME", "GREP_NAME", "GET_NAME",
    "BROWSE_NAME", "UPDATE_NAME", "PROMOTE_NAME", "STATUS_NAME",
    "MEMORY_TOOL_NAMES",
]
