"""Function calling — registry, presets, policy.

Every function the LLM can call is decorated with ``@function`` (see
``_runtime.py``). The decorator builds an ``AgentTool`` and registers
it into a single in-process registry; this module imports each
subpackage so the side-effect registrations fire at import time, then
exposes the resolution API (presets, allow/deny/source policy chain).

Design synthesizes three external frameworks under ``references/``:

  - Claude Code: the ``AgentTool`` shape, the
    ``execute(call_id, args, cancel, on_update) -> AgentToolResult``
    contract, and the search/read collapse semantics.
  - Hermes: ``TOOLSETS`` with ``{tools, includes}`` composition,
    ``_expand_preset`` recursive walk + first-occurrence dedupe.
  - OpenClaw: per-channel ``unsafe_in`` filtering, allow/deny chain
    layered on top of the toolset (``apply_tool_policy``).

Beyond the references we add a dynamic per-call result ceiling, an
LLM-controllable timeout, a bounded streaming on_update accumulator,
and ``can_use()`` pre-flight gates — all wired through ``@function``
kwargs. See ``_runtime.py`` for the decorator and the helpers it
relies on.
"""

from __future__ import annotations

from ._helpers import (
    is_available as _is_available_legacy_dict,
    is_available_agent_tool as _is_available_agent_tool,
)
from ._runtime import (
    AgentTool,
    ToolReturn,
    all_tools as _all_agent_tools,
    deferred_catalog_text,
    filter_for as _filter_agent_tools,
    function,
    get as _get_agent_tool,
    install_loaded_deferred,
    mark_deferred_loaded,
    register as _register_agent_tool,
    split_tools_for_dispatch,
    tool_requires_approval,
    tool_search,
)

# Side-effect imports — ``tools/`` holds @function-decorated leaf tools,
# ``agentics/`` holds @agentic_function bodies. Each subpackage's
# ``__init__`` triggers the decorator side-effects so every shipped
# function lands in the shared ``_registry`` by the time the parent
# package finishes loading. The source tree split (tools/ vs agentics/)
# mirrors the semantic split (deterministic leaf vs LLM-aware
# composable); both end up in the same registry.
from . import tools as _tools_self_register  # noqa: F401
from . import agentics as _agentics_self_register  # noqa: F401

# Layer 2 — exposure whitelist lives in ``TOOLSETS["full"]["tools"]``
# (see below). The ``full`` preset *is* the universe; every other
# preset and the per-call cascade filter subsets of it. Tools in the
# registry but not in ``full`` stay internal-only (Python-direct
# callable but invisible to any LLM). No separate ``EXPOSED_TOOLS``
# constant — one name, one source of truth.


# The safe default set: file ops + shell + search + multi-file patch +
# todos. Matches Claude Code's defaults. Omits ``process`` (long-lived
# background sessions) — opt-in via toolset="coding" instead.
DEFAULT_TOOLS: list[str] = [
    "bash",
    "read",
    "write",
    "edit",
    "apply_patch",
    "glob",
    "grep",
    "list",
    "task",
    "todo_read",
    "todo_write",
    # Plan-mode gate — the LLM is allowed to enter plan mode on its
    # own when it judges the task warrants it (mirrors claude-code's
    # EnterPlanMode tool). The "plan" pseudo-channel filter in
    # ``apply_tool_policy`` hides the write tools while in plan mode,
    # but enter/exit themselves are never on any unsafe_in list — they
    # stay visible across modes.
    "enter_plan_mode",
    "exit_plan_mode",
    # MCP meta — read resources / render prompts on any configured
    # MCP server. Lightweight (one read per call), and the LLM needs
    # them on turn 1 to discover what's there; not deferred.
    "list_mcp_resources",
    "read_mcp_resource",
    "list_mcp_prompts",
    "get_mcp_prompt",
    # Agentic harness entry points — these are the user-facing
    # programs (gui_agent / research_agent / wiki_agent) that the
    # LLM should know about by default. Without them in DEFAULT_TOOLS
    # the model has no idea these exist and resorts to bash-grepping
    # the filesystem when asked to "use gui_agent" or similar.
    "gui_agent",
    "research_agent",
    "wiki_agent",
]


