# Next-step decision making(decision.make / exec(choices=))

本文档描述 OpenProgram 里的**下一步决策**机制:agentic 函数把"下一步做什么"交给 LLM 决定——给它一组选项,它选一个,框架把这个选择直接解析成"下一步的结果"。这套机制和 provider 原生的 tool call 是两条独立的路。

实现在框架内的 `openprogram/agentic_programming/decision.py`。两个入口,共用同一套选项形态和解析:

- `decision.make(prompt, options)` —— 纯决策,模型不干活、直接挑。
- `runtime.exec(..., choices=options)` —— 模型先跑一个完整 turn(推理、调工具),收尾才是一个决策。

`decision.make` 需要 runtime 去发模型调用,但 runtime 从 `@agentic_function` 装饰器设好的 `_current_runtime` ContextVar 自动取,所以在 agentic 函数内部调它不用传 runtime;在 agentic 函数外面调才需要显式传 `runtime=`。

## 和原生 tool call 的区别

| | 原生 tool call | 下一步决策(本机制) |
|---|---|---|
| 选项怎么给模型 | provider 协议的 `tools` 字段 | prompt 里的一段文本菜单 |
| 模型怎么表达选择 | 协议层结构化 `ToolCall` | 回复正文里的一段 JSON |
| 谁解析 | provider / agent_loop | `decision.py` 自己解析 |
| 选项能不能是非函数 | 不能,必须是工具 | 能,支持值选项 |
| 依赖 | provider 支持 tool use | 无,纯文本即可 |

选这套机制的场景:不想依赖 provider 的 tool use 支持;或者需要"选项不是函数"——一个直接返回某个值的决策(典型是 `done` / `escalate` 这类路由标记)。

## 入口一:`decision.make` —— 纯决策

`@agentic_function` 里调一次 `decision.make`,不传 runtime、不写任何 `if`:

```python
from openprogram.agentic_programming import agentic_function, decision

@agentic_function
def route_message(msg: str) -> str:
    return decision.make("挑一个方式处理这条消息。", {
        "analyze":  analyze_sentiment,        # 一个函数
        "fallback": fallback_reply,           # 一个函数
        "done":     "CONVERSATION_OVER",      # 一个值
    })
```

`decision.make` 渲染菜单、调模型、解析回复,然后**把选择直接解析成下一步的结果**:

- LLM 选了函数 → 该函数被执行(带上解析+注入好的参数),返回它的返回值。
- LLM 选了值 → 该值原样返回。

两种情况都返回"下一步的结果"本身。调用方不检查"选的是哪个"、不按类型分支——决策本身就是分支,所以没有 `if` 要写。

## 入口二:`runtime.exec(choices=...)` —— 先干活、再决策

更常见的需求是:模型先跑一个完整 turn(推理、调工具、该干什么干什么),**收尾时**的 return 才是一个决策。用 `exec` 的 `choices=` 参数:

```python
@agentic_function
def handle_ticket(ticket: str) -> dict:
    """读工单、查资料、然后决定派给哪个流程。"""
    return runtime.exec(
        f"处理这个工单:{ticket}",
        toolset="default",          # 前面:模型用工具查资料、跑命令
        choices={                   # 收尾:return 必须是这里选一个
            "refund":    issue_refund,
            "escalate":  escalate_to_human,
            "close":     {"status": "closed"},
        },
    )
```

`exec(choices=...)` 做的事:把选项菜单和一句"先干活、最后用 JSON 挑一个收尾"的指令(`DECISION_FINISH_INSTRUCTION`)拼进 prompt,然后跑正常的 exec turn——`tools` / `toolset` 给的工具该调调、模型该推理推理。turn 结束时模型的最终回复必须是一个 `{"call": ...}` JSON,`exec` 用 `resolve_decision` 把它解析掉:选了函数就执行返回结果,选了值就返回值。

`exec` 不带 `choices` 时返回原始回复文本;带 `choices` 时返回解析后的决策结果。`decision.make(prompt, options)` 等价于"没有前置工作"的 `exec(choices=options)`。

## 选项容器

每个选项像一个 tool:有名字、描述,以及一份**负载 schema**。三种选项:

| 选项类型 | 选中后 | schema 来自 |
|---|---|---|
| 函数选项 | 执行函数,返回它的返回值 | 函数签名 |
| 值选项 | 返回那个固定值 | 无 |
| schema 选项 | 模型按 schema 填一份结构化数据,返回 `{"decision": 名字, **填好的字段}` | 你显式声明的 schema |

`options` 可以是 dict 或 list。

**dict 形式** `{名字: handler}`,key 是选项名:

