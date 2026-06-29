# Function calling

LLM 如何从列表中挑选一个函数、框架如何运行它，以及运行结果如何作为模型
下一轮的输入回灌。关于逐步推进的循环机制（LLM 如何在一次
``runtime.exec`` 调用内挑选下一个工具），参见
``docs/agentic-programming/tool-calling.md``。

主导原则是 **default-on, user-curated**（默认开启、由用户裁剪）：一个已注册的
工具无需任何配置即可使用；用户从这里开始收窄范围。
暴露由注册驱动（一个已注册的工具默认可见，除非它通过 `expose=False`
选择退出），而一次裸 `runtime.exec` 会拿到完整的暴露集合。工具调用结果只
存在于运行历史中，永远不会进入后续的 prompt 上下文，因此宽泛的暴露不带来
上下文开销。

## On the wire

与业界所称的 "tool use"（OpenAI / Anthropic / Gemini API 中的 ``tools=[]`` /
``tool_calls=[]``）是同一个概念。我们把这个*动作*称为
"function calling"（作者编写一个函数，把它暴露给
LLM），但 *API 请求里的那个东西*仍然叫 ``tool``，以与 SDK
术语保持一致。

```
我们(编写姿势)                       LLM API wire / providers/types.py
─────────────────────────────────────────────────────────────────
@function 装饰器                       Tool / ToolCall / ToolResultMessage
@agentic_function 装饰器               tools=[...] 字段
agent_tools() / get_agent_tool() …    tool_calls=[...] 字段
```

边界就是 **the wire format**（线上格式）：providers 把注册表中的每个
``AgentTool`` 序列化成 API 的 ``Tool`` JSON 形状；模型的
``tool_calls`` 返回后，按名字与我们的
注册表匹配，然后运行 ``AgentTool.execute(...)``。我们的包装类
（``AgentTool`` / ``AgentToolResult``）携带了线上格式所没有的运行时附加项
（sidecar 门控、sync→async、字符上限等）。

## Two decorators, one registry

作者注册一个 LLM 可调用函数恰好有两种方式：

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

两个装饰器最终都在同一个共享注册表
（``openprogram.functions._runtime._registry``）中产生一个 ``AgentTool``
条目。``_build_and_register_tool`` 辅助函数是
"构建 AgentTool + 挂载 sidecar + 注册" 的单一事实来源。两个装饰器都
委托给它；新增一个 sidecar 属性或门控层意味着
只需修改这一个辅助函数，两个装饰器都会随之生效。

关于为什么是两个装饰器（而非一个）、以及为什么
``@agentic_function`` 是一个类（而非函数）的设计理由，参见
下文 "Why two decorators"。

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

expose                      Layer 2 — exposure opt-OUT. Default
                            True: a registered tool is visible to
                            the model. Set False for internal helpers
                            that Python calls but the LLM must never
                            see (e.g. _pick_stage, write_section, the
                            _merge_* leaves).

toolset                     Layer 2b — preset membership (Hermes-
                            style: tool also goes into the "research"
                            preset, etc.). A preset is a NAMED SUBSET
                            for callers that want fewer than all
                            exposed tools; it is not the visibility
                            gate (expose is).
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

## The gating layers

每一轮的工具选择都会经过这些过滤器（骨架取自
Claude Code 的 `tools.ts`）。Layer 2 是由注册驱动的暴露
（与 Claude Code / Hermes 一致）。

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
                  for internal helpers           callable but never in
                            — everything else exposed       any LLM tools array.
                            simply by being registered      DEFAULT is exposed;
                            (plugins / MCP included).        no allowlist needed.

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

端到端的默认行为：一个已注册的工具是 **on** 的（Layer 2
已暴露），除非其作者设置了 `expose=False`；一次裸 `runtime.exec`
会拿到 **完整的暴露集合**。框架不会按调用做限制——
收窄是 **调用方** 的选择：一个 agent profile（Layer 3）、
按调用的 allow/deny、Functions 页的关闭开关（Layer 5），或者
当某次具体调用完全不想要工具时使用 `toolset="none"`。默认
始终是 "给工具"；明确知道自己不需要工具的调用方才选择退出。

Layer 1–5 表示 "LLM 无法看到/使用这个工具"。Layer 6 表示
"LLM 在 catalog 中看到名字，但必须选择性地加载 schema"——它是
唯一让 LLM 自己选择拉取哪些工具的层（用于在需要前把
庞大的 MCP/plugin 工具集挡在 prompt 之外）。

## Per-layer default policy

主导原则是 **default-on, user-curated**：一个刚
注册的工具无需任何配置即可使用；每一层的
默认都是 "不限制"。各层分为两类——

- **Active（选择）层** 决定 *用什么*。默认 =
  "所有已暴露的"。用户/调用方主动选入一个更窄的集合。
  这是常规路径。
- **Passive（否决）层** 决定 *什么不能用*。默认 =
  "什么都不否决"。它们只做减法，作为一种安全/维护
  兜底，除非某个条件触发否则保持休眠。

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
                                                     work with fewer tools, or
                                                     toolset="none" when this
                                                     call wants no tools.

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