# Hermes-style named presets. ``default`` is the always-on minimal
# safe set above; ``full`` is the *exposure whitelist* — the universe
# of every tool name that may ever appear in any LLM's tools array.
# Every other preset (research / browser / coding / …) is a curated
# subset of ``full``. Anything in the registry but NOT in ``full``
# stays internal (Python-direct callable but never reaches the LLM)
# — that's how private @agentic_function helpers like ``_pick_stage``
# don't leak into the model's tool table.
#
# Composition: an entry can carry ``includes`` (Hermes pattern) that
# names other presets to expand. ``_expand_preset`` walks them
# recursively and dedupes, so ``debugging`` reuses ``coding`` +
# ``research`` without duplication.
TOOLSETS: dict[str, dict[str, list[str]]] = {
    "default": {
        "tools":    DEFAULT_TOOLS,
        "includes": [],
    },
    "full": {
        # Static exposure whitelist. Adding a tool to the registry
        # without adding its name here keeps it invisible to LLMs.
        # The order is "leaf @function tools first, then agentics,
        # then harnesses" purely for readability — _expand_preset
        # dedupes so order doesn't matter semantically.
        "tools":    [
            # leaf @function tools (tools/)
            "bash", "read", "write", "edit",
            "glob", "grep", "list",
            "apply_patch", "process", "execute_code",
            "task", "message_branch",
            "todo_read", "todo_write",
            "ask_user_question", "cron", "canvas",
            "spawn_program", "mixture_of_agents",
            "agent_browser", "playwright_browser",
            "web_search", "web_fetch", "pdf",
            "image_generate", "image_analyze",
            "memory_note", "memory_recall", "memory_reflect",
            "memory_get", "memory_browse", "memory_lint",
            "memory_ingest", "memory_backlinks",
            "memory_rename", "memory_relink", "memory_delete",
            "memory_review", "memory_status",
            "tool_search",  # Layer 7 bootstrap; always exposed
            "enter_plan_mode", "exit_plan_mode",
            "list_mcp_resources", "read_mcp_resource",
            "list_mcp_prompts", "get_mcp_prompt",
            "worktree_create", "worktree_merge", "worktree_discard",
            "worktree_list", "worktree_keep",

            # agentic side
            # The three harness entry points + the two PDF-extraction
            # utilities. Every other @agentic_function (the composable
            # building blocks like wait / ask_user, the third-party
            # bundled examples, the internal
            # stage helpers gui_step / observe / etc.) is registered
            # for Python-direct invocation but NOT whitelisted: the
            # LLM never sees them, only the harness entry points and
            # the PDF utilities are the LLM-callable surface.
            "extract_pdf_figures",
            "extract_pdf_tables",
            "gui_agent",
            "research_agent",
            "wiki_agent",
            "interaction_demo",
        ],
        "includes": [],
    },
    "research": {
        "tools":    ["web_search", "web_fetch", "pdf", "image_analyze"],
        "includes": ["default"],
    },
    "browser": {
        "tools":    ["playwright_browser", "agent_browser", "web_search"],
        "includes": ["default"],
    },
    "coding": {
        "tools":    ["execute_code", "process"],
        "includes": ["default"],
    },
    "vision": {
        "tools":    ["image_analyze", "image_generate", "pdf"],
        "includes": ["default"],
    },
    "memory": {
        "tools":    ["memory_note", "memory_recall", "memory_reflect",
                     "memory_get", "memory_browse", "memory_lint",
                     "memory_ingest", "memory_backlinks",
                     "memory_rename", "memory_relink", "memory_delete",
                     "memory_review", "memory_status"],
        "includes": ["default"],
    },
    "safe": {
        # No shell / process / code-exec. For untrusted user input
        # paths where we want the LLM to still answer questions but
        # never touch the host.
        "tools":    ["read", "glob", "grep", "list", "web_search",
                     "web_fetch", "image_analyze", "pdf"],
        # Deliberately does NOT include `default` (which has
        # bash/write/edit/apply_patch).
        "includes": [],
    },
    "debugging": {
        # Composition example: union of research + coding.
        "tools":    [],
        "includes": ["research", "coding"],
    },
}


