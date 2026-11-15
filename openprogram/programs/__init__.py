"""Programs: everything the Agent can call, and how a call is resolved.

Importing this package registers every Program: ``tools/`` registers the
built-in tools, then ``_host.registry.load_program_modules`` loads the
built-in applications, the user's Programs and the installed harnesses,
each through its own ``ProgramContext``. What this module exports is the
resolution API on top of that registry: the named presets (``TOOLSETS``,
``DEFAULT_TOOLS``), the resident/deferred split, and the policy chain
(``agent_tools`` / ``apply_tool_policy``: preset, channel ``unsafe_in``,
deny, allow, availability). Other code imports Programs from here; the
``@function`` decorator and the registry itself live in
``_host/runtime.py``.
"""

from __future__ import annotations

from ._authoring.helpers import (
    is_available_agent_tool as _is_available_agent_tool,
)
from ._host.runtime import (
    AgentTool,
    ToolReturn,
    all_tools as _all_agent_tools,
    catalog_note,
    deferred_catalog,
    deferred_catalog_text,
    filter_for as _filter_agent_tools,
    function,
    get as _get_agent_tool,
    install_allowed_tool_names,
    install_turn_tools,
    loaded_tools,
    note_request_loaded,
    schema_not_sent_hint,
    split_tools_for_dispatch,
    tool_requires_approval,
    tool_search,
    Tier,
    as_resident,
    current_tool as _current_tool,
)
from ._host.lower_layers import register as _register_lower_layer_ports

# Models and state sit below Programs; they reach what only a Program has
# (web-search backends, Agent tool names, the Goal tool, the browser's
# argument form) through these ports rather than importing Programs.
_register_lower_layer_ports()

# Side-effect imports — importing ``tools`` registers every
# @function-decorated tool; ``load_program_modules`` then registers the
# ``PROGRAM_ENTRIES`` of the tool packages that carry them, the built-in
# applications under ``applications/``, the user workflows and the
# installed harnesses, each through its own ProgramContext. Every shipped
# function lands in the shared ``_registry`` by the time this package
# finishes loading.
from . import tools as _tools_self_register  # noqa: F401
from .tools.knowledge.memory import MEMORY_TOOL_NAMES
from ._host.registry import load_program_modules as _load_program_modules
import logging

_log = logging.getLogger(__name__)

_load_program_modules()

# Layer 2 — exposure is registration-driven: ``exposed_names()`` (see
# ``_host/runtime.py``) is every registered tool minus the ones registered
# with ``expose=False``. That set is the universe every preset and the
# per-call cascade filter subsets of; there is no hand-maintained
# whitelist to keep in sync, and a plugin / MCP tool that registers at
# runtime is exposed the moment it registers.


# The safe default set: file ops + shell + search + multi-file patch +
# transactional memory + the todo planning board. ``write_stdin`` reaches the
# command sessions ``bash`` leaves running.
DEFAULT_TOOLS: list[str] = [
    "bash",
    "write_stdin",
    "read",
    "write",
    "edit",
    "apply_patch",
    "glob",
    "grep",
    "list",
    "agent",
    # Branch-to-branch communication — an agent can see other agents
    # (sessions/branches) and message them
    # (docs/reference/design/runtime/collaboration/agent-collaboration.html).
    "send_message",
    "list_agents",
    # Outbound attachments — the agent hands a real file back to the
    # user (chip in web chat, native upload on a chat platform).
    # Hidden by ``unsafe_in`` wherever there is no attachment channel.
    "send_file",
    # Speaking in a chat channel on the agent's own initiative: the only
    # voice of an ambient room event, hidden outside channel sources.
    "chat_send",
    "todo_create",
    "todo_update",
    "todo_list",
    # What the Agent still owes the user, across its conversations.
    "open_work",
    "create_goal",
    "get_goal",
    "update_goal",
    # The load verb for <available_skills>. The listing carries a capped
    # summary; this pulls the SKILL.md body when the model picks one.
    # Deferred (below) — the listing already names every skill, so the
    # schema only has to exist on the turn a skill is actually loaded.
    "skill",
    # Reads a tool guide by name; results of guided tools point here.
    "help",
    # One entry for every screen: application windows, built-in browser
    # Pages, a VM. Resident: its description is a short catalogue of
    # targets, and each target's guide arrives with the first call to it.
    "computer_use_agent",
    # Offered only in a turn whose selector says Plan (see ``loop_runner``).
    # Plan comes from the selector alone, so no enter tool is offered.
    "exit_plan_mode",
    # MCP meta — read resources / render prompts on any configured
    # MCP server. Lightweight (one read per call), and the LLM needs
    # them on turn 1 to discover what's there; not deferred.
    "list_mcp_resources",
    "read_mcp_resource",
    "list_mcp_prompts",
    "get_mcp_prompt",
    # Persistent memory is available in ordinary chats. Its schemas are
    # deferred below, so the model sees the names and descriptions here but
    # only loads the full contracts when it needs to inspect or edit memory.
    *MEMORY_TOOL_NAMES,
    "scheduler",
    # Resident: a reminder is asked for in passing ("remind me at 5"), so
    # the model must see it without searching. The full scheduler stays
    # deferred for listing and cancelling.
    "remind",
    # "Do this every Monday": turns the work just done into a Skill and,
    # optionally, a waking reminder. Deferred below; asked for rarely.
    "save_routine",
    # The loader of the deferred tools above: a set that names them without
    # it would list tools nothing can load.
    "tool_search",
]


