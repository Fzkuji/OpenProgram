# 下一步决策（decision.make / exec(choices=)）

本文档描述 OpenProgram 的**下一步决策**机制：一个 agentic
function 把"接下来发生什么"交给 LLM —— 给它一组选项，它选出一个，框架直接把这个选择解析成"下一步的结果"。该机制是一条独立于
provider 原生 tool call 的路径。

它位于框架的 `openprogram/agentic_programming/decision.py`。
两个入口共享同样的选项形态和解析逻辑：

- `decision.make(prompt, options)` —— 纯决策；模型不做任何工作，
  只负责选。
- `runtime.exec(..., choices=options)` —— 模型先跑完一整轮
  （推理、tool call），只有收尾这一步才是决策。

`decision.make` 需要一个 runtime 来发起模型调用，但这个 runtime
会从 `_current_runtime` ContextVar 自动取得。只有当调用链上某个函数声明了
runtime 类参数（`runtime` / `exec_runtime` / `review_runtime`）时，该
ContextVar 才会被设置 —— 一个没有声明该参数的入口
`@agentic_function` 会让 `decision.make` 抛出 `RuntimeError`。
所以在函数上声明 `runtime=None` 但你不需要把它往下传；只有在
agentic function 之外才显式传入 `runtime=`。

## 与原生 tool call 的对比

| | 原生 tool call | 下一步决策（本机制） |
|---|---|---|
| 选项如何到达模型 | provider 协议的 `tools` 字段 | prompt 内的文本菜单 |
| 模型如何表达它的选择 | 协议层的结构化 `ToolCall` | 回复正文里的一段 JSON |
| 由谁解析 | provider / agent_loop | `decision.py` 自身 |
| 选项能否是非函数 | 不能，必须是 tool | 能，支持 value 选项 |
| 依赖 | provider 的 tool-use 支持 | 无，纯文本即可 |

当你不想依赖 provider 的 tool-use 支持，或者需要"一个不是函数的选项"时，选择本机制 ——
一个直接返回值的决策（通常是像 `done` / `escalate`
这样的路由标记）。

## 入口一：`decision.make` —— 纯决策

在一个 `@agentic_function` 内部，只调用一次 `decision.make` —— 不传
runtime，也不写 `if`：

```python
from openprogram.agentic_programming import agentic_function, decision

@agentic_function
def route_message(msg: str, runtime=None) -> str:
    return decision.make("Pick one way to handle this message.", {
        "analyze":  analyze_sentiment,        # 一个函数
        "fallback": fallback_reply,           # 一个函数
        "done":     "CONVERSATION_OVER",      # 一个值
    })
```

`decision.make` 渲染菜单、调用模型、解析回复，然后
**把选择直接解析成下一步的结果**：

- LLM 选了一个函数 → 该函数执行（带上解析出的 + 注入的参数），返回它的返回值。
- LLM 选了一个值 → 原样返回该值。

两种情况返回的都是"下一步的结果"本身。调用方从不检查"选中了哪一个"，也从不按类型分支 ——
决策本身就是分支，所以没有 `if` 要写。

## 入口二：`runtime.exec(choices=...)` —— 先干活，最后决策

更常见的需求：模型先跑完一整轮（推理、tool call，无论这活需要什么），而
**收尾**的返回必须是一个决策。使用 `exec` 的 `choices=` 参数：

```python
@agentic_function
def handle_ticket(ticket: str, runtime=None) -> dict:
    """Read the ticket, look things up, then decide which flow to route to."""
    return runtime.exec(
        f"Handle this ticket: {ticket}",
        toolset="default",          # 之前：模型用 tool 做调研、执行命令
        choices={                   # 收尾：返回必须是其中之一
            "refund":    issue_refund,
            "escalate":  escalate_to_human,
            "close":     {"status": "closed"},
        },
    )
```

`exec(choices=...)` 做的事：它把选项菜单加上一条"先干活，最后用一段 JSON
做选择"的指令（`DECISION_FINISH_INSTRUCTION`）拼接进
prompt，然后跑一轮正常的 exec —— 来自 `tools` /
`toolset` 的 tool 照常被调用，模型照常推理。在这一轮的末尾，模型的最终回复必须是一段
`{"call": ...}` JSON，`exec` 用 `resolve_decision`
解析它：被选中的函数执行并返回其结果，被选中的值则被返回。

不带 `choices` 的 `exec` 返回原始回复文本；带 `choices` 时它返回解析后的决策结果。`decision.make(prompt, options)`
等价于一个没有前置工作的 `exec(choices=options)` —— 但有一处微妙差别：只有
`exec(choices=)` 会追加 `DECISION_FINISH_INSTRUCTION`；
`decision.make` 只发送你的 prompt 加菜单，所以你自己的 prompt
必须告诉模型去选。

## 选项容器

每个选项的形态都像一个 tool：它有名字、描述，以及一个
**payload schema**。三种选项类型：

| 选项类型 | 被选中时 | schema 来自 |
|---|---|---|
| 函数选项 | 执行该函数，返回它的返回值 | 函数签名 |
| 值选项 | 返回那个固定的值 | 无 |
| schema 选项 | 模型按 schema 填充结构化数据；返回 `{"decision": name, **filled fields}` | 你显式声明的 schema |

`options` 可以是 dict 或 list。

**dict 形式** `{name: handler}`，其中 key 是选项名：

