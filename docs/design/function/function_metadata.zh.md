# 函数元数据规范

> 本文档是 [`function_metadata.md`](function_metadata.md) 的中文翻译，仅供阅读参考。**所有规范以英文版为准**——当两份内容不一致时，请按英文版执行；翻译版可能滞后。

本文档定义"在这个框架里写一个函数（不管是否调用 LLM）时，需要承载哪些元数据、写在哪里、由谁消费"。

适用范围：所有用 `@agentic_function` 装饰的函数；以及未被装饰、但被传给 `render_options` / `runtime.exec(tools=[...])` / catalog 协议任意一个组件的普通 Python callable。

## 1. 为什么需要这份规范

历史上同一份信息散落在多处：

- 参数描述既可以写在 docstring 的 `Args:` 段，也可以写在 `@agentic_function(input={...})` 里
- catalog 调用方在调用点手写 `available` registry dict，重复声明已经在函数定义里有的信息
- 不同消费者（tool_use spec、WebUI、catalog 菜单、meta/create）各自约定不同的读取顺序

结果是：写新函数的人不确定该把描述写在哪，框架内部读元数据时来源也不统一，meta/create 生成的代码风格跟其他消费者期望的不完全一致。

这份规范的目标是定一份单一来源（source-of-truth），让所有消费者按同一套规则读元数据。

## 2. 谁消费函数的元数据

| 消费者 | 需要哪些字段 |
|---|---|
| `runtime.exec(tools=[fn])`（provider 原生 tool_use） | 函数名、整体描述、参数 JSON schema（type / required / enum / description） |
| `render_options(options)`（catalog 菜单） | 函数名、何时选、参数名/类型/描述/枚举、参数是否系统填 |
| `parse_args(action, fns, ...)`（catalog 派发） | 参数名、是否 hidden、`runtime` 等自动注入名单 |
| WebUI 参数输入表单 | description / placeholder / multiline / options / hidden |
| `runtime.exec` 默认 system prompt | docstring 全文 |
| Context tree 渲染 | `expose` 模式 + `render_range` |
| meta/create / edit / fix / improve | docstring 风格、签名约束、`input=` 元数据 |
| auto-trace / 持久化 | 函数标识、参数值、返回值、时长 |

## 3. 元数据字段及其唯一归属

每条信息只能有一个 source-of-truth。下面这张表是规范：

| 数据 | 写在哪 | 读出的方式 |
|---|---|---|
| 函数名 | `def name(...)` | `fn.__name__` |
| 参数名 | signature | `inspect.signature(fn).parameters` |
| 参数类型 | annotation | `param.annotation` |
| 参数默认值 | annotation 默认值 | `param.default` |
| 函数一句话总结（"干啥 / 何时选"） | docstring 第一段（到第一个空行） | `inspect.getdoc(fn)` 取首段 |
| 函数执行细节指令（LLM 内部 system prompt） | docstring 全文 | `inspect.getdoc(fn)` |
| 每参数描述 | `@agentic_function(input={"x": {"description": ...}})` | `fn.input_meta["x"]["description"]` |
| 每参数枚举值 | `@agentic_function(input={"x": {"options": [...]}})` | `fn.input_meta["x"]["options"]` |
| 参数是否对 LLM 可见 | `@agentic_function(input={"x": {"hidden": True}})` | `fn.input_meta["x"]["hidden"]` |
| 参数 WebUI placeholder | `@agentic_function(input={"x": {"placeholder": "..."}})` | `fn.input_meta["x"]["placeholder"]` |
| 参数 WebUI 多行输入 | `@agentic_function(input={"x": {"multiline": True}})` | `fn.input_meta["x"]["multiline"]` |
| 参数动态选项来源 | `@agentic_function(input={"x": {"options_from": "functions"}})` | `fn.input_meta["x"]["options_from"]` |
| 工作目录选择器模式 | `@agentic_function(workdir_mode="optional"\|"hidden"\|"required")` | `fn.workdir_mode` |
| 框架自动注入参数 | 全局常量 `_AUTO_PARAMS = {"runtime", "exec_runtime", "review_runtime"}` | 模块级 |
| runtime.exec system prompt 覆盖 | `@agentic_function(system="...")` | `fn.system` |
| context 暴露程度 | `@agentic_function(expose="io"\|"full"\|"hidden")` | `fn.expose` |
| context 渲染范围 | `@agentic_function(render_range={"depth": ..., "siblings": ...})` | `fn.render_range` |
| 是否给 runtime 工具集 | `@agentic_function(no_tools=True)` | `fn.no_tools` |
| skill trigger 词 / agent 发现 | 同目录 `SKILL.md` frontmatter | skill loader 单独加载 |

**核心原则**：能在 signature / annotation 里表达的，不在装饰器里重复；能用 `input=` 装饰器表达的结构化信息，不在 docstring 里重复。

