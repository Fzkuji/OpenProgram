# 函数元数据

本文档定义本框架中的函数必须携带哪些元数据（无论它是否调用 LLM）、这些元数据存放在何处，以及哪些组件会消费它们。

适用范围：所有用 `@agentic_function` 装饰的函数，以及任何传入 `render_options` 或决策菜单协议其他组件的普通 Python 可调用对象。（`runtime.exec(tools=[...])` 不接受普通可调用对象——条目必须是 `@agentic_function`、`{"spec", "execute"}` 字典，或带有 `.spec`/`.execute` 的对象；其他任何形式都会抛出 `TypeError`。）

## 1. 为什么需要这份规范

历史上，同一份信息曾散落在多个位置：

- 参数描述既可以写在 docstring 的 `Args:` 段落里，也可以写在 `@agentic_function(input={...})` 中
- 决策菜单的调用方在调用处手写一份冗长的 `available` 注册字典，重复了函数上已有的信息
- 不同的消费方（tool_use spec、WebUI、决策菜单、meta 工具）遵循的读取顺序约定略有差异

结果是：新函数的作者不知道该把描述写在哪里，框架从不一致的来源读取元数据，而生成的代码也并不总能与其他消费方的预期一致。

本规范定义了单一可信来源，让每个消费方都按相同的规则读取元数据。

## 2. 谁会消费函数元数据

| 消费方 | 所需字段 |
|---|---|
| `runtime.exec(tools=[fn])`（provider 原生 tool_use） | 函数名、整体描述、参数 JSON schema（type / required / enum / description） |
| `render_options(options)`（决策菜单） | 函数名、何时选用的描述、参数 name / type / description / enum，以及每个参数是否由系统填充 |
| `parse_args(reply, options, runtime, ...)`（决策抽取 + 派发 + 重试） | 参数名 + 类型 + enum + hidden 标志，以及 `runtime` 式的自动注入白名单 |
| WebUI 参数表单 | description / placeholder / multiline / options / hidden |
| `runtime.exec` 渲染出的上下文 | docstring——以 `metadata.doc` 携带，并作为前缀拼入渲染出的上下文文本。默认 system prompt 来自 `runtime.system`（外加 skills 区块），而非来自 docstring。 |
| 会话 DAG 渲染（`render_context`） | `expose` 模式 + `render_range` |
| auto-trace / 持久化 | 函数身份、参数值、返回值、耗时 |

## 3. 元数据字段及其规范位置

每一份信息都恰好只有一个可信来源。下表即为规范：