```python
decision.make("...", {
    "retry":     retry_fn,                            # 函数选项
    "skip":      "SKIPPED",                           # 值选项
    "abort":     (AbortSignal(), "pick when stuck"),  # 值选项 + 描述
    "emit_plan": ("Produce a plan.", {                # schema 选项：("description", schema)
        "steps": [{"action": str, "target": str}],
        "rationale": str,
    }),
})
```

**list 形式** —— 每一项要么是一个 callable，要么是一个 `(callable, "description")`
元组，要么是一个字符串选项形态（`"name"` / `("name", "description")` /
`("name", "description", schema)`）。在 list 形式下，函数选项的名字是该函数的
`__name__`。

### schema 结构

一个 schema 是 `{field_name: field_type}`，且字段类型**递归嵌套**，所以单个选项可以让模型返回任意结构的
JSON：

| 写法 | 含义 |
|---|---|
| `field: str`（任意 Python 类型） | 该类型的一个标量 |
| `field: "description"` | 一个带描述的 `str` 标量 |
| `field: [subschema]` | 一个列表，其每个元素都匹配 `subschema` |
| `field: {subfield: ...}` | 一个嵌套对象（key 是 subfield 名） |
| `field: {"type": T, "description": ..., "options": [...]}` | 带 type/description/enum 的元描述 |

三点注意：list schema 必须**恰好**包含一个元素模板 ——
`[str, int]` 会抛 `TypeError`；一个所有 key 都落在
`{type, description, options, fields, items}` 之内的 dict 会被解析成元规格，而不是嵌套对象（嵌套对象至少需要一个落在该集合之外的
key）；还有，元组在 handler 位置是保留语法 —— 一个字面量 2-元组值选项会被误解析为
`(value, "description")`。

解析之后，`parse_args` 会针对 schema **递归校验**类型和嵌套；`render_options`
渲染出的 `Call:` 示例也带上了嵌套占位符的形态。这让"有限分支的选择"与
tool call 对齐 —— 每个分支都可以携带任意结构的
payload。如果需求是"完全没有分支，永远返回相同结构"，就用单选项的
`decision.make`，或者干脆用
`exec(response_format=...)`。

## 内部步骤

### 1. `render_options` 渲染菜单

对每个选项它会输出：签名 `name(param: type, ...)`、描述、逐参数细节，以及一行
`Call:` JSON 示例。只展示 `source="llm"` 参数 —— `runtime` / `context`
注入的参数对 LLM 隐藏。如果某个参数声明了 `options`
（enum），细节里会列出允许的取值。`Call:`
示例中的占位符值是原生 JSON 字面量（`0` / `false` / `[]` / `{}` /
`"<str>"`）。

### 2. 调用模型

`decision.make` 直接做一次 `runtime.exec(prompt + menu)`；
`exec(choices=)` 把菜单拼进它本来就要发送的那一轮里。

### 3. `parse_args` 解析并校验

- `extract_action` 从一个 ```` ```json ```` 代码块或裸文本中挖出带
  `call` key 的 JSON。`call` key 有别名
  `action` / `function` / `tool`，其中任何一个都被接受。
- `call` 不在注册表里 → `_ParseError("unknown_call")`。
- `_validate_field` 逐字段校验：类型
  （`str/int/float/bool/list/dict`；`bool` 不算作 `int`，`float`
  接受 `int`）、enum（`options`）。
- 函数选项：按签名从 `context` dict 填充 `source="context"`
  参数，注入 `runtime` 类参数，丢弃签名之外的字段，检查必填项（那些没有默认值的）。
- 值/文本选项：每个声明的 schema 字段都是必填的；schema 之外被幻觉出来的字段会被丢弃。
- 返回 `(chosen, kwargs)` —— 对函数选项，`chosen`
  是原始函数；对值/文本选项，它是名字字符串。

### 4. 解析失败时重试

如果任一步骤抛出 `_ParseError`，`parse_args` 会重试（默认
`max_retries=1`，设为 0 可禁用；`max_retries` 只能在
`decision.make` / `parse_args` 上设置 —— `exec(choices=)`
这条路径固定为一次重试）：它用 `runtime.exec` 把"上一次回复 + 出错原因 + 重新渲染的菜单"发回给
LLM 让它重新选。这次重试和其他任何模型调用一样，照常落进
DAG。当所有重试都用尽 → 抛出 `DecisionError`，携带最后一次的错误类型、消息以及回复的开头部分。

`DecisionError` 是 `ValueError` 的子类（旧的 `except ValueError`
代码仍能捕获它），而调用方可以用 `except DecisionError`
精确地捕获"模型始终没有产出有效选择"，而不会误捕不相关的
`ValueError` —— 例如一个 planner 把它当作"这一步结束了"。框架止步于"抛出一个清晰的异常"；捕获之后做什么是调用方的事，没有内置兜底。

### 5. `resolve_decision` 解析成结果

如果 `chosen` 是函数，就运行 `chosen(**kwargs)` 并返回结果；如果它是字符串，就在值表里查出该值并返回（一个声明了
schema 的值选项返回 `{"decision": name, **kwargs}`）。

## 与 tool-call 循环的关系

本机制不与 `tool-calling.md` 中描述的 `agent_loop.py`
的 tool-call 循环冲突 —— 它们是"让模型选下一步"的两个并行实现。一个
`@agentic_function` 既可以作为 `exec(tools=[...])`
的原生 tool，也可以作为一个决策选项 —— 同一个函数，两条调用路径。用哪一条取决于：你是否想依赖
provider 的 tool use、某个选项是否需要是值而不是函数，以及每次决策和重试是否都应该是一个可追踪的
DAG 节点。