## 4. docstring 的角色

docstring 既是给读源码的人看的，也是 LLM 在多个环节会读到的。它的责任是：

- **第一段（首段）**：一句话总结，回答"这个函数干啥 / 何时该调它"。被 catalog 菜单当"何时选"，被 tool_use spec 当 description，被 meta/create 当生成函数的目标。
- **正文**：详细行为指令。被 `runtime.exec` 当默认 system prompt 注入到 LLM 调用里。可以说明输出格式、约束、注意事项。

docstring 写法约束：

- 不要扮演角色（不要 "You are a helpful assistant"）
- 不要空指令（不要 "Complete the task"）
- 不要重复 content 里已经有的数据
- 输出格式要在 docstring 里精确定义，不留给 LLM 猜

## 5. `input=` 与 docstring `Args:` 段的关系

历史上同一份参数描述既会写在 docstring 的 `Args:` 段，又会写在 `@agentic_function(input={...})` 里。本规范把 `input=` 定为 source-of-truth。

### 推荐风格（新代码必须用）

最小示例：

```python
@agentic_function(input={
    "text":  {"description": "Text to polish."},
    "style": {"description": "Output style.", "options": ["academic", "casual"]},
})
def polish(text: str, style: str, runtime: Runtime) -> str:
    """Polish a text in the given style."""
    ...
```

涉及更多元数据特性的完整示例（placeholder、multiline、hidden、混合类型）：

```python
@agentic_function(input={
    "essay": {
        "description": "Essay to review.",
        "placeholder": "Paste the essay text here...",
        "multiline": True,
    },
    "rubric_id": {
        "description": "Which rubric to apply.",
        "options": ["ielts_writing", "toefl_writing", "gre_argument"],
    },
    "max_score": {
        "description": "Upper bound for the numeric score.",
    },
    "show_rubric_internals": {
        "description": "Include rubric breakdown in the output.",
    },
    "session_id": {
        # System-supplied; LLM does not see this.
        "hidden": True,
    },
})
def review_essay(
    essay: str,
    rubric_id: str,
    max_score: int,
    show_rubric_internals: bool,
    session_id: str,           # filled by Python via context, not LLM
    runtime: Runtime,          # auto-injected
) -> dict:
    """Score an essay against a named rubric and return a structured report."""
    ...
```

docstring 只保留一句话总结，**不写 `Args:` 段，也不写 `Returns:` 段**。返回值的字段含义如果对 LLM 链式调用重要，请用结构化返回类型（例如 `TypedDict` 或 dataclass）让消费者自查；不要写自然语言段落。

### 旧风格（兼容保留）

```python
@agentic_function
def polish(text: str, style: str, runtime: Runtime) -> str:
    """Polish a text in the given style.

    Args:
        text: Text to polish.
        style: Output style.
        runtime: LLM runtime.

    Returns:
        Polished text.
    """
    ...
```

旧风格函数仍然能跑——`render_options` 和 `_build_agentic_tool_spec` 在 `input_meta` 没给某参数描述时，会 fallback 解析 docstring `Args:` 段。fallback 顺序：

```
fn.input_meta[name]["description"]
    ↓ 找不到
docstring Args 段里 name 的描述（Google-style）
    ↓ 找不到
fn.input_meta[name]["placeholder"]（前缀 "e.g."）
    ↓ 找不到
无描述，菜单只显示参数名 + 类型
```

## 6. 普通 Python 函数（未装饰）的待遇

`@agentic_function` 不是强制要求。普通 callable 也能进 `render_options` 和 `runtime.exec(tools=[...])`，只是元数据缺：

| 字段 | 装饰过 | 未装饰 |
|---|---|---|
| 参数描述 | `input=` | docstring `Args:` fallback，没就是空 |
| 参数枚举 | `input={"x": {"options": [...]}}` | 不存在；annotation 写 `Literal["a","b"]` 可识别 |
| hidden | `input={"x": {"hidden": True}}` | 只 `_AUTO_PARAMS` 名单里的（`runtime` 等）自动 hidden |
| context tree 记录 | 是 | 否 |

普通函数适用于：决策分支简单、参数少、不需要 WebUI 展示、不需要 catalog 菜单复杂提示的临时函数。一旦需要 enum / hidden / 详细参数描述，就升级为 `@agentic_function`。

## 7. 已自动注入的参数

下面这些参数名是约定保留的，框架会自动从运行时填入；用户函数签名里出现就自动注入，不出现就不注入。LLM 不会看到、不需要填：

| 参数名 | 含义 |
|---|---|
| `runtime` | 当前 Runtime 实例 |
| `exec_runtime` | 执行用的 Runtime（多 runtime 场景） |
| `review_runtime` | 审查用的 Runtime（多 runtime 场景） |