# DEFAULT_TOOLS 里**可用但不常驻**的工具：仍在 default 预设中（模型随时能用），
# 但完整 JSON Schema 不进每轮请求，只在 deferred catalog 里占一行 name+description，
# 模型按需 tool_search 加载。可用性与常驻性是两件事——这个集合把它们解耦。
#
# 入选标准：schema 大 + 调用频率低。四个大头合计 ~2.5k token，占常驻 ~7.9k 的三分之一，
# 而绝大多数会话一次都不调它们。
DEFERRED_DEFAULT_TOOLS: set = {
    *MEMORY_TOOL_NAMES,
    "scheduler",
    # 644 tok。只在 plan mode 内有意义；plan 提示块已指名要调它。
    "exit_plan_mode",
    # 378 tok。跨 session/branch 通信，多分支协作场景才用得上。
    "send_message",
    # 技能加载动词。<available_skills> 每轮已经列了名字和描述，模型要用
    # 才需要这个 schema；不用的会话不该为它付费。catalog 一行即可。
    "skill",
    # Saving a routine is rare; its catalog line says when to load it.
    "save_routine",
}

# 常驻工具：schema 一直带在请求里（不 defer）。= DEFAULT_TOOLS 减去上面的冷门大块
# （其中的 tool_search 引导器是加载其余工具的唯一入口，永不 defer）。
# DEFAULT_TOOLS 之外的已曝光工具也默认 defer。
# 这是治「exec 默认全塞 ~14000 token」的唯一旋钮 —— 保守取，宁多勿缺。
RESIDENT_TOOLS: set = (
    (set(DEFAULT_TOOLS) - DEFERRED_DEFAULT_TOOLS)
    | {"read_conversation"}
)


# Tools an Agent gets only by naming them (a selected list or a preset
# that lists them), never through ``full``: an automatic Agent does not
# carry them. ``mixture_of_agents`` asks several models for one answer;
# that is a cross-model answer, not collaboration, and every tool in the
# list costs prefix tokens (Pi keeps a small cache-stable list for the
# same reason). Design: agent-collaboration.html#tools.
OPT_IN_TOOLS: frozenset[str] = frozenset({"mixture_of_agents"})


# Hermes-style named presets. ``default`` is the always-on minimal
# safe set above; ``full`` is every exposed tool, computed live rather
# than written down (see ``_preset_tool_names``). Every other preset
# (research / coding / …) is a curated subset of it.
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
        # Resolved from the registry by ``_preset_tool_names``, never
        # written out here: a static copy is a list someone forgets to
        # update. The empty list is the placeholder that keeps the
        # entry shaped like every other preset, so ``toolset="full"``
        # resolves and other presets may ``includes`` it.
        "tools":    [],
        "includes": [],
    },
    "research": {
        "tools":    ["web_search", "web_fetch", "pdf", "image_analyze"],
        "includes": ["default"],
    },
    "coding": {
        "tools":    ["execute_code"],
        "includes": ["default"],
    },
    "vision": {
        "tools":    ["image_analyze", "image_generate", "pdf"],
        "includes": ["default"],
    },
    "memory": {
        # Read from the bundle, not restated: a name the registry does not
        # know is dropped silently at resolution, so a hand-copied list
        # goes on quietly handing out less than it says. This one spent a
        # while offering three of the six memory tools.
        "tools":    list(MEMORY_TOOL_NAMES),
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
    "mixture_of_agents": {
        # The opt-in group for OPT_IN_TOOLS' cross-model answer.
        "tools":    ["mixture_of_agents"],
        "includes": ["default"],
    },
}


