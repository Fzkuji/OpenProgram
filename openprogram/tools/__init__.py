"""Tool registry.

Tools live under ``openprogram/tools/<name>/``. There are two formats:

1. New ``@tool``-decorated tools (preferred). Author writes a typed
   Python function with a Google-style docstring and decorates it.
   Schema, char cap, persist-to-disk, error wrap, sync→async are all
   handled by ``openprogram.tools._runtime``. The tool auto-registers
   into the AgentTool registry on module import.

2. Legacy dict tools, exporting:
       TOOL = {
           "spec": {"name", "description", "parameters"},
           "execute": callable | async callable,
       }
   These get auto-wrapped via ``wrap_legacy_tool`` when this module
   loads, so they end up in the same AgentTool registry.

Optional metadata keys on the TOOL dict (legacy format):

    "check_fn":             () -> bool      gate availability at runtime
    "requires_env":         list[str]       env vars required for the tool
                                             to function (e.g. API keys);
                                             `is_available` returns False
                                             when any are missing
    "max_result_size_chars": int             advisory truncation budget

Both formats coexist during the migration. New code should consume
``agent_tools()`` (returns ``list[AgentTool]``) for the dispatcher
path; ``ALL_TOOLS`` and ``get_many`` remain for the legacy
``runtime.exec(tools=[...])`` path until those callers are migrated.
"""

from __future__ import annotations

from typing import Any

from ._helpers import is_available as _is_available
from ._runtime import (
    AgentTool,
    ToolReturn,
    all_tools as _all_agent_tools,
    filter_for as _filter_agent_tools,
    get as _get_agent_tool,
    register as _register_agent_tool,
    tool,
    tool_requires_approval,
    wrap_legacy_tool as _wrap_legacy_tool,
)

from .agent_browser import TOOL as AGENT_BROWSER
from .apply_patch import TOOL as APPLY_PATCH
from .bash import TOOL as BASH
from .browser import TOOL as BROWSER
from .canvas import TOOL as CANVAS
from .clarify import TOOL as CLARIFY
from .cron import TOOL as CRON
from .edit import TOOL as EDIT
from .execute_code import TOOL as EXECUTE_CODE
from .glob import TOOL as GLOB
from .grep import TOOL as GREP
from .image_analyze import TOOL as IMAGE_ANALYZE
from .image_generate import TOOL as IMAGE_GENERATE
from .list import TOOL as LIST
from .memory import TOOL as MEMORY
from .mixture_of_agents import TOOL as MIXTURE_OF_AGENTS
from .pdf import TOOL as PDF
from .process import TOOL as PROCESS
from .read import TOOL as READ
from .spawn_program import TOOL as SPAWN_PROGRAM
from .todo import READ_TOOL as TODO_READ, WRITE_TOOL as TODO_WRITE
from .web_fetch import TOOL as WEB_FETCH
from .web_search import TOOL as WEB_SEARCH
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
    "web_fetch": WEB_FETCH,
    "web_search": WEB_SEARCH,
    "image_generate": IMAGE_GENERATE,
    "image_analyze": IMAGE_ANALYZE,
    "pdf": PDF,
    "spawn_program": SPAWN_PROGRAM,
    "memory": MEMORY,
    "clarify": CLARIFY,
    "execute_code": EXECUTE_CODE,
    "mixture_of_agents": MIXTURE_OF_AGENTS,
    "canvas": CANVAS,
    "cron": CRON,
    "playwright_browser": BROWSER,
    "agent_browser": AGENT_BROWSER,
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