def _expand_preset(name: str, _seen: set[str] | None = None) -> list[str]:
    """Resolve a preset name to a flat, deduplicated function-name list.

    Walks the ``includes`` chain recursively. Cycle-safe: keeps a
    visited set so a misconfigured preset that references itself
    doesn't recurse forever. Unknown preset names raise KeyError —
    same contract as direct ``TOOLSETS[name]`` access used to have.
    """
    if _seen is None:
        _seen = set()
    if name in _seen:
        return []
    _seen.add(name)

    entry = TOOLSETS[name]
    out: list[str] = []
    seen_tools: set[str] = set()
    for inc in entry.get("includes", []) or []:
        for t in _expand_preset(inc, _seen):
            if t not in seen_tools:
                out.append(t)
                seen_tools.add(t)
    for t in entry.get("tools", []) or []:
        if t not in seen_tools:
            out.append(t)
            seen_tools.add(t)
    return out


def _exposed_set() -> set[str] | None:
    """Layer 2 exposure universe, read fresh each call.

    Registration-driven: every registered tool is exposed EXCEPT those
    registered with ``expose=False`` (internal helpers). Computed from the
    live registry, so plugin / MCP tools that register at runtime are
    visible immediately — no hand-maintained whitelist.

    Returning ``None`` (only via monkey-patch in test fixtures) means "no
    exposure filter for this call".
    """
    from openprogram.functions._runtime import exposed_names
    return exposed_names()


def list_available() -> list[str]:
    """Names of every registered function that (a) is on the exposure
    whitelist, (b) passes its sidecar gating, and (c) the user hasn't
    disabled via ``openprogram setup tools``.

    Reads ``check_fn`` / ``requires_env`` / ``can_use`` from each
    registered AgentTool's sidecar attributes (set by ``@function``).
    The disabled-list lives at ``tools.disabled`` in
    ``~/.openprogram/config.json`` and is read lazily so this module
    stays free of webui/FastAPI imports at registry-build time.
    """
    disabled: set[str] = set()
    try:
        from openprogram.setup import read_disabled_tools
        disabled = read_disabled_tools()
    except Exception:
        pass
    exposed = _exposed_set()
    return [
        t.name for t in _all_agent_tools()
        if (exposed is None or t.name in exposed)
        and _is_available_agent_tool(t)
        and t.name not in disabled
    ]


# ---------------------------------------------------------------------------
# Resolution API
def _resolve_folder_toolset(folder_name: str) -> list[str] | None:
    """Look up a user-defined folder in functions_meta.json. Returns the
    tool-name list for the folder, or None if it doesn't exist (so the
    caller falls through to DEFAULT_TOOLS). New folders default to all
    exposed tools, so a fresh folder = full set."""
    import os as _os
    try:
        import openprogram.webui as _webui_mod
        meta_path = _os.path.join(
            _os.path.dirname(_webui_mod.__file__), "functions_meta.json",
        )
        if not _os.path.isfile(meta_path):
            return None
        import json as _json
        with open(meta_path, encoding="utf-8") as f:
            meta = _json.load(f)
        tools = meta.get("profiles", meta.get("folders", {})).get(folder_name)
        return list(tools) if isinstance(tools, list) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------

