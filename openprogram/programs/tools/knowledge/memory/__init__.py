"""Memory functions — agent bundle for the Markdown memory workspace.

Functions exposed (all self-register via @function on import):

  memory_search — find paragraphs by meaning, or an exact string
  memory_get    — list what memory holds, read a file, section or block,
                  or read the chat message a footnote cites
  memory_update — correct or add records

Recording the conversation is not among them. That happens in the
background once enough has been said; see ``openprogram/state/memory``.
"""
import json
from functools import wraps

from openprogram.programs._host.runtime import ToolReturn, function
from .memory import (
    SEARCH_NAME, SEARCH_SPEC, memory_search,
    GET_NAME, GET_SPEC, memory_get,
    UPDATE_NAME, UPDATE_SPEC, memory_update,
)


def _flag_failures(fn):
    """Report a refused or failed memory call as a failed tool result.

    The memory functions return failures as JSON text with ``"ok": false``
    (for example ``MEMORY_ACCESS_DENIED``), which the agent loop otherwise
    records as a successful call. Only the registered tool changes; Python
    callers of the functions still get the JSON string.
    """
    @wraps(fn)
    def call(*args, **kwargs):
        result = fn(*args, **kwargs)
        if isinstance(result, str) and result.lstrip().startswith("{"):
            try:
                payload = json.loads(result)
            except ValueError:
                return result
            if isinstance(payload, dict) and payload.get("ok") is False:
                return ToolReturn(text=result, is_error=True)
        return result
    return call


def _register(name, spec, fn, *, max_chars=20_000):
    function(
        name=name,
        description=spec["description"],
        parameters=spec["parameters"],
        toolset=["core"],
        max_result_weight=max_chars // 4,  # characters of English text
        # A memory path names a file inside the workspace, or a chat
        # citation, never a filesystem path; the workspace resolves and
        # contains it itself.
        path_params={},
    )(_flag_failures(fn))


_register(SEARCH_NAME, SEARCH_SPEC, memory_search, max_chars=30_000)
_register(GET_NAME, GET_SPEC, memory_get, max_chars=30_000)
_register(UPDATE_NAME, UPDATE_SPEC, memory_update, max_chars=8_000)


# What "the memory tools" means, for everything that has to name them as a
# group: the toolset preset, the Functions page grouping, and the switch
# that hides them when there is no backing store. Written once here so a
# renamed tool cannot leave one of those lists quietly pointing at nothing.
MEMORY_TOOL_NAMES: tuple[str, ...] = (SEARCH_NAME, GET_NAME, UPDATE_NAME)


__all__ = [
    "SEARCH_NAME", "GET_NAME", "UPDATE_NAME", "MEMORY_TOOL_NAMES",
]