| 字段 | 存放位置 | 如何读取 |
|---|---|---|
| 函数名 | `def name(...)` | `fn.__name__` |
| 参数名 | 签名 | `inspect.signature(fn).parameters` |
| 参数类型 | 注解 | `param.annotation` |
| 参数默认值 | 注解默认值 | `param.default` |
| 一行摘要（做什么 / 何时选用） | docstring 的第一段（直到第一个空行为止） | `inspect.getdoc(fn)`，取第一段 |
| 单次调用的 LLM 指令（针对某一次具体 `runtime.exec` 的 prompt + 数据） | 该次 exec 调用的 `content=[...]` 列表 | 在调用时直接传入 |
| 单个参数的描述 | `@agentic_function(input={"x": {"description": ...}})` | `fn.input_meta["x"]["description"]` |
| 单个参数的枚举值 | `@agentic_function(input={"x": {"options": [...]}})` | `fn.input_meta["x"]["options"]` |
| 该参数是否对 LLM 可见 | `@agentic_function(input={"x": {"hidden": True}})` | `fn.input_meta["x"]["hidden"]` |
| WebUI 占位符 | `@agentic_function(input={"x": {"placeholder": "..."}})` | `fn.input_meta["x"]["placeholder"]` |
| WebUI 多行输入 | `@agentic_function(input={"x": {"multiline": True}})` | `fn.input_meta["x"]["multiline"]` |
| 动态选项来源 | `@agentic_function(input={"x": {"options_from": "functions"}})` | `fn.input_meta["x"]["options_from"]` |
| 工作目录选择器模式 | `@agentic_function(workdir_mode="optional"\|"hidden"\|"required")`——由装饰器校验并存储在实例上 | `fn.workdir_mode`。其消费方是 WebUI，它不会反射对象——而是对源码文本做 AST 解析（`openprogram/webui/_functions.py:_extract_workdir_mode`），因此该值必须在装饰器调用中写成字面量 |
| 框架自动注入的参数 | 分布在两个文件中的两个常量，值均为 `{"runtime", "exec_runtime", "review_runtime"}`：`agentic_programming/function.py` 中的 `_RUNTIME_PARAMS`（注入 + 从 tool-spec 中过滤）和 `agentic_programming/decision.py` 中的 `_AUTO_PARAMS`（菜单隐藏 + 派发） | 模块级 |
| 覆盖 `runtime.exec` 的 system prompt | `@agentic_function(system="...")` | `fn.system` |
| DAG expose 模式 | `@agentic_function(expose="io"\|"llm"\|"full"\|"hidden")`——控制**调用方**在其 DAG 渲染中能看到本函数的哪些内容 | `fn.expose` |
| DAG 渲染范围 | `@agentic_function(render_range={"callers": N, "subcalls": M})`——控制**本函数自身**的 `runtime.exec` 会读取多少 DAG 历史。两者都是基于 `seq` 的节点数切片：`callers` = 在本函数帧开始**之前**写入的最近 N 个节点（默认 `None` = 不设上限，`0` = 隔绝所有先前上下文）；`subcalls` = 自本函数帧开始**以来**写入的最近 N 个节点（默认 `-1` = 不设上限——帧能看到自身的进展；子函数的内部内容是由*它们自己*的 `expose` 设置隐藏的，而非由 subcalls 计数隐藏；仅当需要在循环中主动限制 prompt 体积时才设 `N>=0`）。 | `fn.render_range` |
| Skill 触发词 / agent 发现 | 同级的 `SKILL.md` frontmatter | 由 skill 加载器单独加载 |

**核心原则**：凡是可通过签名 / 注解表达的内容，就不在装饰器中重复；凡是可通过 `input=` 表达的内容，就不在 docstring 中重复。

装饰器还接受共享的工具注册 / 门控 kwargs（`name`、`description`、`toolset`、`unsafe_in`、`requires_approval`、`available_if`、`defer` 等），详见 `docs/reference/design/function/function-calling-unification.md`；`cache` / `cache_ttl`（按 name + args 记忆化结果）和 `timeout`（N 秒后强行终止函数体并返回一个错误结果）的行为与 `@function` 中一致。

### 一个裸 `@agentic_function` 的有效默认值

| 方面 | 默认值 | 由此产生的行为 |
|---|---|---|
| `expose` | `"io"` | 调用方能看到我的 name + input + output。我内部的 `runtime.exec`（`llm` Calls）对它们隐藏。 |
| `render_range` | `None` → `render_context` 回落到 `callers=None, subcalls=-1` | 帧之前的历史不设上限。帧内节点（帧自身的进展：先前的 `runtime.exec` 结果、返回的子函数 io）同样不设上限。子 `@agentic_function` 的内部内容之所以保持隐藏，是因为子函数携带 `expose="io"`，而非因为 subcalls 对其做了裁剪。 |
| 顶层对话轮次 | `frame_entry_seq=-1`，无帧前内容 | 与任何其他帧走相同的代码路径——所有节点都属帧内且可见。没有特殊处理。 |
| tools | 完整工具集 | 既不传 `tools=` 也不传 `toolset=` 的裸 `runtime.exec` 默认解析出完整的注册表工具集——工具是开启的。传 `tools=[...]` 给出显式菜单；传 `toolset="none"` 或 `tools=[]` 才是无工具的纯推理调用。工具体内嵌套的 `exec` 会通过 `_current_tools` contextvar 继承外层的 `tools=` 列表。 |
| `system` | `None` | 原样使用运行时现有的 system prompt。 |