def agent_tools(
    names: list[str] | None = None,
    *,
    toolset: str | None = None,
    source: str | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    only_available: bool = False,
    include_disabled: bool = False,
) -> list[AgentTool]:
    """Return AgentTool instances. Hermes-style preset resolution plus
    an OpenClaw-style allow/deny/source policy chain.

    Cascade (in order):

      1. Resolve the *initial set* by exactly one of:
            * ``names=`` — explicit list
            * ``toolset=`` — a name in :data:`TOOLSETS` (presets
              resolve recursively via their ``includes``)
            * neither — :data:`DEFAULT_TOOLS`
      2. Drop tools whose ``unsafe_in`` metadata blacklists ``source``
         (channel-level filter). Mirrors OpenClaw's
         ``filterToolsByMessageProvider``.
      3. Apply ``deny=`` — explicit subtraction by name.
      4. Apply ``allow=`` — explicit intersection (only listed names
         survive). Useful for per-call subagent / role-scoped runs.
      5. ``only_available=True`` drops tools whose ``check_fn`` /
         ``requires_env`` / ``can_use`` reports them unrunnable.

    All filters compose: ``toolset="research", deny=["pdf"],
    allow=["web_search", "read"]`` is "research minus pdf, then
    intersected with [web_search, read]". The allow step runs last so
    it acts as a hard ceiling regardless of what the preset includes.
    """
    if names is not None and toolset is not None:
        raise ValueError("Pass either `names` or `toolset`, not both.")
    if toolset is not None and toolset in TOOLSETS:
        names = _expand_preset(toolset)
        toolset = None
    elif toolset is not None and toolset not in TOOLSETS:
        # User-defined folder from Functions page (functions_meta.json).
        # Falls back to all exposed if the folder doesn't exist.
        names = _resolve_folder_toolset(toolset)
        toolset = None
    if names is None and toolset is None:
        names = DEFAULT_TOOLS
    picked = _filter_agent_tools(names=names, toolset=toolset, source=source)
    # Layer 2 — exposure whitelist. Anything decorated but not on the
    # whitelist never reaches the LLM, no matter what preset, allow,
    # or check_fn says. This is the cascade's foundation: every later
    # filter operates on a subset of the exposed universe. ``None``
    # means the test harness disabled this layer.
    exposed = _exposed_set()
    if exposed is not None:
        picked = [t for t in picked if t.name in exposed]
    if deny:
        denyset = set(deny)
        picked = [t for t in picked if t.name not in denyset]
    if allow is not None:
        allowset = set(allow)
        picked = [t for t in picked if t.name in allowset]
    # User-disabled tools (``tools.disabled`` in config, toggled from the
    # Functions page) are hidden from the LLM by default — this is what
    # makes the per-tool on/off switch actually take effect. Pass
    # ``include_disabled=True`` only to *list* them (e.g. /api/tools, so
    # the user can switch them back on).
    if not include_disabled:
        try:
            from openprogram.setup import read_disabled_tools
            disabled = read_disabled_tools()
            if disabled:
                picked = [t for t in picked if t.name not in disabled]
        except Exception:
            pass
    if only_available:
        picked = [t for t in picked if _is_available_agent_tool(t)]
    return picked


def apply_tool_policy(
    tools: list[AgentTool],
    *,
    source: str | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    only_available: bool = False,
    exposure_filter: bool = True,
) -> list[AgentTool]:
    """Run the policy cascade on an existing AgentTool list.

    Same channel / allow / deny / availability filters as
    :func:`agent_tools`, applied post-construction. Use this when the
    caller already has a tool list (e.g. produced by an explicit
    ``runtime.exec(tools=[...])`` call) and needs to enforce session
    or channel policy on top — mirrors how OpenClaw runs its tool
    builder once and then layers ``wrapTool*`` filters over the
    result.

    ``exposure_filter`` (default True) applies the Layer 2 exposure
    whitelist. Set it False for tools the CALLER supplied ad-hoc via
    ``runtime.exec(tools=[...])``: those are self-authorized (the caller
    decided to expose them) and are typically NOT in the framework's
    registered-tool whitelist, so applying the whitelist would silently
    drop every such tool — which broke ``call_with_schema`` /
    forced-submit flows entirely.
    """
    # ``list`` builtin is shadowed by the ``.list`` subpackage import
    # above; use slice copy instead of ``list(...)``.
    out = [t for t in tools]
    # Layer 2 — same exposure whitelist that :func:`agent_tools`
    # applies. Anything not on the list never reaches the LLM.
    # Skipped for caller-supplied ad-hoc tools (exposure_filter=False).
    # ``None`` (test fixtures) also disables this layer.
    if exposure_filter:
        exposed = _exposed_set()
        if exposed is not None:
            out = [t for t in out if t.name in exposed]
    if source:
        out = [t for t in out if source not in _unsafe_in_for(t.name)]
    if deny:
        denyset = set(deny)
        out = [t for t in out if t.name not in denyset]
    if allow is not None:
        allowset = set(allow)
        out = [t for t in out if t.name in allowset]
    if only_available:
        out = [t for t in out if _is_available_agent_tool(t)]
    return out


