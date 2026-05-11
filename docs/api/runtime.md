# Runtime

> Source: [`agentic/runtime.py`](../../agentic/runtime.py)

LLM 运行时。封装 LLM provider，自动处理 Context 注入和记录。

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
Runtime.exec(content, context=None, response_format=None, model=None) -> str
```

调用 LLM，自动注入 Context。

**在 `@agentic_function` 内部调用时：**
1. 从 Context 树生成 execution context（调用 `summarize()`）
2. 把 context 作为第一个 text block 插入 content 列表
3. 调用 `_call()` 发送请求
4. 把回复记录到当前 Context 节点的 `raw_reply`

**在 `@agentic_function` 外部调用时：** 直接调用 LLM，不注入 context，不记录。

**每个 `@agentic_function` 最多调用一次 `exec()`。** 第二次调用会抛 `RuntimeError`。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `content` | `list[dict]` | *(必填)* | 内容块列表（见下方格式） |
| `context` | `str \| None` | `None` | 手动覆盖自动生成的 context。`None` = 自动 |
| `response_format` | `dict \| None` | `None` | 输出格式约束（JSON schema），传给 `_call()` |
| `model` | `str \| None` | `None` | 覆盖默认模型 |

#### Content block 格式

```python
{"type": "text",  "text": "Find the login button."}
{"type": "image", "path": "screenshot.png"}
{"type": "audio", "path": "recording.wav"}
{"type": "file",  "path": "data.csv"}
```

#### 返回值

`str` — LLM 的回复文本。

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
from openprogram import Runtime, agentic_function

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
| API 调用成功 | 返回结果，并把 `{attempt, reply, error}` 记录到当前 Context 节点 |
| API 抛出异常（非 `TypeError` / `NotImplementedError`） | 记录失败 attempt，然后继续重试，直到达到 `max_retries` |
| `TypeError` 或 `NotImplementedError` | 立即抛出，不重试（通常是 provider 实现或调用方式的问题） |
| 所有重试均失败 | 抛出 `RuntimeError`，并附上完整 attempt 报告 |

### Context 中的 attempts

每次 `exec()` / `async_exec()` 都会把尝试历史写入 `ctx.attempts`：

```python
[
    {"attempt": 1, "reply": None, "error": "ConnectionError: timeout"},
    {"attempt": 2, "reply": "ok", "error": None},
]
```

这有两个直接用途：

1. `Context.save()` 能把 retry 历史落盘，方便排查线上问题。
2. `fix(fn=...)` 能直接读取这些 attempts，把失败上下文带给 LLM 做定向修复。

### 错误报告格式

当所有重试耗尽时，抛出的 `RuntimeError` 包含每次尝试的错误信息：

```
RuntimeError: exec() failed after 3 attempts in observe():
Attempt 1: ConnectionError: timeout
Attempt 2: RateLimitError: 429 Too Many Requests
Attempt 3: ConnectionError: timeout
```

这样可以在 Context 树中看到完整的失败史，方便调试。

### 与 `fix()` 的配合

推荐模式：让 `Runtime(max_retries=N)` 先处理短暂 API 波动（网络超时、速率限制等临时错误）；如果函数本身逻辑或输出格式有问题，再用 `fix()` 做结构性修复。两者是互补的——`max_retries` 处理 API 层面的瞬态故障，`fix()` 处理代码层面的结构性问题。

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
