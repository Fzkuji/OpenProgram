# Function calling — design as built

Status: **implemented, revised**. This document describes the
function-calling framework. The exposure / default-tools design was
revised after the original "unification" build; the revision is marked
**[REVISED]** in each affected section and summarized here. For the
moment-by-moment loop mechanics (how the LLM picks the next tool to run
inside one ``runtime.exec`` call), see
``docs/agentic-programming/tool-calling.md`` — that companion doc is
still accurate.

## What changed in the revision (read this first)

The original build had two properties that turned out wrong, both
fixed here:

1. **Exposure was a STATIC hand-written whitelist.** `_exposed_set()`
   returned `set(TOOLSETS["full"]["tools"])` — a list someone had to
   edit by hand. Tools registered at runtime (plugins, MCP servers)
   were **invisible to the model** unless their names were added to
   that list. None of the frameworks we cite as parents do this:
   Claude Code, Hermes, and OpenClaw all expose tools **on
   registration** (Claude Code: MCP/plugin tools auto-discovered +
   `list_changed`; Hermes: `registry.register()` / `ctx.register_tool()`
   makes a tool first-class immediately). So the static whitelist was a
   silent divergence from the "Claude Code parity" the doc claimed.
   → **Now: exposure is registration-driven.** Any registered
   `@function` / `@agentic_function` / plugin / MCP tool is visible by
   default. `TOOLSETS["full"]` is derived from the registry, not
   hand-maintained. Tools opt OUT of exposure with `expose=False`
   (internal helpers like `_pick_stage`); they don't opt in.

2. **Tools were OFF by default.** A bare `runtime.exec(content=...)`
   got NO tools; every function had to opt in per call. This caused a
   recurring class of bug — a function needed a tool, forgot to ask for
   it, and silently produced nothing (literature search returned no
   papers, experiment steps wrote no files). → **Now: tools are ON by
   default.** A bare `runtime.exec` gets the full registered set.
   Tool-call results live only in the run history and never leak into
   later prompt context, so broad exposure has no context cost. A
   call that genuinely wants none (a pure-choice / pure-reasoning step)
   opts out with `toolset="none"`.

User-facing management of *which* tools are on lives on the Functions
page (`/functions`) as folders-as-categories — see "User-managed tool
categories" below. The principle is **default-on, user-curated**, not
**default-off, author-opted-in**.

## What "function calling" means here

The mechanism by which an LLM picks a function from a list, the
framework runs that function, and the result is fed back as the
model's next-turn input. Same concept the industry calls "tool use"
on the wire (``tools=[]`` / ``tool_calls=[]`` in the OpenAI /
Anthropic / Gemini APIs). Our code calls the *act* "function
calling" because that's what authors do ("write a function, expose
it to the LLM"), but the *thing in the API request* stays ``tool``
to match SDK terminology.

```
我们(编写姿势)                       LLM API wire / providers/types.py
─────────────────────────────────────────────────────────────────
@function 装饰器                       Tool / ToolCall / ToolResultMessage
@agentic_function 装饰器               tools=[...] 字段
agent_tools() / get_agent_tool() …    tool_calls=[...] 字段
```

The boundary is **the wire format**: providers serialize each
``AgentTool`` in our registry into the API's ``Tool`` JSON shape; the
model's ``tool_calls`` come back, get matched by name against our
registry, and ``AgentTool.execute(...)`` runs. Our wrapping classes
(``AgentTool`` / ``AgentToolResult``) carry runtime extras the wire
format doesn't have (sidecar gating, sync→async, char-cap, etc.).

## Two decorators, one registry

Authors get exactly two ways to register an LLM-callable function:

```
@function                             @agentic_function
─────────────────────────────────────────────────────────────────
Function-implemented decorator        Class-implemented decorator
"deterministic Python tool"           "tool whose body spawns an
                                       inner agent loop"

bash, read, write, edit, glob,        research, gui_agent, idea-
grep, list, todo_*, web_search,       generator, evaluate, the
web_fetch, pdf, image_*,              memory_* family, the research
execute_code, apply_patch, …          stages, …

Decoration replaces the Python name   Decoration replaces the name
with the AgentTool object itself.     with an agentic_function class
Python code can't call `bash("ls")`   instance. Python code CAN call
directly — the only entry is the     `research("topic")` directly
LLM's tool_call dispatch.             (it triggers __call__ → wrapper);
                                       LLM can ALSO call via dispatcher.
                                       Both routes hit the same wrapper.
```

