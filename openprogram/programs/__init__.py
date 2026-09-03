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
    is_available_agent_tool as _is_available_agent_tool,
)
from ._runtime import (
    AgentTool,
    ToolReturn,
    all_tools as _all_agent_tools,
    deferred_catalog_text,
    filter_for as _filter_agent_tools,
    freeze_turn_tools,
    function,
    get as _get_agent_tool,
    install_allowed_tool_names,
    install_loaded_deferred,
    release_turn_tools,
    mark_deferred_loaded,
    register as _register_agent_tool,
    split_tools_for_dispatch,
    tool_requires_approval,
    tool_search,
)

# Side-effect imports — ``tools`` holds @function-decorated leaf
# tools and ``workflow`` holds @agentic_function bodies. Each
# subpackage's
# ``__init__`` triggers the decorator side-effects so every shipped
# function lands in the shared ``_registry`` by the time the parent
# package finishes loading. The source tree split
# mirrors the semantic split (deterministic leaf vs LLM-aware
# composable); both end up in the same registry.
from . import tools as _tools_self_register  # noqa: F401
from .tools.knowledge.memory import MEMORY_TOOL_NAMES
from . import workflow as _workflow_self_register  # noqa: F401

# Layer 2 — exposure is registration-driven: ``exposed_names()`` (see
# ``_runtime.py``) is every registered tool minus the ones registered
# with ``expose=False``. That set is the universe every preset and the
# per-call cascade filter subsets of; there is no hand-maintained
# whitelist to keep in sync, and a plugin / MCP tool that registers at
# runtime is exposed the moment it registers.


# The safe default set: file ops + shell + search + multi-file patch +
# transactional memory + the todo planning board. Omits ``process`` (long-lived
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
    "agent",
    # Branch-to-branch communication — an agent can see other agents
    # (sessions/branches) and message them
    # (docs/reference/design/runtime/agent-collaboration.md).
    "send_message",
    "list_agents",
    # Outbound attachments — the agent hands a real file back to the
    # user (chip in web chat, native upload on a chat platform).
    # Hidden by ``unsafe_in`` wherever there is no attachment channel.
    "send_file",
    "todo_create",
    "todo_update",
    "todo_list",
    # The load verb for <available_skills>. The listing carries a capped
    # summary; this pulls the SKILL.md body when the model picks one.
    # Deferred (below) — the listing already names every skill, so the
    # schema only has to exist on the turn a skill is actually loaded.
    "skill",
    "playwright_browser",
    "browser_agent",
    "web_use",
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
    # Persistent memory is available in ordinary chats. Its schemas are
    # deferred below, so the model sees the names and descriptions here but
    # only loads the full contracts when it needs to inspect or edit memory.
    *MEMORY_TOOL_NAMES,
    "scheduler",
    # Agentic harness entry points — these are the user-facing
    # programs (gui_agent / research_agent / wiki_agent) that the
    # LLM should know about by default. Without them in DEFAULT_TOOLS
    # the model has no idea these exist and resorts to bash-grepping
    # the filesystem when asked to "use gui_agent" or similar.
    "gui_agent",
    "research_agent",
    "wiki_agent",
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
    # 1052 tok。进 plan mode 有两条路：用户在 web chip / TUI 选 "Plan mode"
    # 档位（agent/plan_mode.py sync_tier，不经此工具），或模型自己判断要规划。
    # 后者罕见，且一旦 plan mode 激活，plan_mode 系统提示块会明确点名
    # exit_plan_mode，模型据此 tool_search 加载即可。UI 入口不依赖它常驻。
    "enter_plan_mode",
    # 644 tok。只在 plan mode 内有意义；plan 提示块已指名要调它。
    "exit_plan_mode",
    # 1172 tok，最大单个 schema。浏览器自动化是明确的小众意图，用户说
    # "打开网页/截图" 时模型再加载，日常编码会话完全用不到。
    "playwright_browser",
    "browser_agent",
    # 378 tok。跨 session/branch 通信，多分支协作场景才用得上。
    "send_message",
    # 技能加载动词。<available_skills> 每轮已经列了名字和描述，模型要用
    # 才需要这个 schema；不用的会话不该为它付费。catalog 一行即可。
    "skill",
}