**L2b（active）对比 L5（passive）——为什么两者都存在、为什么不合并。** 它们
看起来像是 "用户两次去碰工具"，但其实是相反的操作：L2b 是
用户/调用方 *选择一个要用的集合*（加法，一个文件夹 = "今天的
工具箱"）；L5 是 *拉黑/封顶*（减法，"这个工具
坏了 / 这个会话不允许用它"）。否决必须独立于
选择——例如，无论当前激活哪个文件夹，attended-mode 都必须扣下
`ask_user_question`；你不能指望用户记得
把它从每一个文件夹里都排除掉。因此 **Functions 页把 L2b 作为
主操作呈现（组织并挑选文件夹），把 L5 作为例外
（按工具的关闭开关）**；系统自身的 L5 否决（attended、
subagent 上限）仅存在于代码中，对用户不可见。

## Plugins and MCP servers

因为暴露由注册驱动（Layer 2），一个 plugin 或 MCP
server 只需 **注册它的工具** 就能使工具可用——与
Claude Code 和 Hermes 一致。不存在编辑某个中心
allowlist 的第二步。具体来说：

- plugin 的工具通过与内置工具相同的 `_build_and_register_tool`
  路径注册（plugin 调用 `@function` / 注册一个
  `AgentTool`），因此它们落进 `_registry` 并默认被暴露。
- MCP-server 的工具在连接时被注册为 `AgentTool` 条目，并默认
  标记 `defer=True`（Layer 6），因此它们的名字出现在
  deferred catalog 中，LLM 通过 `tool_search` 按需加载
  schema——这在不让工具变得不可见的前提下，把
  庞大的 MCP 面挡在每个 prompt 之外。
- 应保持内部使用的 plugin/MCP 工具仍可设置
  `expose=False`；用户仍可在 Functions
  页（Layer 5）关闭其中任何一个。不过默认是 "注册即可用"。

## Tool profiles (Functions page)

一个 **tool profile** 是一个命名配置，表示 "这次对话
启用哪些工具"。Functions 页（`/functions`）
管理 profiles；聊天输入框让用户挑选使用哪个 profile。

### Concepts

```
tool catalog          all registered, exposed tools — a flat read-only
(the shelf)           list on the Functions page. Shows every tool with
                      its name + description. The catalog itself has no
                      enable/disable controls; it just shows what exists.

tool profile          a named set of tools to use — like a shopping cart
(the cart)            built from the catalog. Each profile starts with
                      ALL tools (default-on); the user removes what they
                      don't want for this scenario.

                      Operations on a profile:
                        • remove a tool (take it out of this config)
                        • add a tool (put it back — pick from "not yet
                          in this profile" list)
                        • rename / delete the profile

default profile       the built-in "all tools on" profile. Always
                      exists, cannot be deleted, contains every exposed
                      tool. Used when no other profile is selected.
```

### User flow

1. **Functions 页** 展示 catalog（所有工具）和一个
   profiles 侧边栏。点击某个 profile 会显示它包含哪些工具；
   用户从该 profile 中添加/移除工具。
2. **聊天输入框** 有一个 profile 选择器（例如模型选择器旁边的
   下拉菜单）。选择一个 profile = 这次对话使用该
   工具集。默认 = "all tools"。
3. profile 名在任何接受 ``toolset=`` 的地方都能解析（Layer
   2b）。``agent_tools(toolset="research")`` 返回
   "research" profile 中的工具。Agent profiles（``agent.json``）可在其
   ``tools.toolset`` 字段中按名字引用一个 tool profile。

### Storage

Profiles 持久化在 ``functions_meta.json`` 中（与
``programs_meta.json`` 同一位置），形状如下：

```json
{
  "profiles": {
    "default": ["bash", "read", "write", ...],   // immutable = all exposed
    "research": ["web_search", "web_fetch", "read", "write", "bash"],
    "safe": ["read", "glob", "grep", "web_search"]
  },
  "active": "default"   // which profile the chat composer is using
}
```

新建一个 profile = 复制 "default"（所有工具）。用户随后
移除该场景下不需要的工具。

### Relationship to Layer 5 (global disable)

一个 profile 表示 "这次对话使用这些工具"（L2b，主动
选择）。按工具的全局禁用（L5，``tools.disabled``）
仍作为一个独立的、很少使用的兜底存在：如果一个工具被全局
禁用，它会从每一个 profile 中自动移除（
解析管道在 L2b 之后应用 L5）。但用户的
主操作是 profile 管理，而非按工具的全局开关。

## User-editable entry points