Both decorators ultimately produce one ``AgentTool`` entry in one
shared registry (``openprogram.functions._runtime._registry``). The
``_build_and_register_tool`` helper is the single source of truth for
"build AgentTool + attach sidecars + register". Both decorators
delegate to it; adding a new sidecar attribute or gating layer means
editing one helper, both decorators pick it up.

For the design rationale on why these are two decorators (not one)
and why ``@agentic_function`` is a class (not a function), see
"Why two decorators" below.

## The shared kwargs (apply to both decorators)

```
kwarg                       what it controls
─────────────────────────────────────────────────────────────────
name, description,          model-facing surface (the JSON the
parameters, label           LLM sees)
                            auto-derived from def signature +
                            docstring if omitted (only @function;
                            @agentic_function reuses
                            _build_agentic_tool_spec)

max_result_chars,           result truncation — head+tail with
persist_full, head_ratio,   marker; persist-to-disk for full
stream_capacity_chars       version; bounded tail accumulator
                            for streamed on_update

timeout,                    static + LLM-controllable timeout
timeout_min, timeout_max    (clamp into range, used both as
                            wait_for budget and passed-through
                            to the fn body)

cache, cache_ttl            memoize on (name, args)

check_fn                    Layer 4 — process-level "this tool
                            can run now" gate
requires_env                Layer 4 — env vars that must be set
can_use                     Layer 4 — session-level gate
requires_approval           dispatcher consults before invoking

expose                      [REVISED] Layer 2 — exposure opt-OUT.
                            Default True: a registered tool is
                            visible to the model. Set False for
                            internal helpers that Python calls but
                            the LLM must never see (e.g. _pick_stage,
                            write_section, the _merge_* leaves).
                            Replaces the old hand-edited
                            TOOLSETS["full"] whitelist.

toolset                     Layer 2b — preset membership (Hermes-
                            style: tool also goes into the "research"
                            preset, etc.). A preset is a NAMED SUBSET
                            for callers that want fewer than all
                            exposed tools; it is no longer the gate
                            for visibility (expose is).
unsafe_in                   Layer 3 — channel blacklist
                            (OpenClaw-style: hide on Telegram)

available_if                Layer 1 — registration-time gate.
                            Decided once at import; False → tool
                            never enters the registry.
defer                       Layer 6 — schema-deferred. Tool is
                            registered but its full JSON Schema is
                            NOT shipped to the provider unless
                            the LLM calls tool_search first.

register_globally           If False, build AgentTool + attach
                            sidecars but skip the global register.
                            Useful for in-test isolation.
```

## The gating layers [REVISED]

Tool selection per turn passes through these filters. The skeleton is
still Claude Code's `tools.ts`, but Layer 2 is now **registration-driven
exposure** (matching Claude Code / Hermes), not a static whitelist.

```
Layer  When                  How configured                Effect when rejected
─────────────────────────────────────────────────────────────────────────────────
1   at import / decoration  @function(available_if=...)    tool never enters
                            @agentic_function(             _registry → invisible
                              available_if=...)            everywhere
                                                            (Claude Code's
                                                            `feature() ?
                                                            require() : []`)

2   exposure (DEFAULT ON)   @function(expose=False)        expose=False → Python-
    [REVISED]               for internal helpers           callable but never in
                            — everything else exposed       any LLM tools array.
                            simply by being registered      DEFAULT is exposed.
                            (plugins / MCP included).        No hand-edited list.

2b  preset membership       @function(toolset=[...])       a named SUBSET for
    (optional narrowing)    TOOLSETS / DEFAULT_TOOLS        callers who want fewer
                            (Hermes includes chain)         than "all exposed";
                                                            not a visibility gate.

3   per-session mode         agent_profile.toolset =       this session sees a
                            "safe"/"research"/<folder>     narrower set than the
                            (a Functions-page folder        full exposed set
                             counts as a named subset)

4   per-tool-list build      @function(check_fn=,         filtered out of this
    isEnabled-style          requires_env=, can_use=)     session's tools list
                            agent_tools(only_available=    when the runtime gate
                              True)                         fails (missing key/env)

5   user/policy filter       agent_tools(deny=, allow=)    explicit subtraction /
                            agent_profile.disabled         intersection by name;
                            Functions-page off-toggle      attended-mode denies
                            (tools.disabled) ; attended    ask_user_question
                              mode

6   prompt construction      @function(defer=True)         schema NOT in provider
    schema-deferred                                        request; name + 1-liner
                                                          in deferred catalog;
                                                          LLM calls tool_search
                                                          to load schema first
```