```python
decision.make("...", {
    "retry":     retry_fn,                       # 函数选项
    "skip":      "SKIPPED",                      # 值选项
    "abort":     (AbortSignal(), "无法继续时选"),  # 值选项 + 描述
    "emit_plan": ("产出一份计划。", {              # schema 选项:("描述", schema)
        "steps": [{"action": str, "target": str}],
        "rationale": str,
    }),
})
```

**list 形式**——每项是可调用对象、`(callable, "描述")`、或字符串选项形态(`"name"` / `("name", "描述")` / `("name", "描述", schema)`)。list 形式函数选项的名字取函数 `__name__`。

### schema 的结构

schema 是 `{字段名: 字段类型}`,字段类型可以**递归嵌套**,这样一个选项就能让模型返回任意结构化 JSON:

| 写法 | 含义 |
|---|---|
| `字段: str` (任意 Python 类型) | 该类型的标量 |
| `字段: "描述"` | 带描述的 `str` 标量 |
| `字段: [子schema]` | 列表,每个元素匹配 `子schema` |
| `字段: {子字段: ...}` | 嵌套对象(键名是子字段名) |
| `字段: {"type": T, "description": ..., "options": [...]}` | 带类型/描述/枚举的元描述 |

`parse_args` 解析后会按 schema **递归校验**类型与嵌套结构;`render_options` 渲染的 `Call:` 示例也带嵌套占位形状。这让"有限分支选择"和 tool calling 对齐——每个分支带任意结构的负载。需求若是"压根不分支、永远返回同一份结构",就用单选项的 `decision.make`,或直接 `exec(response_format=...)`。

## 内部步骤

### 1. `render_options` 渲染菜单

对每个选项输出:签名 `name(参数: 类型, ...)`、描述、逐参数明细、一行 `Call:` JSON 示例。只显示 `source="llm"` 的参数——`runtime` / `context` 注入的参数对 LLM 隐藏。参数若声明了 `options`(枚举),明细里列出可选值。`Call:` 示例的占位值是 JSON 原生字面量(`0` / `false` / `[]` / `{}` / `"<str>"`)。

### 2. 调模型

`decision.make` 直接 `runtime.exec(prompt + 菜单)`;`exec(choices=)` 是把菜单拼进本来就要发的那次 turn。

### 3. `parse_args` 解析与校验

- `extract_action` 从 ```` ```json ```` 代码块或裸文本里抠出带 `call` 键的 JSON。`call` 键有别名 `action` / `function` / `tool`,任一都接受。
- `call` 不在 registry → `_ParseError("unknown_call")`。
- `_validate_field` 逐字段校验:类型(`str/int/float/bool/list/dict`,`bool` 不当 `int`,`float` 接受 `int`)、枚举(`options`)。
- 函数选项:按签名补 `source="context"` 参数(从 `context` dict 取)、注入 `runtime` 类参数、丢掉签名外的多余字段、检查必填(签名无默认值的)。
- 值/文本选项:声明的 schema 字段全部必填,丢掉 schema 外的幻觉字段。
- 返回 `(chosen, kwargs)`——函数选项的 `chosen` 是原函数;值/文本选项的 `chosen` 是名字字符串。

### 4. 解析失败重试

任一步抛 `_ParseError`,`parse_args` 走重试(默认 `max_retries=1`,设 0 关闭):用 `runtime.exec` 把"上次回复 + 错误原因 + 重渲染的菜单"发给 LLM 让它重选。这次重试也是一次模型调用,照样落进 DAG。重试全部用尽仍失败 → 抛 `DecisionError`,带最后一次错误类型、消息、回复头部。

`DecisionError` 继承 `ValueError`(老的 `except ValueError` 仍能接住),调用方可以 `except DecisionError` 精确捕获"模型始终没选出合法选项"这一种情况、不误伤无关的 `ValueError`——例如让某个 planner 把它当作"结束这一步"。框架到"抛清晰异常"为止,接住后怎么收场是调用方的事,框架不内置任何 fallback。

### 5. `resolve_decision` 解析成结果

`chosen` 是函数就 `chosen(**kwargs)` 执行并返回结果;`chosen` 是字符串就在值表里查出对应值返回(值选项若声明了 schema,则返回 `{"decision": 名字, **kwargs}`)。

## 与 tool call 循环的关系

这套机制和 `tool-calling.md` 里 `agent_loop.py` 的 tool call 循环不冲突,是并列的两种"让模型选下一步"的实现。`@agentic_function` 既可以作为 `exec(tools=[...])` 的原生工具,也可以作为决策选项——同一个函数,两套调用路径。选哪套取决于:要不要依赖 provider tool use、要不要"选项是个值而不是函数"、要不要把每次决策和重试都作为可追溯的 DAG 节点。
