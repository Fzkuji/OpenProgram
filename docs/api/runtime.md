# Runtime

> Source: [`openprogram/agentic_programming/runtime.py`](../../openprogram/agentic_programming/runtime.py)

LLM 运行时。封装 LLM provider,自动从 session DAG 算上下文、调用 LLM、把回复写回 DAG。

---

## Class: `Runtime`

```python
class Runtime(call=None, model="default")
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `call` | `Callable \| None` | `None` | LLM provider 函数。签名：`fn(content: list[dict], model: str, response_format: dict) -> str`。如果不传，需要子类化并重写 `_call()` |
| `model` | `str` | `"default"` | 默认模型名称，每次调用可覆盖 |
| `max_retries` | `int` | `2` | exec() 最大尝试次数（包含首次调用，且必须 >= 1） |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `model` | `str` | 默认模型名称 |

---

## 方法

### `exec()`

```python
Runtime.exec(content, context=None, response_format=None, model=None,
             tools=None, toolset=None, tools_source=None, tools_allow=None,
             tools_deny=None, tool_choice="auto", parallel_tool_calls=True,
             max_iterations=20, choices=None) -> Any
```

调用 LLM,上下文从 session DAG 自动算出。

**在 `@agentic_function` 内部调用时:**
1. 从当前函数的 DAG 节点出发,`compute_reads` 按 `expose` / `render_range` 算出本次要读哪些历史节点
2. `render_dag_messages` 把这些节点渲染成 messages
3. 调用 `_call()` 发送请求
4. 把回复写成一个新的 `llm` 节点 append 到 DAG

**在 `@agentic_function` 外部调用时:** 直接调用 LLM,不算上下文、不写 DAG(退化成单轮调用)。

一个 `@agentic_function` 可以多次调用 `exec()`,每次都是 DAG 上的一个新 `llm` 节点。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `content` | `list[dict]` | *(必填)* | 内容块列表（见下方格式） |
| `context` | `str \| None` | `None` | 手动覆盖自动算出的上下文。`None` = 从 DAG 自动算 |
| `response_format` | `dict \| None` | `None` | 输出格式约束（JSON schema），传给 `_call()` |
| `model` | `str \| None` | `None` | 覆盖默认模型 |
| `tools` | `list \| None` | `None` | 本次调用 LLM 可用的工具。设了就跑工具循环直到模型返回纯文本 |
| `toolset` / `tools_source` / `tools_allow` / `tools_deny` | — | `None` | 工具集与策略过滤 |
| `tool_choice` | `str \| dict` | `"auto"` | `"auto"` / `"required"` / `"none"` / 强制某工具 |
| `parallel_tool_calls` | `bool` | `True` | 允许一轮多个工具调用 |
| `max_iterations` | `int` | `20` | 工具循环安全上限 |
| `choices` | `dict \| list \| None` | `None` | 设了则约束 turn 的**收尾**:模型跑完整 turn 后,最终回复必须从 `choices` 里选一个;`exec` 解析并返回该选择的结果。详见 [next-step-decision](../agentic-programming/choosing-the-next-step/next-step-decision.md) |

#### Content block 格式

```python
{"type": "text",  "text": "Find the login button."}
{"type": "image", "path": "screenshot.png"}
{"type": "audio", "path": "recording.wav"}
{"type": "file",  "path": "data.csv"}
```

#### 返回值

`str` — LLM 的回复文本。带 `choices` 时返回解析后的决策结果(选中函数的返回值,或选中值本身)。

#### 异常

- `RuntimeError` — 同一个 `@agentic_function` 内调用了两次
- `TypeError` — 传入了 async 的 call 函数（应使用 `async_exec()`）
- `NotImplementedError` — 没有配置 call 函数

---

### `async_exec()`

```python
await Runtime.async_exec(content, context=None, response_format=None, model=None) -> str
```

`exec()` 的异步版本。内部调用 `_async_call()`。

参数和行为与 `exec()` 相同。如果传入同步 call 函数，会自动适配（不报错）。

---

### `_call()`

```python
Runtime._call(content, model="default", response_format=None) -> str
```

实际调用 LLM 的方法。**子类化时重写此方法。**

#### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `content` | `list[dict]` | 完整的内容列表（context + 用户内容） |
| `model` | `str` | 模型名称 |
| `response_format` | `dict \| None` | 输出格式约束 |

#### 返回值

`str` — LLM 回复文本。

---

### `_async_call()`

```python
await Runtime._async_call(content, model="default", response_format=None) -> str
```

`_call()` 的异步版本。子类化时重写此方法以支持异步 provider。

---

## 使用方式

### 方式一：传入 call 函数

```python
from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