由此可知：一个帧会自然地累积自身的工作。仅当需要在长循环中约束 prompt 体积时，才显式设置 `subcalls=N`。仅在（少见的）需要完全隔绝帧内节点时，才显式设置 `subcalls=0`。

## 4. docstring 与 `content` 各自的角色

这两条通道可以携带相互重叠的信息，但它们承担**不同的职责**——彼此都无法替代对方：

| 通道 | 作用范围 | 这里该写什么 |
|---|---|---|
| docstring | 整个函数层面。描述函数作为一个整体做什么（它可能包含预处理、若干次 LLM 调用以及后处理）。供人类、决策菜单、tool_use spec 和 meta 工具读取。 | 必须有一行摘要。也可以详细描述每一次 LLM 调用做什么、预期输出、边界情况——想写多详细就写多详细，供读者与上下文使用。 |
| `runtime.exec(content=[...])` | 函数内部某一次具体的 LLM 调用。每次 `exec` 都是它自己的一次“提问”；函数可以用不同的 prompt 发起多次。 | *这一次* LLM 调用真正的 prompt + 数据：什么任务、什么输出格式、什么约束，以及待处理的数据。**即便 docstring 已经描述过，这里也必须写。** |

一个函数可以有一段详尽的 docstring，说明“本函数通过询问 LLM 并对回复做归一化来分类情感”，但函数体仍然需要一个显式的 `runtime.exec(content=[...])`，其中包含真正发给 LLM 的指令 + 数据。**docstring 中的文档不会传递进 LLM 调用**——框架把 docstring 作为描述性上下文发送（以 `metadata.doc` 携带在 DAG 节点上，并渲染进内层调用的 situation prompt），而非作为权威指令。各 provider 的行为各异；codex CLI 尤其会忽略作为指令的 docstring。务必把每次调用的 prompt 放进 `content`。

docstring 撰写规则：

- 一行摘要（第一段）是必需的——这正是决策菜单和 tool_use spec 所读取的内容。
- 正文可以写到对代码读者有用的详细程度——包括描述每一次 LLM 调用做什么。
- 不要写填充性内容（“You are a helpful assistant”、“Complete the task”）。
- 详尽的 docstring 并不能免去在每次 exec 调用上写显式 `content=[...]` 文本的必要。

`content` 撰写规则：

- 每一项都是形如 `{"type": "text", "text": ...}` 或 `{"type": "image", "path": ...}` 的字典。
- 把这次 LLM 调用的指令*与*数据都嵌进去。示例：
  ```python
  runtime.exec(content=[{"type": "text", "text": (
      f"Classify the sentiment of the following text. Reply with exactly one "
      f"word: positive, negative, or neutral.\n\nText:\n{text}"
  )}])
  ```
- 在行内定义确切的输出格式；不要指望 docstring 或外部上下文来传达它。

## 5. `input=` 与 docstring 的 `Args:` 段落

历史上，同一份参数描述既可能出现在 docstring 的 `Args:` 段落，也可能出现在 `@agentic_function(input={...})` 中。本规范将 `input=` 定为可信来源。

### 推荐写法（新代码必须采用）

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

更完整的示例，演练了更多元数据特性（placeholder、multiline、hidden、混合类型）：

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
        # 由系统提供；LLM 看不到它。
        "hidden": True,
    },
})
def review_essay(
    essay: str,
    rubric_id: str,
    max_score: int,
    show_rubric_internals: bool,
    session_id: str,           # 由 Python 通过上下文填充，而非 LLM
    runtime: Runtime,          # 自动注入
) -> dict:
    """Score an essay against a named rubric and return a structured report."""
    ...