```
Entry point                     Controls                     Layer  State  Persisted in
────────────────────────────────────────────────────────────────────────────────────────
Functions page —                create / edit / delete tool  L2b    ⬜     functions_meta.json
  tool profiles                 profiles (named tool sets).                 → profiles: {name:[...]}
                                Add/remove tools to/from a
                                profile. Default profile =
                                all tools on.

Chat composer —                 pick which tool profile to   L2b    ⬜     session state
  profile picker                use for this conversation.                  (sent per-turn with
  (Tools toggle → expand        Expand the "Tools" chip to                  tools_override)
   → profile list)              see available profiles +
                                select one. Default = all.

Chat composer —                 per-turn toggles: Tools      L2b    ✅     per-message
  "+" menu toggles              on/off + Web Search on/off   /L5           (tools_override)

Agent profile                   per-agent toolset / enabled  L2b    ✅     ~/.openprogram/
  (tools field)                 / disabled / allowed.        + L5          agents/<id>.json
                                Can reference a tool profile                → tools: {...}
                                by name (toolset="research")

Attended / unattended           withhold ask_user_question   L5     ✅     session state
  switch (CLI/TUI/web)          when no human is watching          (system  (attended.py)
                                                                    auto)

Global tool disable             blacklist a single tool      L5     ✅     config.json:
  (Functions page / config)     everywhere — rarely used.                   tools.disabled
                                Overrides any profile.

Author decorator kwargs         expose / available_if /      L1/2/  ✅     in-code
  @function(...)                defer / toolset / unsafe_in  3/6
                                / check_fn
```

日常使用 = Functions 页上的 **tool profiles**（创建 profiles，
添加/移除工具）+ 聊天输入框里的 **profile 选择器**（选择
这次对话使用哪个 profile）。全局禁用 = 很少使用的
兜底。Agent profile = 面向高级多 agent 配置的按 agent 覆盖。
Author kwargs = 框架内部机制。

## Four knobs none of the reference frameworks have

在 6 层级联之外，框架还增加了四个运行时旋钮，
Claude Code、Hermes、OpenClaw 都没有：

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

这两个装饰器包装的是不同 *种类的工作*：

- **@function** 包装确定性的 Python 代码。其函数体在每次 LLM
  tool_call 时运行一次并返回结果。内部没有 LLM 轮次。
  示例：``bash`` 运行子进程，``web_search`` 调用一个 API，
  ``read`` 读取一个文件。被装饰的函数 **只** 由 LLM
  通过 dispatcher 调用——没有 Python 代码会直接
  ``bash("ls")``。因此让装饰器把 Python
  名字 **替换** 为 ``AgentTool`` 对象是安全的（装饰后
  原函数从模块命名空间中消失）。

- **@agentic_function** 包装 "一个内部 agent 循环"——其函数体
  自己通过 ``runtime.exec(...)`` 运行一个 LLM，并可能递归调用其它
  ``@agentic_function``。这些函数既由
  LLM 调用，**也** 直接被 Python 调用——一个
  ``@agentic_function`` 通常组合若干其它的，例如
  ``research_pipeline`` 以普通 Python 调用 ``survey_topic`` →
  ``generate_ideas`` → ``rank_ideas``。因此被装饰的名字必须
  **仍是一个 Python 可调用对象**。我们不能像 @function 那样把它
  替换为 ``AgentTool``。

因此：@agentic_function 是一个 **类装饰器**。被装饰的
名字变成一个类实例，它：

- 拥有 ``__call__``，使得 ``research("topic")`` 运行 wrapper
  （同步地或作为协程，与原函数匹配）
- 拥有一个 sidecar ``_agent_tool``，引用一个已在
  共享注册表中注册的 ``AgentTool``
- 拥有方法（``.execute``、``.spec``）和属性
  （``.expose``、``.render_range``、``._fn``、``._wrapper``），供
  其它代码（``spawn_program``、webui、DAG 可视化器）读取

两个装饰器都向同一个共享
注册表贡献 ``AgentTool`` 条目，因此 dispatcher / agent_loop / provider adapter
只跟 ``AgentTool`` 打交道——它们不区分这两个
装饰器。这种拆分在注册表层之后是不可见的。

原则上同样的逻辑也可以做成单个带
``mode="leaf" | "agentic"`` 标志的类装饰器，但那会把真正的
语义差异藏进一个标志里。两个装饰器让选择在
调用点显式化：叶子上用 ``@function``，agentic 函数体上用 ``@agentic_function``。

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
                                                       expose=False)
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

单元测试套件（``tests/unit/test_tools_runtime.py``、
``tests/unit/test_dispatcher_tools.py``）覆盖：

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

## Stable boundary

注册表/装饰器/dispatcher 边界是稳定的。**不会** 触及它的
工作包括：

- 新增 @function 工具（编写函数 + 装饰；它默认
  被暴露——无需编辑白名单）
- 新增 @agentic_function harness（同上）
- 把一个内部 helper 对 LLM 隐藏（仅需 `expose=False` kwarg）
- 定义一个命名子集（TOOLSETS dict），或让用户自行定义一个
  （Functions 页文件夹 → functions_meta.json）
- 给工具打上 defer / available_if 标记（仅需 kwarg）
- 接入 MCP servers / plugins——它们以常规方式注册 AgentTool
  条目，并在注册时被暴露（对庞大的 MCP 面标记
  `defer=True`）

**会** 需要触及该边界的未来工作（除非必要否则推迟）：

- 在上述各层之外新增一个门控层
- 修改 AgentTool.execute 的签名
- 拆分 / 合并共享注册表
- 用 ToolSearch 之外的其它机制替换 deferred-loading 机制