Default behaviour, end to end: a registered tool is **on** (Layer 2
exposed) unless its author set `expose=False`; a bare `runtime.exec`
gets the **full exposed set**; the user narrows from there via agent
profile (Layer 3), per-call allow/deny or the Functions-page off-toggle
(Layer 5). `toolset="none"` is the explicit "give this call no tools"
opt-out for pure-choice steps.

Layers 1–5 mean "the LLM cannot see/use this tool". Layer 6 means "the
LLM sees the name in a catalog but must opt-in to load the schema" — the
only layer that lets the LLM itself choose what to pull in (used to keep
large MCP/plugin tool sets out of the prompt until needed).

## Per-layer default policy [REVISED]

The guiding principle is **default-on, user-curated**: a freshly
registered tool is usable with zero configuration; every layer's
default is "don't restrict". Layers split into two kinds —

- **Active (selection) layers** decide *what to use*. Default =
  "everything exposed". The user/caller opts IN to a narrower set.
  These are the normal path.
- **Passive (veto) layers** decide *what must not be used*. Default =
  "veto nothing". They only ever subtract, as a safety/maintenance
  backstop, and stay dormant unless a condition fires.

```
Layer  Kind      Default (no config)                Who overrides & when
──────────────────────────────────────────────────────────────────────────────
1      —         tool registers (available_if       author: only to gate a
       gate      absent/True)                        tool behind a feature/env
                                                     that makes it meaningless
                                                     otherwise

2      active    EXPOSED. Registered ⇒ visible to    author: expose=False for
       (expose)  the LLM, including plugin/MCP.      an internal helper the LLM
                 Bare runtime.exec ⇒ full exposed    must never see. That's the
                 set. Nothing hidden by default.     ONLY reason to touch L2.

2b     active    no preset forced. Caller gets the   caller/user: pass
       (preset)  full exposed set unless it names    toolset="research" / a
                 a subset.                           Functions-page folder to
                                                     work with fewer tools.
                                                     toolset="none" = no tools
                                                     (pure-choice steps).

3      passive   no channel/mode restriction.        framework: a tool's
       (channel) source=None ⇒ nothing filtered.    unsafe_in fires only on
                                                     that channel (telegram/
                                                     wechat/plan). profile
                                                     toolset narrows a session.

4      passive   tool assumed runnable. Only         framework: drops a tool
       (avail)   checked when                        whose key/env/can_use is
                 only_available=True (dispatcher     missing — so the LLM never
                 path).                              sees a tool that would
                                                     error on call.

5      passive   veto nothing. allow=None,           user: Functions-page
       (veto)    deny=None, no disabled, attended    off-toggle (persistent,
                 unless set.                         tools.disabled). system:
                                                     attended-mode denies
                                                     ask_user_question;
                                                     subagent/role caps via
                                                     allow/deny. Pure subtraction.

6      active*   not deferred. Schema ships in the   author / MCP wiring:
       (defer)   request by default.                 defer=True for large MCP
                                                     surfaces → name in catalog,
                                                     LLM tool_search to load.
                                                     *active by the LLM, not the
                                                     user.
```