```

docstring 只保留一行摘要——**没有 `Args:` 段落，也没有 `Returns:` 段落**。如果返回值字段的含义对下游 LLM 驱动的调用很重要，就用结构化返回类型（例如 `TypedDict` 或 dataclass）来编码它，以便消费方能够反射它；不要在 docstring 段落里重复这套 schema。

### 旧写法（仍受支持）

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

旧函数仍然能运行，但 `Args:` 段落是死文本——**框架中任何地方都不存在 docstring-`Args:` 解析器**（见 §10）。`_build_agentic_tool_spec` 的回落顺序如下：

```
fn.input_meta[name]["description"]
    ↓ 未找到
fn.input_meta[name]["placeholder"]  (渲染为 "e.g. {placeholder}")
    ↓ 未找到
无描述；仅有参数名 + 类型
```

`render_options` 只从 `input_meta` 读取 `description` 和 `options`；缺少二者的参数只会得到 name + type。

## 6. 普通 Python 可调用对象（未装饰）

决策菜单路径并不强制要求 `@agentic_function`：普通可调用对象可以传给 `render_options`（也可作为 `decision.make` / `choices=` 的选项），但它们携带的元数据更少。它们**不能**传给 `runtime.exec(tools=[...])`——对于任何既不是 `@agentic_function`、也不是 `{"spec", "execute"}` 字典、又不带 `.spec`/`.execute` 的对象，`_adapt_tools` 都会抛出 `TypeError`。

| 字段 | 已装饰 | 未装饰 |
|---|---|---|
| 参数描述 | 来自 `input=` | 空（不存在 docstring 回落） |
| 参数枚举值 | `input={"x": {"options": [...]}}` | 无 |
| Hidden 标志 | `input={"x": {"hidden": True}}` | 仅 `_AUTO_PARAMS` 中的名字（`runtime` 等）会被自动隐藏 |
| 是否记录进会话 DAG | 是 | 否 |

在以下场景使用普通可调用对象：参数很少的简单决策分支、不暴露 WebUI 界面、且不需要丰富的菜单提示。一旦你需要枚举值、隐藏参数或详细描述，就立刻升级到 `@agentic_function`。

## 7. 自动注入的参数

下列参数名按约定保留：如果某函数的签名包含其中任何一个，框架会在调用时自动注入它们。LLM 看不到它们，也不需要填写。

| 参数名 | 含义 |
|---|---|
| `runtime` | 当前的 Runtime 实例 |
| `exec_runtime` | 用于执行的运行时（多运行时设置） |
| `review_runtime` | 用于评审的运行时（多运行时设置） |

这些名字存放在两个文件中的两个常量里：`agentic_programming/function.py` 中的 `_RUNTIME_PARAMS`（运行时注入 + 从 tool spec 中过滤）和 `agentic_programming/decision.py` 中的 `_AUTO_PARAMS`（从决策菜单中隐藏 + 派发）。要新增一个自动注入的名字，需同时改这两处。不要在单个调用处用 `input={"x": {"hidden": True}}` 来标记它们。

## 8. WebUI 渲染行为

WebUI 不会反射运行中的 Python 对象：它对源文件（`openprogram/webui/_functions.py`）做 AST/正则解析。因此 `input=` 必须在装饰器调用中写成字面量字典，才能在表单中显示出来——动态构建的元数据（变量、辅助函数调用）对 WebUI 是不可见的。

WebUI 表单按下列规则渲染每个参数（实现见 `web/components/chat/composer/fn-form/fn-form.tsx` 和 `fn-form-fields.tsx`）。在编写 `@agentic_function(input={...})` 时，用下表来预判你的函数会生成哪种输入控件：

| 参数特征 | WebUI 控件 |
|---|---|
| `bool` 类型 | Yes / No 切换按钮（不是复选框） |
| `str` 类型，未设 `multiline` | 默认为 textarea（隐含 `multiline=True`） |
| `str` 类型，`multiline: False` | 单行 `<input>` |
| 非 `str` 非 `bool`，未设 `multiline` | 单行 `<input>` |
| `options: ["a", "b", ...]` | 可点击的 chips，外加一个自由文本输入框（用户可选预设值或输入自定义值） |
| `options_from: "functions"` | 由当前已注册的非内建 / 非 meta 函数填充的 `<select>` 下拉框 |
| `hidden: True` | 完全从表单中省略 |
| 有 Python 默认值且无显式 `placeholder` | 原始默认值成为占位符的灰字提示（无 `"default: "` 前缀）；在空字段中按 Tab 会把它提升为实际值；`None` 或以 `_` 开头的默认值会被抑制 |

工作目录选择器单独渲染在参数行上方，由顶层的 `workdir_mode` 控制：

| `workdir_mode` 取值 | 表单行为 |
|---|---|
| `"optional"`（默认） | 显示选择器；可以留空 |
| `"hidden"` | 不显示选择器（函数不依赖文件系统位置） |
| `"required"` | 显示选择器且为必填；未填值时表单阻止提交 |

参数的 `description` 渲染为参数名旁的小标签；类型注解显示在同一行，非必填参数会带一个 “optional” 标记（没有 “required” 标记）。

## 9. 不在范围内（推迟到未来扩展）

下列字段曾被讨论，但**现在有意不引入**，因为尚不存在任何消费方：

| 字段 | 预期用途 | 为何推迟 |
|---|---|---|
| `effects=["fs", "net", "state"]` | 标记函数副作用，用于权限门控 / 危险操作拦截 | 装饰器上已存在一个门控界面（`requires_approval`、`check_fn`、`unsafe_in`、`available_if`、`defer`）；在其之上再加一个声明式的 `effects=` 字段仍属未来工作 |
| `permissions=[...]` | 调用前所需的权限范围 | 同上 |
| `idempotent=True` | 函数是否可安全重试 | 不存在自动重试组件 |
| `latency_hint="long"` | 调度器提示 | 不存在调度器 |
| `cost_hint=...` | 配额管理 | 同上 |

一旦出现真正的上游消费方，再重新审视本节。

## 10. 迁移路径（仅供参考）

将既有代码迁移到本规范的推荐顺序；并非严格要求：

1. 在 `agentic_programming/function.py` 中添加一个共享辅助函数 `_parse_docstring_args(fn) -> dict[name, description]`
2. 让 `_build_agentic_tool_spec` 调用该辅助函数作为回落（目前 spec 完全不读取 docstring 的 `Args:`）
3. 让 `render_options` 调用同一个辅助函数作为同样的回落
4. 既有的 `@agentic_function` 函数无需立即改写；在你顺手触及它们时再随机迁移

## 11. 风格检查清单

编写一个新的 `@agentic_function` 时：

- [ ] docstring 第一段是一行摘要（直接说明函数做什么 / 何时选用它）
- [ ] docstring 中没有 `Args:` 或 `Returns:` 段落（除非你确实出于调试 / 阅读目的想要它们）
- [ ] 每个对 LLM 可见的参数在 `input=` 中都有 `description`
- [ ] 枚举参数在 `input=` 中使用 `options`（而非埋在描述文本里）
- [ ] 由系统填充的参数（DB session、当前用户等）标记 `hidden: True`
- [ ] 框架自动注入的参数（`runtime` 等）无需注解；框架会自行检测它们
- [ ] 函数名清晰（`fn.__name__` 就是 LLM 看到的动作名）
- [ ] docstring 中没有角色扮演、没有空洞指令、没有比喻

## 12. 参考资料

- `openprogram/agentic_programming/function.py` —— `@agentic_function` 装饰器实现
- `openprogram/agentic_programming/decision.py` —— 选项菜单渲染、回复解析，以及下一步决策原语（`decision.make`、`render_options`、`parse_args`、`DecisionError`）
- `docs/capabilities/agentic-programming/writing-functions/agentic-function.md` —— 装饰器使用指南
- `docs/reference/design/function/function-calling-unification.md` —— 函数 / 工具调用框架
- `docs/capabilities/agentic-programming/choosing-the-next-step/tool-calling.md` —— 单轮原生 tool-use 循环机制