def _preset_tool_names(name: str, entry: dict[str, list[str]]) -> list[str]:
    """The tool names a preset contributes directly (before ``includes``).

    Every preset but one is the literal list in its entry. ``full`` is
    the exception: it means "every exposed tool", so it is read from
    the live registry instead of being written down. A tool joins it by
    registering, and a private helper stays out of it by registering
    with ``expose=False`` — neither needs an edit here. ``OPT_IN_TOOLS``
    stay out of it: they are offered only where named.
    """
    if name == "full":
        exposed = _exposed_set()
        if exposed is None:      # exposure filter disabled by a test fixture
            exposed = {t.name for t in _all_agent_tools()}
        return sorted(set(exposed) - OPT_IN_TOOLS)
    return list(entry.get("tools", []) or [])


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
    for t in _preset_tool_names(name, entry):
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
    from openprogram.programs._host.runtime import exposed_names
    return exposed_names()


def list_available() -> list[str]:
    """Names of every registered function that (a) is exposed, (b)
    passes its sidecar gating, and (c) the user hasn't disabled via
    ``openprogram setup tools``.

    Reads ``check_fn`` / ``requires_env`` / ``can_use`` from each
    registered AgentTool's sidecar attributes (set by ``@function``).
    The disabled-list lives at ``tools.disabled`` in
    ``~/.openprogram/config.json`` and is read lazily so this module
    stays free of webui/FastAPI imports at registry-build time.
    """
    disabled: set[str] = set()
    try:
        from openprogram.operations.setup import read_disabled_tools
        disabled = read_disabled_tools()
    except Exception:
        # tools.disabled is optional config; unreadable config hides nothing.
        _log.debug("tools.disabled unreadable; no tool treated as disabled", exc_info=True)
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
    try:
        from ._host.meta_storage import load_functions_meta

        meta = load_functions_meta({})
        tools = meta.get("profiles", meta.get("folders", {})).get(folder_name)
        return list(tools) if isinstance(tools, list) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------

_VERIFIER_READ_IMPLEMENTATIONS = tuple(
    (name, tool, tool.execute)
    for name in ("read", "glob", "grep", "list", "self_update_observe")
    for tool in (_get_agent_tool(name),)
    if tool is not None
)


