"""Tool registry.

Each tool lives under ``openprogram/tools/<name>/`` with at minimum:

    <name>/__init__.py exporting
        TOOL = {
            "spec": {"name", "description", "parameters"},
            "execute": callable | async callable,
        }

Optional metadata keys on the TOOL dict (all have safe defaults):

    "check_fn":             () -> bool      gate availability at runtime
    "requires_env":         list[str]       env vars required for the tool
                                             to function (e.g. API keys);
                                             `is_available` returns False
                                             when any are missing
    "max_result_size_chars": int             advisory truncation budget

Registration stays lazy — import only the tools you pass to
``runtime.exec(..., tools=...)`` or pick via ``get_many(toolset=...)``.
"""

from __future__ import annotations

from typing import Any

from ._helpers import is_available as _is_available

# Use builtin list/dict/etc. by aliasing them before importing the
# ``list`` submodule — otherwise ``list(...)`` below would call the
# module. Same concern isn't there for other submodule names.
_builtin_list = list

from .apply_patch import TOOL as APPLY_PATCH
from .bash import TOOL as BASH
from .edit import TOOL as EDIT
from .glob import TOOL as GLOB
from .grep import TOOL as GREP
from .list import TOOL as LIST
from .process import TOOL as PROCESS
from .read import TOOL as READ
from .todo import READ_TOOL as TODO_READ, WRITE_TOOL as TODO_WRITE
from .write import TOOL as WRITE


ALL_TOOLS: dict[str, dict[str, Any]] = {
    "bash": BASH,
    "read": READ,
    "write": WRITE,
    "edit": EDIT,
    "glob": GLOB,
    "grep": GREP,
    "list": LIST,
    "apply_patch": APPLY_PATCH,
    "process": PROCESS,
    "todo_read": TODO_READ,
    "todo_write": TODO_WRITE,
}

# Default tool set (à la Claude Code): dedicated file ops for safe common
# cases + bash as the escape hatch + search + multi-file patch + todos.
# Omit `process` by default — long-running background sessions are opt-in.
DEFAULT_TOOLS: list[str] = [
    "bash",
    "read",
    "write",
    "edit",
    "apply_patch",
    "glob",
    "grep",
    "list",
    "todo_read",
    "todo_write",
]

# Named toolset presets. Pass the name to ``get_many(toolset=...)`` instead
# of curating a list inline. New tools added by later steps slot into these
# presets so callers don't have to edit every entry point.
#
# "default" — matches DEFAULT_TOOLS (kept in sync below).
# "research" — default + web/pdf/memory/image when landed.
# "full"    — every registered tool. Mostly for debugging / listing.
TOOLSETS: dict[str, list[str]] = {
    "default": DEFAULT_TOOLS,
    "research": _builtin_list(DEFAULT_TOOLS),  # filled in by later steps
    "full": _builtin_list(ALL_TOOLS.keys()),
}


def get(name: str) -> dict[str, Any]:
    """Look up a tool record by name. Raises KeyError if not registered."""
    return ALL_TOOLS[name]


def get_many(
    names: list[str] | None = None,
    *,
    toolset: str | None = None,
    only_available: bool = False,
) -> list[dict[str, Any]]:
    """Look up several tools.

    - Pass ``names`` for an explicit list.
    - Pass ``toolset="research"`` (etc) to use a named preset.
    - Pass nothing to get DEFAULT_TOOLS.
    - Set ``only_available=True`` to drop tools whose ``check_fn`` /
      ``requires_env`` gating says they can't run right now (e.g. missing
      API keys) — useful so the model doesn't see tools it can't use.
    """
    if names is not None and toolset is not None:
        raise ValueError("Pass either `names` or `toolset`, not both.")
    if toolset is not None:
        try:
            names = TOOLSETS[toolset]
        except KeyError as e:
            raise KeyError(
                f"Unknown toolset {toolset!r}. Known: {sorted(TOOLSETS)}"
            ) from e
    if names is None:
        names = DEFAULT_TOOLS
    tools = [get(n) for n in names]
    if only_available:
        tools = [t for t in tools if _is_available(t)]
    return tools


def list_available() -> list[str]:
    """Return the names of every registered tool whose gating currently passes."""
    return [name for name, tool in ALL_TOOLS.items() if _is_available(tool)]


def register_tool(name: str, tool: dict[str, Any], *, toolsets: list[str] | None = None) -> None:
    """Register a tool at runtime and optionally add it to named presets.

    Used by tools that get added after the initial import (e.g. third-party
    extensions). Idempotent — re-registering the same name overwrites the
    previous entry. Updates ``TOOLSETS["full"]`` automatically.
    """
    ALL_TOOLS[name] = tool
    if name not in TOOLSETS["full"]:
        TOOLSETS["full"].append(name)
    for preset in toolsets or []:
        bucket = TOOLSETS.setdefault(preset, [])
        if name not in bucket:
            bucket.append(name)


__all__ = [
    "ALL_TOOLS",
    "DEFAULT_TOOLS",
    "TOOLSETS",
    "APPLY_PATCH",
    "BASH",
    "READ",
    "WRITE",
    "EDIT",
    "GLOB",
    "GREP",
    "LIST",
    "PROCESS",
    "TODO_READ",
    "TODO_WRITE",
    "get",
    "get_many",
    "list_available",
    "register_tool",
]