**L2b (active) vs L5 (passive) — why both exist, why not merged.** They
look like "user touches tools" twice but are opposite operations: L2b is
the user/caller *choosing a set to use* (additive, a folder = "today's
toolbox"); L5 is *blacklisting / capping* (subtractive, "this tool is
broken / this session may not use it"). A veto must be independent of
selection — e.g. attended-mode must withhold `ask_user_question` no
matter which folder is active; you can't rely on the user remembering to
leave it out of every folder. So the **Functions page surfaces L2b as
the primary action (organize & pick folders) and L5 as the exception
(a per-tool off switch)**; the system's own L5 vetoes (attended,
subagent caps) are code-only and invisible to the user.

## Plugins and MCP servers [REVISED]

Because exposure is registration-driven (Layer 2), a plugin or MCP
server makes its tools available **just by registering them** — same as
Claude Code and Hermes. There is no second step of editing a central
allowlist. Concretely:

- A plugin's tools register through the same `_build_and_register_tool`
  path as built-ins (a plugin calls `@function` / registers an
  `AgentTool`), so they land in `_registry` and are exposed by default.
- MCP-server tools are registered as `AgentTool` entries on connect and
  marked `defer=True` (Layer 6) by default, so their names appear in the
  deferred catalog and the LLM loads schemas on demand via
  `tool_search` — this keeps a large MCP surface out of every prompt
  without making the tools invisible.
- A plugin/MCP tool that should stay internal can still set
  `expose=False`; a user can still turn any of them off on the Functions
  page (Layer 5). The default, though, is "registered = usable".

This is the property the old static whitelist broke: previously a
plugin tool was registered but unreachable until someone hand-added its
name to `TOOLSETS["full"]`. That manual step is gone.

## User-managed tool categories (Functions folders) [REVISED]

Default is **all exposed tools on**. The user curates from the
Functions page (`/functions`) instead of authors curating in code:

- The page supports **folders as categories** — the same
  `{favorites, folders}` shape already used by the Programs page
  (`programs_meta.json`), persisted here as `functions_meta.json`.
  A user creates a folder ("writing", "search", "shell-only"), drags
  tools into it, and toggles individual tools on/off.
- A folder name resolves as a **named subset** wherever a `toolset=`
  is accepted (Layer 3): selecting folder "search" for a session is
  equivalent to `agent_tools(toolset="search")`. Folders are
  user-defined presets that sit beside the code-defined TOOLSETS.
- Per-tool off-toggles write `tools.disabled` (Layer 5), already read by
  `read_disabled_tools()`, so a disabled tool is filtered out of every
  resolution regardless of folder.

Net model: the framework ships everything on; the user, if they care,
organizes tools into folders and switches things off — they never have
to switch things *on* to make a registered tool work.

## Four knobs none of the reference frameworks have

Beyond the 6-layer cascade, the framework adds four runtime knobs
neither Claude Code, Hermes, nor OpenClaw ship:

```
1. Dynamic per-call result ceiling          _effective_max_chars() +
   min(per-tool max, 0.3 × ctx_window)      _current_context_window_chars
   small-context models auto-shrink         ContextVar installed by
                                            dispatcher per turn

2. LLM-controllable timeout (clamp)         If fn declares `timeout`
   LLM-passed value clamped into            param AND decorator sets
   [timeout_min, timeout_max]; both used    timeout_min/max → clamped
   as wait_for budget and fn param           and passed both places

3. Streaming tail accumulator (bounded)     _TailAccumulator —
   long-running tools writing through        capacity defaults to
   on_update can't grow unbounded            max_result_chars, head
                                            evicted on overflow

4. can_use() session-level gate              Distinct from check_fn
   process-level "can it run" (check_fn) +  (always-on installable)
   channel-level "is it allowed here"        and unsafe_in (channel
   (unsafe_in) + session-level "is this      blacklist)
   user / role allowed to use it" (can_use)
```

## Why two decorators, not one

The two decorators wrap different *kinds of work*:

- **@function** wraps deterministic Python code. The body runs once
  per LLM tool_call and returns its result. No LLM rounds inside.
  Examples: ``bash`` runs subprocess, ``web_search`` calls an API,
  ``read`` reads a file. The decorated function is **only** called
  by the LLM via dispatcher — no Python code does ``bash("ls")``
  directly. So it's safe for the decorator to REPLACE the Python
  name with the ``AgentTool`` object (the original function is
  gone from the module namespace after decoration).

- **@agentic_function** wraps "an inner agent loop" — the body
  itself runs an LLM via ``runtime.exec(...)`` and may call other
  ``@agentic_function``s recursively. These functions are called by
  the LLM **and** also called directly from Python — one
  ``@agentic_function`` typically composes several others, e.g.
  ``research_pipeline`` calls ``survey_topic`` → ``generate_ideas``
  → ``rank_ideas`` as plain Python. So the decorated name must
  **remain a Python callable**. We can't replace it with an
  ``AgentTool`` like @function does.

Hence: @agentic_function is a **class decorator**. The decorated
name becomes a class instance that:

- Has ``__call__`` so ``research("topic")`` runs the wrapper
  (synchronously or as a coroutine, matching the original fn)
- Has a sidecar ``_agent_tool`` referencing an ``AgentTool`` that
  was registered in the shared registry
- Has methods (``.execute``, ``.spec``) and attributes
  (``.expose``, ``.render_range``, ``._fn``, ``._wrapper``) that
  other code (``spawn_program``, the webui, DAG visualizer) reads

Both decorators contribute ``AgentTool`` entries to one shared
registry, so the dispatcher / agent_loop / provider adapter only
ever deal with ``AgentTool`` — they don't distinguish the two
decorators. The split is invisible past the registry layer.

The same logic could in principle be a single class decorator with
a ``mode="leaf" | "agentic"`` flag, but that hides the genuine
semantic difference inside a flag. Two decorators makes the choice
explicit at the call site: ``@function`` on a leaf, ``@agentic_function``
on an agentic body.

## Decoration → registration trace

### @function (leaf)

```
@function(name="bash", toolset=["core"], unsafe_in=["wechat"], ...)
def bash(command: str) -> str: ...

→ function(name="bash", ...) is called with no fn → returns _inner

→ _inner(bash) is called → re-enters function(bash, name="bash", ...)

  Inside function():
    - parse docstring + type hints (or use overrides)
    - build _execute async closure that calls bash(**args)
    - _build_and_register_tool(
          name="bash", description=…, parameters=…, label=…,
          execute=_execute, check_fn=…, defer=…, toolsets=[…],
          unsafe_in=[…], register_globally=True)
      → constructs AgentTool
      → setattr sidecar attrs (_check_fn / _requires_env / _can_use /
                                _defer / _requires_approval)
      → register(agent_tool, toolsets=…, unsafe_in=…)
        → _registry["bash"] = agent_tool
        → _toolset_membership["bash"] = {"core"}
        → _unsafe_in_channel["bash"] = {"wechat"}
      → returns AgentTool
    - returns AgentTool

→ module-level name `bash` now points at the AgentTool
```

### @agentic_function (composite)

```
@agentic_function(name="research", toolset=["research"], expose="io", ...)
def research(topic: str) -> str: ...

→ agentic_function(name="research", ...) instantiates the class
  with fn=None — __init__ stores config + leaves _fn / _wrapper unset

→ Python passes `research` (the function) to the instance:
  instance(research) → triggers __call__(research)

  Inside __call__:
    - _fn is None → this is the decorator entry path
    - delegates to self._attach(research):
        - Layer 1 (available_if) check
        - self._fn = research
        - self._wrapper = self._make_wrapper(research)
              → wrapper does:
                  pre-invocation hooks (cancel check),
                  _inject_runtime (auto-fill the `runtime` kwarg),
                  DAG entry node,
                  call research(**args) (which probably runs
                    runtime.exec(...) for an inner LLM round),
                  DAG exit node,
                  return value
        - functools.update_wrapper(self, research)
        - _registry["research"] = self     ← local registry
                                              (for spawn_program /
                                               webui instance lookup)
        - if as_tool=True:
            self._register_as_tool()
              → builds _execute closure that funnels through
                self._wrapper
              → _build_and_register_tool(
                    name="research", description=…, parameters=…,
                    label=…, execute=_execute, sidecar kwargs, …)
              → AgentTool lands in the SAME shared _registry as
                @function tools
              → self._agent_tool = the returned AgentTool

  Returns self (the instance, now fully attached).

→ module-level name `research` now points at the agentic_function
  instance. It's both:
    - directly callable as Python (research("topic") → __call__ →
      wrapper → fn body)
    - present in the shared registry as an AgentTool (LLM can
      tool_call it)
```

