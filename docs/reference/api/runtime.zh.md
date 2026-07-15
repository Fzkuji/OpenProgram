# Runtime

> Source: [`openprogram/agentic_programming/runtime.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/runtime.py)

LLM 运行时。封装 LLM provider,自动从 session DAG 算上下文、调用 LLM、把回复写回 DAG。

---

## Class: `Runtime`

```python
class Runtime(call=None, model="default", max_retries=None, api_key=None, skills=None)
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `call` | `Callable \| None` | `None` | 用户自带的 LLM 函数。签名:`fn(content: list[dict], model: str, response_format: dict) -> str`。内部经 `CallableModel` 包进标准 provider 路径,DAG 记录和历史渲染与真 provider 一致。既没传 `call` 也没用 `"provider:model_id"` 形式时,需要子类化并重写 `_call()` |
| `model` | `str` | `"default"` | 默认模型。两种形式:`"provider:model_id"`(如 `"anthropic:claude-sonnet-4-6"`)经 `openprogram.providers` 解析并走 provider 层流式;其它字符串只有配合 `call=` 或子类才有意义。未知的 `"provider:model_id"` 抛 `ValueError` |
| `max_retries` | `int \| None` | `None` | exec() 最大尝试次数(包含首次调用,且必须 >= 1)。`None` = 读环境变量 `OPENPROGRAM_MAX_RETRIES`,没设则为 `6` |
| `api_key` | `str \| None` | `None` | provider 路径的 API key。`None` = 从凭据库解析(`openprogram providers login`) |
| `skills` | `bool \| list[str] \| None` | `None` | system prompt 的技能发现。`None` / `False` = 关闭;`True` = 探测默认技能目录(用户 + 仓库);`list[str]` = 显式目录列表。开启后每次 `exec()` 的 system prompt 追加 `<available_skills>` 块 |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `model` | `str` | 默认模型名称 |
| `max_retries` | `int` | 解析后的重试预算 |
| `system` | `str` | 可赋值的 system prompt,`exec()` 在 provider 路径上读取(`@agentic_function(system=...)` 装饰器会在调用期间设置它) |
| `thinking_level` | `str` | 推理力度旋钮:`"off"`(默认)/ `"low"` / `"medium"` / `"high"` / `"xhigh"`,透传给 provider |
| `session_id` | `str` | 跨多次 `exec()` 稳定的 id(`"op-<hex>"`),provider 拿它当 prompt-cache key |
| `on_stream` | `Callable \| None` | 可选回调 `fn(event_dict)`,接收流式事件(text / thinking / tool_use / tool_result) |
| `last_usage` | `dict \| None` | 上一次调用的 token 用量:`{input_tokens, output_tokens, total_tokens, cache_read, cache_create, ...}` |

---

## 方法

### `exec()`

```python
Runtime.exec(content, context=None, response_format=None, model=None,
             tools=None, toolset=None, tools_source=None, tools_allow=None,
             tools_deny=None, tool_choice="auto", parallel_tool_calls=True,
             max_iterations=20, choices=None, timeout_s=None, on_retry=None,
             web_search=False, stream_fn=None) -> Any
```

调用 LLM,上下文从 session DAG 自动算出。

**在 `@agentic_function` 内部调用时:**
1. 从当前函数的 DAG 节点出发,`render_context` 按 `expose` / `render_range` 算出本次要读哪些历史节点
2. `render_dag_messages` 把这些节点渲染成 messages
3. 调用 `_call()` 发送请求
4. 回复写进 `exec()` 开始时打开的那个 `llm` 节点

**没安装 DAG store 时**(standalone 脚本、无 dispatcher):`content` 包成单条 user message 作单轮调用发送,不做任何记录。

一个 `@agentic_function` 可以多次调用 `exec()`,每次都是 DAG 上的一个新 `llm` 节点。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `content` | `list[dict] \| str` | *(必填)* | 内容块列表(见下方格式)。纯字符串会包成一个 text 块 |
| `context` | `str \| None` | `None` | 遗留参数,已被忽略——provider 路径从 DAG 构建历史 |
| `response_format` | `dict \| None` | `None` | 输出格式约束(JSON schema),转发给 `_call()`。默认 provider 路径不应用它;要生效需子类重写 `_call()` |
| `model` | `str \| None` | `None` | 覆盖默认模型 |
| `tools` | `list \| None` | `None` | 本次调用 LLM 可用的工具。每项可以是 `@agentic_function`、`{"spec":..., "execute":...}` 字典、或带 `.spec` / `.execute` 的对象。设了就跑工具循环直到模型返回纯文本。**默认(`None`)不是"无工具"**:调用会拿到完整的注册工具集;纯推理调用传 `toolset="none"`,要显式空列表传 `tools=[]` |
| `toolset` / `tools_source` / `tools_allow` / `tools_deny` | — | `None` | 工具集预设与策略过滤:`toolset` 指名预设(`"full"` 是隐式默认,`"none"` 表示退出),`tools_source` 按渠道来源过滤,`tools_allow` / `tools_deny` 是名单允许/拒绝列表 |
| `tool_choice` | `str \| dict` | `"auto"` | `"auto"` / `"required"` / `"none"` / `{"type":"function","name":"X"}` 强制某工具。透传到 provider(OpenAI / Anthropic / Gemini / Bedrock 各自映射协议形态) |
| `parallel_tool_calls` | `bool` | `True` | 允许一轮多个工具调用;`False` 透传到支持该开关的 provider |
| `max_iterations` | `int` | `20` | 工具循环轮数上限(一轮 = 一次模型调用 + 其工具执行)。生效值为 `max(1, min(50, max_iterations))`,50 是 `agent_loop.py` 的硬上限 |
| `choices` | `dict \| list \| None` | `None` | 设了则约束 turn 的**收尾**:模型跑完整 turn 后,最终回复必须从 `choices` 里选一个;`exec` 解析并返回该选择的结果。详见 [next-step-decision](../../capabilities/agentic-programming/choosing-the-next-step/next-step-decision.md) |
| `timeout_s` | `float \| None` | `None` | 整个 `exec()`(含全部重试休眠)的墙钟时间预算,超时抛 `LLMError`(`reason=TIMEOUT`, `retryable=False`)。`None` = 回落到环境变量 `OPENPROGRAM_EXEC_TIMEOUT_S`(没设或为 `0` = 不限时) |
| `on_retry` | `Callable \| None` | `None` | 每次退避休眠前调用的观测回调(每个后面还排着重试的失败尝试触发一次),入参 `RetryInfo`;最终失败不触发。回调内抛出的异常被吞掉 |
| `web_search` | `bool` | `False` | 本次调用启用 provider 原生的 web 搜索工具(视 provider 支持) |
| `stream_fn` | — | `None` | 逐调用的流函数覆盖(dispatcher 和测试用它注入假的或预构建的流);`None` = 真 provider |

#### Content block 格式

```python
{"type": "text",  "text": "Find the login button."}
{"type": "image", "path": "screenshot.png"}
{"type": "image", "data": "<base64>", "mime_type": "image/png"}
{"type": "video", "path": "clip.mp4"}
{"type": "audio", "path": "recording.wav"}
```

媒体块给 `path`(自动读取并 base64 编码,mime 类型按扩展名猜)或内联 `data` + `mime_type` 都行。text 块可带 `"role": "system"` 汇入 system prompt;text/image 块接受 `cache_control` 做 provider prompt caching。未知块类型静默跳过。

#### 返回值

`str` — LLM 的回复文本。带 `choices` 时返回解析后的决策结果(选中函数的返回值,或选中值本身)。

#### 异常

- `RuntimeError` — runtime 已关闭(调用过 `close()`)
- `TypeError` / `NotImplementedError` — 立即抛出,从不重试(编程错误:调用签名不对、没配置 provider)
- `LLMError` — 重试耗尽或遇到不可重试错误时抛出,结构化字段含 `reason` / `retryable` / `http_status` / `retry_after_s` / `attempts` / `elapsed_s` / `provider` / `model` 等

---

### `async_exec()`

```python
await Runtime.async_exec(content, context=None, response_format=None, model=None,
                         timeout_s=None, on_retry=None) -> str
```

`exec()` 的异步版本。内部调用 `_async_call()`——默认实现只支持 `call=` 函数(同步或异步均可,同步的自动适配);`"provider:model_id"` 路径需要子类重写 `_async_call()`。`timeout_s` / `on_retry` 语义与 `exec()` 相同;重试用 `asyncio.sleep` 休眠,外部取消能生效。没有工具循环参数——`async_exec()` 是单回复的普通调用。

---

### `_call()`

```python
Runtime._call(content, model="default", response_format=None) -> str
```

实际调用一次 LLM 的方法(不含重试——重试循环在 `exec()` 里包着)。默认实现在配置了 provider 模型或 `call=` 函数时走 provider 层(`AgentSession`),否则抛 `NotImplementedError`。**子类化时重写此方法。**

#### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `content` | `list[dict]` | 当前 turn 的内容块(历史由 provider 路径从 DAG 渲染) |
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

### `close()`

释放资源、结束会话;`close()` 之后 `exec()` 抛 `RuntimeError`。`Runtime` 也是上下文管理器(`with Runtime(...) as rt:` 退出时自动关闭)。子类重写它清理 provider 专属资源。

---

### 向用户提问

有前端会话连着时,runtime 可以在函数中途阻塞等用户输入:`runtime.ask(prompt, options=..., multi=..., questions=[...], timeout=300.0, default=None)`(一题或多题一屏),`runtime.confirm(prompt, default=False)`(是/否),`runtime.form(prompt, fields)`(多字段表单)。`runtime.can_ask()` 报告当前有没有人能回答(headless 跑时为 False)。用户拒绝抛 `UserDeclined`;超时给了 `default` 就返回它,否则抛 `AskTimeout`。

---

## 使用方式

### 方式一:传入 call 函数

```python
from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

def my_llm(content, model="sonnet", response_format=None):
    # 把 content 转成你的 provider 格式,发请求
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

### 方式二:子类化

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

`exec()` 和 `async_exec()` 内置自动重试,用于处理 LLM API 的临时性错误(网络超时、速率限制、服务器错误等)。

### 配置

```python
# 默认:max_retries=None → 读环境变量 OPENPROGRAM_MAX_RETRIES,没设则为 6
rt = Runtime(call=my_llm)

# 不重试(失败即抛异常)
rt = Runtime(call=my_llm, max_retries=1)

# 多次重试(适用于不稳定的 API)
rt = Runtime(call=my_llm, max_retries=5)
```

### 行为规则

| 情况 | 处理 |
|------|------|
| API 调用成功 | 返回结果 |
| API 抛出瞬态异常 | 记录失败 attempt,按指数退避休眠(基数 1.5 秒 x 2^attempt,±25% 抖动;服务端 `Retry-After` 提示作为下限被遵守;基数可用 `OPENPROGRAM_RETRY_BACKOFF_BASE` 调),然后重试直到达到 `max_retries` |
| 永久性错误(图片数据损坏、登录过期、无效 API key、或 provider 已把异常标记 `retryable=False`) | 立即以 `retryable=False` 的 `LLMError` 抛出,不重试 |
| provider 自己的传输层重试预算已耗尽(`transport_exhausted`) | 不再二次重试——直接抛 `LLMError` |
| `TypeError` 或 `NotImplementedError` | 立即抛出,不重试(通常是 provider 实现或调用方式的问题) |
| 所有重试均失败 | 抛出结构化 `LLMError`(`reason` / `retryable` / `http_status` / `attempts` 等字段),并附上完整 attempt 报告 |

### 错误报告格式

当所有重试耗尽时,抛出的 `LLMError` 包含每次尝试的错误信息,结构化字段(`reason` / `retryable` / `http_status` / `attempts` / `elapsed_s` 等)可直接读取:

```
LLMError: exec() failed after 3 attempt(s):
Attempt 1: ConnectionError: timeout
Attempt 2: RateLimitError: 429 Too Many Requests
Attempt 3: ConnectionError: timeout
```

### 重试的边界

`max_retries` 只处理 API 层面的瞬态故障(网络超时、速率限制等)。如果是函数本身的逻辑或输出格式有问题,重试解决不了——直接修改函数代码,参见 [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md)。