def my_llm(content, model="sonnet", response_format=None):
    # 把 content 转成你的 provider 格式，发请求
    texts = [b["text"] for b in content if b["type"] == "text"]
    return call_my_api("\n".join(texts), model=model)

runtime = Runtime(call=my_llm, model="sonnet")

@agentic_function
def observe(task):
    """Look at the screen."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Find: {task}"},
        {"type": "image", "path": "screenshot.png"},
    ])
```

### 方式二：子类化

```python
class AnthropicRuntime(Runtime):
    def __init__(self, api_key, model="sonnet"):
        super().__init__(model=model)
        self.client = anthropic.Anthropic(api_key=api_key)

    def _call(self, content, model="sonnet", response_format=None):
        messages_content = []
        for block in content:
            if block["type"] == "text":
                messages_content.append({"type": "text", "text": block["text"]})
        response = self.client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": messages_content}],
        )
        return response.content[0].text

runtime = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")
```

### 多个 Runtime 共存

```python
fast = Runtime(call=gemini_call, model="gemini-2.5-flash")
strong = Runtime(call=claude_call, model="sonnet")

@agentic_function
def observe(task):
    """Quick observation with cheap model."""
    return fast.exec(content=[...])

@agentic_function
def plan(goal):
    """Complex planning with strong model."""
    return strong.exec(content=[...])
```

---

## Retry 机制

`exec()` 和 `async_exec()` 内置自动重试，用于处理 LLM API 的临时性错误（网络超时、速率限制、服务器错误等）。

### 配置

```python
# 默认：最多尝试 2 次（首次调用 + 失败后再试一次）
rt = Runtime(call=my_llm, max_retries=2)

# 不重试（失败即抛异常）
rt = Runtime(call=my_llm, max_retries=1)

# 多次重试（适用于不稳定的 API）
rt = Runtime(call=my_llm, max_retries=5)
```

### 行为规则

| 情况 | 处理 |
|------|------|
| API 调用成功 | 返回结果 |
| API 抛出异常（非 `TypeError` / `NotImplementedError`） | 记录失败 attempt，然后继续重试，直到达到 `max_retries` |
| `TypeError` 或 `NotImplementedError` | 立即抛出，不重试（通常是 provider 实现或调用方式的问题） |
| 所有重试均失败 | 抛出 `RuntimeError`，并附上完整 attempt 报告 |

### 错误报告格式

当所有重试耗尽时,抛出的 `RuntimeError` 包含每次尝试的错误信息:

```
RuntimeError: exec() failed after 3 attempts in observe():
Attempt 1: ConnectionError: timeout
Attempt 2: RateLimitError: 429 Too Many Requests
Attempt 3: ConnectionError: timeout
```

### 重试的边界

`max_retries` 只处理 API 层面的瞬态故障(网络超时、速率限制等)。如果是函数本身的逻辑或输出格式有问题,重试解决不了——直接修改函数代码,参见 [`skills/agentic-programming/SKILL.md`](../../skills/agentic-programming/SKILL.md)。

```python
runtime = Runtime(call=my_llm, max_retries=3)

try:
    result = my_agentic_function(...)
except Exception:
    my_agentic_function = fix(
        fn=my_agentic_function,
        runtime=runtime,
        instruction="Handle empty input and always return valid JSON.",
    )
```