def _is_verifier_read_tool(tool: AgentTool) -> bool:
    # Pin the callable too: the public registry's AgentTool objects are mutable.
    return any(
        tool.name == name and tool is registered and tool.execute is execute
        for name, registered, execute in _VERIFIER_READ_IMPLEMENTATIONS
    )


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
    if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        picked = [t for t in picked if _is_verifier_read_tool(t)
                  and (source == "self_update_verify" or t.name != "self_update_observe")]
    # Layer 2 — exposure. Anything registered with ``expose=False``
    # never reaches the LLM, no matter what preset, allow, or check_fn
    # says. This is the cascade's foundation: every later filter
    # operates on a subset of the exposed universe. ``None`` means the
    # test harness disabled this layer.
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
            from openprogram.operations.setup import read_disabled_tools
            disabled = read_disabled_tools()
            if disabled:
                picked = [t for t in picked if t.name not in disabled]
        except Exception:
            # tools.disabled is optional config; unreadable config hides nothing.
            _log.debug("tools.disabled unreadable; no tool hidden", exc_info=True)
    if only_available:
        picked = [t for t in picked if _is_available_agent_tool(t)]
    # Each tool as it describes itself now (the ``describe`` hook).
    return [_current_tool(t) for t in picked]


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
    ``agent(..., tools=[...])`` call) and needs to enforce session
    or channel policy on top — mirrors how OpenClaw runs its tool
    builder once and then layers ``wrapTool*`` filters over the
    result.

    ``exposure_filter`` (default True) applies the Layer 2 exposure
    whitelist. Set it False for tools the CALLER supplied ad-hoc via
    ``agent(..., tools=[...])``: those are self-authorized (the caller
    decided to expose them) and are typically NOT in the framework's
    registered-tool whitelist, so applying the whitelist would silently
    drop every such tool — which broke ``call_with_schema`` /
    forced-submit flows entirely.
    """
    # ``list`` builtin is shadowed by the ``.list`` subpackage import
    # above; use slice copy instead of ``list(...)``.
    out = [t for t in tools]
    if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        # Ad-hoc tools are not trusted just because they reuse a read name.
        out = [t for t in out if _is_verifier_read_tool(t)
               and (source == "self_update_verify" or t.name != "self_update_observe")]
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
    return [_current_tool(t) for t in out]


def _unsafe_in_for(tool_name: str) -> set[str]:
    """Read the live unsafe_in metadata for a single function. Looks at
    the in-process channel registry that ``@function(unsafe_in=[...])``
    populates so the answer reflects whatever plugins are loaded right
    now.
    """
    from openprogram.programs._host.runtime import _unsafe_in_channel
    return _unsafe_in_channel.get(tool_name, set())


def get_agent_tool(name: str) -> AgentTool | None:
    """Look up a single AgentTool by name from the unified registry.

    Honours the Layer 2 exposure whitelist: returns ``None`` for
    decorated-but-not-exposed names so internal helpers (e.g. private
    Agent method bodies whose name is not in ``EXPOSED_TOOLS``)
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
    """Resolve a registered Program's source module from shared tool metadata."""
    import importlib
    tool = _get_agent_tool(name)
    source = getattr(tool, "_source_module", None) if tool is not None else None
    if source:
        return importlib.import_module(source)
    # Fallback: a built-in Program package whose registration did not
    # fire (it failed at load time) is still found by its name.
    from ._host.registry import builtin_program_modules
    module_path = builtin_program_modules().get(name)
    if module_path:
        return importlib.import_module(module_path)
    raise ImportError(
        f"No Agent method named {name!r} found in the agentic registry "
        f"or among the built-in Programs."
    )


__all__ = [
    "DEFAULT_TOOLS",
    "TOOLSETS",
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
    "catalog_note",
    "deferred_catalog",
    "deferred_catalog_text",
    "install_allowed_tool_names",
    "install_turn_tools",
    "loaded_tools",
    "note_request_loaded",
    "schema_not_sent_hint",
    "split_tools_for_dispatch",
    "tool_search",
    # Exposure tiers
    "RESIDENT_TOOLS",
    "DEFERRED_DEFAULT_TOOLS",
    "Tier",
    "apply_default_deferral",
    "as_resident",
    "default_tier",
]


def default_tier(tool) -> "Tier":
    """The tier a registered entry starts in (program-model.md §5).

    An application entry (built-in or installed application, external
    harness, user package) is deferred, open in a window or not: the model
    loads it with ``tool_search``. A tool is resident when it
    is in ``RESIDENT_TOOLS``, deferred otherwise. A hidden entry stays hidden.
    """
    from openprogram.programs._host.runtime import Tier, owner_kind, tool_owner, tool_tier
    if tool_tier(tool) is Tier.HIDDEN:
        return Tier.HIDDEN
    if tool.name == "tool_search":
        return Tier.RESIDENT  # the loader never defers itself
    if owner_kind(tool_owner(tool)) == "application":
        return Tier.DEFERRED
    return Tier.RESIDENT if tool.name in RESIDENT_TOOLS else Tier.DEFERRED


def apply_default_deferral() -> None:
    """Give every registered entry its default tier. Idempotent.

    Runs once at the end of this module's import, when every shipped entry
    is registered; entries registered later (installed applications, MCP
    tools, reloaded packages) get theirs at registration.
    """
    from openprogram.programs._host.runtime import set_tier
    # ``full`` leaves out the opt-in tools; they get their tier too.
    opt_in = [t for t in agent_tools(toolset="mixture_of_agents", include_disabled=True)
              if t.name in OPT_IN_TOOLS]
    for t in [*agent_tools(toolset="full", include_disabled=True), *opt_in]:
        try:
            set_tier(t, default_tier(t))
        except Exception:
            # One entry failing must not abort the registry's import.
            _log.warning("default tier not applied to tool %s", t.name, exc_info=True)


apply_default_deferral()