# 常驻工具：schema 一直带在请求里（不 defer）。= DEFAULT_TOOLS 减去上面的冷门大块，
# 再加 tool_search 引导器（它是加载其余工具的唯一入口，永不 defer）。
# DEFAULT_TOOLS 之外的已曝光工具也默认 defer。
# 这是治「exec 默认全塞 ~14000 token」的唯一旋钮 —— 保守取，宁多勿缺。
RESIDENT_TOOLS: set = (
    (set(DEFAULT_TOOLS) - DEFERRED_DEFAULT_TOOLS)
    | {"web_use", "tool_search"}
    # `agent` 是常驻的，而它自己的返回文本会写 "Call job_output(job_id=…) to
    # retrieve result, or job_stop(job_id) to cancel"。这两个 defer 掉，模型
    # 照着提示调用就撞 InputValidationError，得先 tool_search 才能接上，白费一轮。
    | {"job_output", "job_stop"}
)


# Hermes-style named presets. ``default`` is the always-on minimal
# safe set above; ``full`` is every exposed tool, computed live rather
# than written down (see ``_preset_tool_names``). Every other preset
# (research / browser / coding / …) is a curated subset of it.
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
}


def _preset_tool_names(name: str, entry: dict[str, list[str]]) -> list[str]:
    """The tool names a preset contributes directly (before ``includes``).

    Every preset but one is the literal list in its entry. ``full`` is
    the exception: it means "every exposed tool", so it is read from
    the live registry instead of being written down. A tool joins it by
    registering, and a private helper stays out of it by registering
    with ``expose=False`` — neither needs an edit here.
    """
    if name == "full":
        exposed = _exposed_set()
        if exposed is None:      # exposure filter disabled by a test fixture
            return sorted(t.name for t in _all_agent_tools())
        return sorted(exposed)
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
    from openprogram.programs._runtime import exposed_names
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
    try:
        from .meta_storage import load_functions_meta

        meta = load_functions_meta({})
        tools = meta.get("profiles", meta.get("folders", {})).get(folder_name)
        return list(tools) if isinstance(tools, list) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------

_VERIFIER_READ_TOOLS = frozenset({"read", "glob", "grep", "list"})


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
    if source == "self_update_verify":
        picked = [t for t in picked if t.name in _VERIFIER_READ_TOOLS]
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
    if source == "self_update_verify":
        out = [t for t in out if t.name in _VERIFIER_READ_TOOLS]
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
    from openprogram.programs._runtime import _unsafe_in_channel
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
            f"openprogram.programs.workflow.{name}"
        )
    except ImportError:
        raise ImportError(
            f"No @agentic_function named {name!r} found in the "
            f"agentic registry or under openprogram/programs/workflow/."
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
    "freeze_turn_tools",
    "install_allowed_tool_names",
    "install_loaded_deferred",
    "mark_deferred_loaded",
    "release_turn_tools",
    "split_tools_for_dispatch",
    "tool_search",
    # Layer 6 — default deferral of cold full-toolset tools
    "RESIDENT_TOOLS",
    "DEFERRED_DEFAULT_TOOLS",
    "apply_default_deferral",
]


def apply_default_deferral() -> None:
    """把已曝光工具里不在 RESIDENT_TOOLS 的标 _defer=True。幂等。
    在本模块 import 末尾调一次（所有工具已注册）。best-effort：失败不影响 import。

    效果：split_tools_for_dispatch 后，只有常驻工具带完整 schema（~2000 token），
    其余退到 deferred catalog（每个一行描述）。治 exec 默认全塞 ~14000 的问题。"""
    try:
        for t in agent_tools(toolset="full", include_disabled=True):
            name = getattr(t, "name", "")
            should_defer = name not in RESIDENT_TOOLS
            if name == "tool_search":
                should_defer = False  # 引导器永不 defer 自己（双保险）
            try:
                setattr(t, "_defer", should_defer)
            except Exception:
                pass
    except Exception:
        pass


# 冷门工具默认 defer —— 见 RESIDENT_TOOLS / 论文仓库 spec §5 ④。
apply_default_deferral()