## Resolution path (dispatcher → provider)

```
1. user message arrives → dispatcher.process_user_turn

2. dispatcher seeds _loaded_deferred ContextVar (Layer 6) for this
   session — starts as empty set

3. dispatcher._resolve_tools(agent_profile, …) → list[AgentTool]
   → agent_tools(toolset=…, source=req.source, only_available=True)
     → walks Layers 2/3/4/5 (filter_for + sidecar gating)
     → does NOT walk Layer 6 (defer is handled later, per provider
       call)

4. dispatcher computes deferred catalog text from the *initial* set
   → injects "deferred tools available via ToolSearch:" block into
     system prompt
   → NOTE: the tools list passed to agent_loop is still the full
     list including deferred tools — agent_loop does the per-call
     split before each provider request

5. agent_loop runs the inner tool-call loop. Each provider call:
   → split_tools_for_dispatch(context.tools) → (provider_tools, _)
     - non-deferred + deferred-already-loaded → provider_tools
     - deferred-not-loaded → omitted from provider_tools
   → provider receives provider_tools as its `tools=[]` field

6. when LLM emits ToolCall(name="bash"), agent_loop:
   → looks up AgentTool by name from context.tools
     (or via _registry if not found in current list)
   → validates arguments against the schema
   → await agent_tool.execute(call_id, args, cancel, on_update)

7. if the LLM called tool_search(select="cron"):
   → tool_search.execute mutates _loaded_deferred (adds "cron")
   → next iteration of step 5 includes cron in provider_tools
     → cron's full schema is now in the next request
   → LLM can call cron normally
```

## Where each piece lives

```
openprogram/functions/_runtime.py
  AgentTool subclass (from openprogram.agent.types)
  _registry                                            exposure source
                                                       (Layer 2: exposed =
                                                       registered & not
                                                       expose=False) [REVISED]
  _toolset_membership, _unsafe_in_channel               Layer 2b/3 data
  register / get / all_tools / filter_for / reset_registry
  _build_and_register_tool                              shared helper
  function decorator                                    user-facing
  ToolReturn dataclass                                  optional return type
  _normalize_result, _cap_result_text                   truncation
  _persist_full_result                                  落盘
  _effective_max_chars, _current_context_window_chars   dynamic ceiling
  _TailAccumulator                                      streaming tail
  _parse_docstring, _build_parameters_schema            schema autoderive
  _evaluate_approval, tool_requires_approval           approval hook
  _loaded_deferred (ContextVar)                        Layer 6 state
  install_loaded_deferred, mark_deferred_loaded
  split_tools_for_dispatch                              Layer 6 partition
  deferred_catalog_text                                Layer 6 prompt block
  tool_search (the AgentTool itself)                   Layer 6 loader

openprogram/functions/_helpers.py
  is_available (legacy dict, kept for older callers)
  is_available_agent_tool                              consolidates the
                                                       Layer 4 triad

openprogram/functions/__init__.py
  DEFAULT_TOOLS, TOOLSETS                              Layer 2 presets
  agent_tools, apply_tool_policy                       resolution API
  get_agent_tool, list_registered_agent_tools,
  list_available
  side-effect imports of every subpackage              @function tools register
                                                       at import time

openprogram/functions/<name>/<name>.py                  one per tool
  @function on a plain def                             (for the 38 leaf
                                                       tools shipped today)

openprogram/agentic_programming/function.py
  class agentic_function                               class decorator
    __init__ / __call__ / _attach                      attach path
    _register_as_tool                                  bridge to shared
                                                       registry
    _make_wrapper (sync + async variants)              DAG-aware wrapper
  _build_agentic_tool_spec                              schema builder
                                                       (filters runtime
                                                       params, hidden
                                                       input_meta)
  _registry (file-local)                                instance-lookup
                                                       table for
                                                       spawn_program /
                                                       webui

openprogram/agent/dispatcher.py
  install_loaded_deferred(...)                         called at session
                                                       start
  agent_tools(toolset=, source=, only_available=True)  Layer 2-5
  split_tools_for_dispatch + deferred_catalog_text    Layer 6 prompt
                                                       block

openprogram/agent/agent_loop.py
  per-provider-call split_tools_for_dispatch          Layer 6 enforcement
                                                       (Mid-loop loaded
                                                       schemas appear on
                                                       the next call)

openprogram/functions/agentics/*/__init__.py           @agentic_function
                                                       modules (each its
                                                       own directory).
                                                       Includes harness
                                                       symlinks
                                                       GUI-Agent-Harness,
                                                       Research-Agent-Harness,
                                                       Wiki-Agent-Harness.
```