def _unsafe_in_for(tool_name: str) -> set[str]:
    """Read the live unsafe_in metadata for a single function. Looks at
    the in-process channel registry that ``@function(unsafe_in=[...])``
    populates so the answer reflects whatever plugins are loaded right
    now.
    """
    from openprogram.functions._runtime import _unsafe_in_channel
    return _unsafe_in_channel.get(tool_name, set())


def get_agent_tool(name: str) -> AgentTool | None:
    """Look up a single AgentTool by name from the unified registry.

    Honours the Layer 2 exposure whitelist: returns ``None`` for
    decorated-but-not-exposed names so internal helpers (e.g. private
    @agentic_function bodies whose name is not in ``EXPOSED_TOOLS``)
    don't leak through this API. Internal Python code that needs to
    invoke a non-exposed helper directly should use the Python-level
    name (the function or class instance), not this registry lookup.
    """
    exposed = _exposed_set()
    if exposed is not None and name not in exposed:
        return None
    return _get_agent_tool(name)


def list_registered_agent_tools() -> list[str]:
    """Names of every tool present in the AgentTool registry **and** on
    the Layer 2 exposure whitelist.

    This is what the dispatcher / UI shows as "tools the framework can
    expose to an LLM". Non-exposed helpers are filtered out — see
    :data:`EXPOSED_TOOLS`.
    """
    exposed = _exposed_set()
    if exposed is None:
        return [t.name for t in _all_agent_tools()]
    return [t.name for t in _all_agent_tools() if t.name in exposed]


def resolve_function_module(name: str):
    """Return the imported Python module that defines an @agentic_function
    of the given name.

    Walks the agentic local registry (populated when @agentic_function
    fires its side-effect on import) and returns the module the
    callable belongs to. Used by the ``openprogram programs run <name>``
    CLI path and the webui's function-source / function-edit endpoints.

    Raises ``ImportError`` if the name isn't a registered agentic
    function — the caller decides how to surface that.
    """
    from openprogram.agentic_programming.function import (
        _registry as _agentic_local_registry,
    )
    import importlib
    instance = _agentic_local_registry.get(name)
    if instance is not None and instance._fn is not None:
        return importlib.import_module(instance._fn.__module__)
    # Fallback: standard agentics layout — agentics/<name>/__init__.py
    # might exist even if registration didn't fire (e.g. listed in
    # AGENTIC_MODULES but skipped at load time).
    try:
        return importlib.import_module(
            f"openprogram.functions.agentics.{name}"
        )
    except ImportError:
        raise ImportError(
            f"No @agentic_function named {name!r} found in the "
            f"agentic registry or under openprogram/functions/agentics/."
        )


__all__ = [
    "DEFAULT_TOOLS",
    "TOOLSETS",
    "AgentTool",
    "ToolReturn",
    "agent_tools",
    "apply_tool_policy",
    "function",
    "resolve_function_module",
    "get_agent_tool",
    "list_available",
    "list_registered_agent_tools",
    "tool_requires_approval",
    # Layer 6 — deferred loading helpers
    "deferred_catalog_text",
    "install_loaded_deferred",
    "mark_deferred_loaded",
    "split_tools_for_dispatch",
    "tool_search",
]
