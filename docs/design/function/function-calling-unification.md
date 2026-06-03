# Function calling — design as built

Status: **implemented**. This document describes the function-calling
framework as it exists today, after the unification refactor. For
the moment-by-moment loop mechanics (how the LLM picks the next tool
to run inside one ``runtime.exec`` call), see ``tool-calling.md`` —
that companion doc is still accurate.

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

toolset                     Layer 2 — preset membership (Hermes-
                            style: tool goes into the "research"
                            preset, etc.)
unsafe_in                   Layer 2/3 — channel blacklist
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

## The 6 gating layers (Claude Code parity)

Tool selection per turn passes through up to 6 filters. We adopted
all 6 from Claude Code's design (their ``tools.ts``). Layers 2/3 are
sometimes merged into "preset selection"; the 6-way split is more
mechanistically clear.

```
Layer  When                  How configured                Effect when rejected
─────────────────────────────────────────────────────────────────────────────────
1   at import / decoration  @function(available_if=...)    tool never enters
                            @agentic_function(             _registry → invisible
                              available_if=...)            to dispatcher + ToolSearch
                                                            (equivalent to Claude
                                                            Code's `feature() ?
                                                            require() : []`)

2   process startup /       @function(toolset=[...])       not in any LLM-facing
    flat-list assembly      DEFAULT_TOOLS / TOOLSETS       preset by default
                            (Hermes-style includes chain)

3   per-session mode         agent_profile.toolset =       this session sees a
                            "safe" / "research" / …       narrower set than the
                                                          base preset

4   per-tool-list build      @function(check_fn=,         filtered out of this
    isEnabled-style          requires_env=, can_use=)     session's tools list
                            agent_tools(only_available=    when gate fails
                              True)

5   user/policy filter       agent_tools(deny=, allow=)    explicit subtraction /
                            agent_profile.disabled         intersection on names

6   prompt construction      @function(defer=True)         schema is NOT in
    schema-deferred                                        provider request;
                                                          tool name + 1-liner
                                                          appears in deferred
                                                          catalog in system
                                                          prompt; LLM must call
                                                          tool_search to load
                                                          schema before invoke
```

Layers 1–5 mean "the LLM cannot see this tool at all". Layer 6 means
"the LLM sees the name in a catalog but must opt-in to load the
schema". Layer 6 is the only one that **lets the LLM choose** what
to bring in.

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
  _registry, _toolset_membership, _unsafe_in_channel    Layer 2 data
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

## What "we won't touch this anymore" means

The framework is **frozen at the registry / decorator / dispatcher
boundary**. Future work that **doesn't** require touching this
boundary:

- Adding new @function tools (just write the function + decorate)
- Adding new @agentic_function harnesses (same)
- Adjusting which tools appear in which preset (TOOLSETS dict only)
- Flagging existing tools as defer / available_if (kwarg only)
- Wiring MCP servers (they'd add AgentTool entries via the same
  register() + presumably mark them defer=True by default)

Future work that **would** require touching this boundary (defer
unless absolutely necessary):

- Adding a 7th gating layer
- Changing AgentTool.execute signature
- Splitting / merging the shared registry
- Replacing the deferred-loading mechanism with something other
  than ToolSearch