## Test invariants (what the suite locks down)

The unit suite (``tests/unit/test_tools_runtime.py``,
``tests/unit/test_dispatcher_tools.py``) covers:

- Docstring + signature → parameters schema
- Sync / async fn dispatch
- Exception → AgentToolResult(is_error=True) wrap
- Char-cap truncation + persist_full
- on_update callback delivery + tail accumulator
- cancel event propagation
- timeout (asyncio.wait_for)
- requires_approval evaluation
- Registry filter (toolset, source, names)
- All shipped @function tools register at package import
- @function with overrides (name / description / toolset)
- Layer 1 (available_if) skips registration on False / exception
- Layer 6 defer sidecar + tool_search promotes to provider list +
  unknown name handling + catalog text format
- @agentic_function registers as AgentTool by default (as_tool=True)
- @agentic_function(as_tool=False) skips shared registry
- @agentic_function(register_globally=False) skips shared registry
  but still attaches `_agent_tool`
- @agentic_function(available_if=lambda: False) returns raw fn

666 tests passing as of the last refactor.

## What "we won't touch this anymore" means [REVISED]

The original doc froze the registry/decorator/dispatcher boundary. The
revision **deliberately reopened ONE part of it** — Layer 2 exposure —
because the static whitelist diverged from the cited frameworks and
broke plugins. That change is now part of the frozen baseline. The rest
of the boundary stays frozen.

Future work that **doesn't** require touching the boundary:

- Adding new @function tools (write the function + decorate; it is
  exposed by default — no whitelist edit)
- Adding new @agentic_function harnesses (same)
- Hiding an internal helper from the LLM (`expose=False` kwarg only)
- Defining a named subset (TOOLSETS dict) or letting the user define one
  (Functions-page folder → functions_meta.json)
- Flagging tools defer / available_if (kwarg only)
- Wiring MCP servers / plugins — they register AgentTool entries the
  normal way and are exposed on registration (mark `defer=True` for
  large MCP surfaces)

Future work that **would** require touching the boundary (defer unless
necessary):

- Adding a new gating layer beyond the ones above
- Changing AgentTool.execute signature
- Splitting / merging the shared registry
- Replacing the deferred-loading mechanism with something other than
  ToolSearch

### Open implementation deltas (doc ahead of code)

The doc now describes the target; these code points still need to catch
up (tracked for the P1–P4 work):

- `_exposed_set()` (`functions/__init__.py`) still returns the static
  `set(TOOLSETS["full"]["tools"])` → change to collect exposed tools
  from the registry (everything not `expose=False`).
- `expose=` kwarg not yet on the decorators → add; default True; the
  internal helpers (`_pick_stage`, `write_section`, `_merge_*`, …) set
  `expose=False`.
- Functions page: per-tool off-toggle exists; **folders-as-categories
  (`functions_meta.json`) not built yet** — mirror the Programs page.
- `runtime.exec` default is already `"full"` (done); the pure-choice
  callers (`_pick_stage`/`_stage_step`/`ask_user`/`_conclusion`) should
  pass `toolset="none"`.