# Per-name metadata used when auto-wrapping legacy dict tools into
# AgentTool. Migrated tools (bash/read/write/edit/list/glob/grep)
# already declared their own toolset/unsafe_in/persist_full via
# ``@tool(...)``, so they're absent from this map.
_LEGACY_TOOL_META: dict[str, dict[str, Any]] = {
    "apply_patch":        {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
    "process":            {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
    "todo_read":          {"toolsets": ["core"]},
    "todo_write":         {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
    "web_fetch":          {"toolsets": ["core", "research"], "max_result_chars": 30_000, "persist_full": True},
    "web_search":         {"toolsets": ["core", "research"]},
    "image_generate":     {"toolsets": ["core"]},
    "image_analyze":      {"toolsets": ["core", "research"]},
    "pdf":                {"toolsets": ["research"], "max_result_chars": 50_000, "persist_full": True},
    "spawn_program":      {"toolsets": ["core"]},
    "memory":             {"toolsets": ["core"]},
    "clarify":            {"toolsets": ["core"]},
    "execute_code":       {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
    "mixture_of_agents":  {"toolsets": ["research"]},
    "canvas":             {"toolsets": ["core"]},
    "cron":               {"toolsets": ["core"]},
    "playwright_browser": {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
    "agent_browser":      {"toolsets": ["core"], "unsafe_in": ["wechat", "telegram"]},
}


def _autoload_agent_registry() -> None:
    """Wrap every legacy-format tool in ALL_TOOLS so they appear in
    the AgentTool registry alongside @tool-decorated tools.

    Migrated tools are already registered (their @tool side-effects
    fired during the import block above). ``wrap_legacy_tool`` itself
    short-circuits when the name already exists, so this loop is a
    no-op for already-migrated names.
    """
    for name, record in ALL_TOOLS.items():
        if _get_agent_tool(name) is not None:
            continue
        meta = _LEGACY_TOOL_META.get(name, {})
        try:
            _wrap_legacy_tool(record, **meta)
        except Exception:
            # Don't crash the registry import if one tool's spec is
            # malformed — surface the issue when the tool is selected.
            pass


_autoload_agent_registry()

# Named toolset presets. Pass the name to ``get_many(toolset=...)`` instead
# of curating a list inline. The preset machinery stays here for future
# role-based curation, but for now we treat every tool as generic — no
# categorization by agent type. Only the two non-curated extremes are
# kept as named entries:
#
# "default" — matches DEFAULT_TOOLS (kept in sync below).
# "full"    — every registered tool. Mostly for debugging / listing.
TOOLSETS: dict[str, list[str]] = {
    "default": DEFAULT_TOOLS,
    "full": [*ALL_TOOLS],
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
    """Return the names of every registered tool whose gating passes AND
    which the user hasn't disabled via ``openprogram config tools``.

    Disabled-list is stored under ``tools.disabled`` in
    ``~/.agentic/config.json`` and read lazily so the tools module
    stays free of webui/FastAPI imports at registry build time.
    """
    disabled: set[str] = set()
    try:
        from openprogram.setup import read_disabled_tools
        disabled = read_disabled_tools()
    except Exception:
        pass
    return [
        name for name, tool in ALL_TOOLS.items()
        if _is_available(tool) and name not in disabled
    ]


def register_tool(name: str, tool: dict[str, Any], *, toolsets: list[str] | None = None) -> None:
    """Register a tool at runtime and optionally add it to named presets.

    Used by tools that get added after the initial import (e.g. third-party
    extensions). Idempotent — re-registering the same name overwrites the
    previous entry. Updates ``TOOLSETS["full"]`` automatically. Also
    auto-wraps the dict into an AgentTool so it shows up in the
    chat-side registry.
    """
    ALL_TOOLS[name] = tool
    if name not in TOOLSETS["full"]:
        TOOLSETS["full"].append(name)
    for preset in toolsets or []:
        bucket = TOOLSETS.setdefault(preset, [])
        if name not in bucket:
            bucket.append(name)
    # Mirror into AgentTool registry for new dispatcher consumers.
    try:
        _wrap_legacy_tool(tool, toolsets=toolsets or [])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AgentTool-side API — preferred for dispatcher / agent_loop callers
# ---------------------------------------------------------------------------

def agent_tools(
    names: list[str] | None = None,
    *,
    toolset: str | None = None,
    source: str | None = None,
    only_available: bool = False,
) -> list[AgentTool]:
    """Return AgentTool instances. Mirrors ``get_many`` semantics but
    returns the unified AgentTool shape consumed by ``agent_loop``.

    - ``source`` (e.g. "wechat") drops tools flagged unsafe_in that channel.
    - ``only_available`` reuses the legacy ``check_fn`` / ``requires_env``
      gating so the LLM doesn't see tools without their API keys.
    """
    if names is not None and toolset is not None:
        raise ValueError("Pass either `names` or `toolset`, not both.")
    if names is None and toolset is None:
        names = DEFAULT_TOOLS
    picked = _filter_agent_tools(names=names, toolset=toolset, source=source)
    if only_available:
        # Cross-reference with the legacy dict for gating signals
        gated = []
        for t in picked:
            record = ALL_TOOLS.get(t.name)
            if record is None or _is_available(record):
                gated.append(t)
        picked = gated
    return picked


def get_agent_tool(name: str) -> AgentTool | None:
    """Look up a single AgentTool by name from the unified registry."""
    return _get_agent_tool(name)


def list_registered_agent_tools() -> list[str]:
    """Names of every tool present in the AgentTool registry."""
    return [t.name for t in _all_agent_tools()]


__all__ = [
    "ALL_TOOLS",
    "DEFAULT_TOOLS",
    "TOOLSETS",
    "AgentTool",
    "ToolReturn",
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
    "agent_tools",
    "get",
    "get_agent_tool",
    "get_many",
    "list_available",
    "list_registered_agent_tools",
    "register_tool",
    "tool",
    "tool_requires_approval",
]
