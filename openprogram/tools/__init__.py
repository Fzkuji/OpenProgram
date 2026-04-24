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

from importlib import import_module
from typing import Any

from ._helpers import is_available as _is_available

# Use builtin list/dict/etc. by aliasing them before importing the
# ``list`` submodule — otherwise ``list(...)`` below would call the
# module. Same concern isn't there for other submodule names.
_builtin_list = list


def _load_attr(module_name: str, attr_name: str):
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


ALL_TOOLS: dict[str, dict[str, Any]] = {}

for _module_name, _tool_name, _attr_name in [
    (".bash", "bash", "TOOL"),
    (".browser", "browser", "TOOL"),
    (".canvas", "canvas", "TOOL"),
    (".clarify", "clarify", "TOOL"),
    (".cron", "cron", "TOOL"),
    (".edit", "edit", "TOOL"),
    (".execute_code", "execute_code", "TOOL"),
    (".glob", "glob", "TOOL"),
    (".grep", "grep", "TOOL"),
    (".image_analyze", "image_analyze", "TOOL"),
    (".image_generate", "image_generate", "TOOL"),
    (".list", "list", "TOOL"),
    (".memory", "memory", "TOOL"),
    (".mixture_of_agents", "mixture_of_agents", "TOOL"),
    (".pdf", "pdf", "TOOL"),
    (".read", "read", "TOOL"),
    (".spawn_program", "spawn_program", "TOOL"),
    (".web_fetch", "web_fetch", "TOOL"),
    (".web_search", "web_search", "TOOL"),
    (".write", "write", "TOOL"),
    (".apply_patch", "apply_patch", "TOOL"),
    (".process", "process", "TOOL"),
    (".todo", "todo_read", "READ_TOOL"),
    (".todo", "todo_write", "WRITE_TOOL"),
]:
    try:
        ALL_TOOLS[_tool_name] = _load_attr(_module_name, _attr_name)
    except ModuleNotFoundError:
        continue

BASH = ALL_TOOLS.get("bash")
BROWSER = ALL_TOOLS.get("browser")
CANVAS = ALL_TOOLS.get("canvas")
CLARIFY = ALL_TOOLS.get("clarify")
CRON = ALL_TOOLS.get("cron")
EDIT = ALL_TOOLS.get("edit")
EXECUTE_CODE = ALL_TOOLS.get("execute_code")
GLOB = ALL_TOOLS.get("glob")
GREP = ALL_TOOLS.get("grep")
IMAGE_ANALYZE = ALL_TOOLS.get("image_analyze")
IMAGE_GENERATE = ALL_TOOLS.get("image_generate")
LIST = ALL_TOOLS.get("list")
MEMORY = ALL_TOOLS.get("memory")
MIXTURE_OF_AGENTS = ALL_TOOLS.get("mixture_of_agents")
PDF = ALL_TOOLS.get("pdf")
READ = ALL_TOOLS.get("read")
SPAWN_PROGRAM = ALL_TOOLS.get("spawn_program")
WEB_FETCH = ALL_TOOLS.get("web_fetch")
WEB_SEARCH = ALL_TOOLS.get("web_search")
WRITE = ALL_TOOLS.get("write")

APPLY_PATCH = ALL_TOOLS.get("apply_patch")
PROCESS = ALL_TOOLS.get("process")
TODO_READ = ALL_TOOLS.get("todo_read")
TODO_WRITE = ALL_TOOLS.get("todo_write")

# Default tool set (à la Claude Code): dedicated file ops for safe common
# cases + bash as the escape hatch + search + multi-file patch + todos.
# Omit `process` by default — long-running background sessions are opt-in.
DEFAULT_TOOLS: list[str] = [
    name for name in [
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
    ] if name in ALL_TOOLS
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
    "research": _builtin_list(DEFAULT_TOOLS) + [
        "web_fetch", "web_search", "image_generate", "image_analyze",
        "pdf", "spawn_program", "memory", "clarify", "execute_code",
        "mixture_of_agents", "canvas", "cron",
    ],
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
    "WEB_FETCH",
    "WEB_SEARCH",
    "IMAGE_GENERATE",
    "IMAGE_ANALYZE",
    "PDF",
    "SPAWN_PROGRAM",
    "MEMORY",
    "CLARIFY",
    "EXECUTE_CODE",
    "MIXTURE_OF_AGENTS",
    "CANVAS",
    "CRON",
    "get",
    "get_many",
    "list_available",
    "register_tool",
]