需要新增自动注入名时，在 `agentic_programming/function.py` 的 `_AUTO_PARAMS` 里加；不要靠 case-by-case 在 `input={"x": {"hidden": True}}` 里手标。

## 8. WebUI 渲染行为

WebUI 表单按以下规则渲染每个参数（在 `openprogram/webui/static/js/sidebar.js` 实现）。函数作者写 `@agentic_function(input={...})` 时应据此判断会得到什么样的输入控件：

| 参数特征 | WebUI 控件 |
|---|---|
| `bool` 类型 | Yes / No 切换按钮（不是复选框） |
| `str` 类型，未指定 `multiline` | 默认渲染为 textarea（`multiline=True`） |
| `str` 类型，`multiline: False` | 单行 `<input>` |
| 非 `str` / 非 `bool`，未指定 `multiline` | 单行 `<input>` |
| `options: ["a", "b", ...]` | 一组可点击 chips + 一个 "自填" 文本框（用户可选可填） |
| `options_from: "functions"` | `<select>` 下拉，选项来自当前注册的非内置 / 非 meta 函数列表 |
| `hidden: True` | 完全不在表单出现 |
| 有 Python 默认值且未显式给 `placeholder` | placeholder 自动设为 `"default: X"` |

工作目录选择器在表单底部独立渲染，行为受顶层 `workdir_mode` 控制：

| `workdir_mode` 值 | 表单行为 |
|---|---|
| `"optional"`（默认） | 显示工作目录选择器，可空可填 |
| `"hidden"` | 不显示工作目录选择器（函数本身不依赖文件系统位置） |
| `"required"` | 显示工作目录选择器，必填，不填不让提交 |

参数描述（`description`）渲染在参数名旁的小字标签里；type 注解和"required" 标记也展示在参数名行。

## 9. 不在本规范范围内（暂留作未来扩展）

以下字段在评审中被讨论过但**目前不引入**，原因是没有现成消费者：

| 字段 | 设想用途 | 不引入的原因 |
|---|---|---|
| `effects=["fs", "net", "state"]` | 标记函数副作用，给权限网关 / 危险操作拦截用 | 还没有权限网关组件 |
| `permissions=[...]` | 调用前需要的权限 scope | 同上 |
| `idempotent=True` | 能否安全重试 | 当前没有自动重试器 |
| `latency_hint="long"` | 调度提示 | 当前没有调度器 |
| `cost_hint=...` | 配额管理 | 同上 |

等真有上游消费者出现，再回头扩这一节。

## 10. 落地步骤（参考）

下面是把现有代码迁移到这套规范的建议顺序，不在本规范强制：

1. 在 `agentic_programming/function.py` 加共用 helper `_parse_docstring_args(fn) -> dict[name, description]`
2. `_build_agentic_tool_spec` 调这个 helper 做 fallback（目前 spec 完全不读 docstring Args）
3. `render_options` 同样调这个 helper 做 fallback
4. 把 `meta/_helpers.py` 第 657 行起的"docstring rules"文档块改成 `input=` 优先、`Args:` 段降级为可选
5. 同步 `meta/create.md` 和其他 meta 函数引用 docstring 风格的地方
6. 现有 `@agentic_function` 不需要立刻改写；后续接触到时顺手改成新风格

## 11. 风格速查

新写 `@agentic_function` 时检查：

- [ ] docstring 第一段是一句话总结（直接陈述函数做什么 / 何时该调）
- [ ] docstring 没有 `Args:` 或 `Returns:` 段（除非有特殊调试 / 阅读需要）
- [ ] 每个 LLM 可见参数在 `input=` 里有 `description`
- [ ] 枚举参数用 `input=` 的 `options`（不是写在描述里）
- [ ] Python 自动填的参数（DB session、当前用户等）标 `hidden: True`
- [ ] 框架自动注入（`runtime` 等）参数不需要任何标注，框架自动识别
- [ ] 函数名清楚（`fn.__name__` 会被 LLM 当 action 名称）
- [ ] 没用比喻 / 没扮角色 / 没空指令的 docstring

## 12. 参考

- `openprogram/agentic_programming/function.py` — `@agentic_function` 装饰器实现
- `openprogram/programs/functions/buildin/build_options.py` — catalog 菜单渲染
- `openprogram/programs/functions/buildin/parse_args.py` — catalog 决策抽取 + 派发 + 重试
- `openprogram/programs/functions/buildin/_retry_choice.py` — `parse_args` 内部重试 helper
- `openprogram/programs/functions/meta/_helpers.py` — meta 函数生成约束
- `docs/design/function/agentic_function.md` — 装饰器使用指南
- `docs/design/function/function_calling/llm_call.md` — 原生 tool_use 协议
