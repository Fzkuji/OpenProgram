# Token 计量

## 统一格式（Anthropic 约定）

所有 provider 都归一化为：
```python
{
    "input_tokens": int,   # 非缓存输入 token
    "output_tokens": int,  # 输出 token
    "cache_read": int,     # 缓存输入 token（从缓存读取）
    "cache_create": int,   # 写入缓存的 token（仅 Anthropic/Claude Code）
}
```

---

## 1. Anthropic API (`anthropic.py`)

**API 调用**：`client.messages.create(**kwargs)`

**原始响应**：
```python
response.usage:
    input_tokens: int           # 非缓存输入 token
    output_tokens: int          # 输出 token
    cache_read_input_tokens: int    # 从 prompt 缓存读取的 token
    cache_creation_input_tokens: int # 写入 prompt 缓存的 token
```

**提取逻辑**（第 147-154 行）：
```python
self.last_usage = {
    "input_tokens": u.input_tokens,
    "output_tokens": u.output_tokens,
    "cache_read": u.cache_read_input_tokens,
    "cache_create": u.cache_creation_input_tokens,
}
```

- **累加方式**：无，每次调用覆盖
- **`usage_is_cumulative`**：`False`
- **缓存支持**：完整（读 + 写）
- **备注**：原生格式，无需归一化。通过在 system 消息上设置 `cache_control` 支持 prompt 缓存。

---

## 2. OpenAI API (`openai.py`)

**API 调用**：`client.chat.completions.create(**kwargs)`

**原始响应**：
```python
response.usage:
    prompt_tokens: int          # 输入 token 总数（缓存 + 非缓存）
    completion_tokens: int      # 输出 token
    prompt_tokens_details:
        cached_tokens: int      # 来自 prompt 缓存的 token
```

**提取逻辑**（第 148-159 行）：
```python
details = getattr(u, 'prompt_tokens_details', None)
cached = getattr(details, 'cached_tokens', 0) if details else 0
total_in = getattr(u, 'prompt_tokens', 0)
self.last_usage = {
    "input_tokens": total_in - (cached or 0),  # 减去缓存部分得到非缓存 token
    "output_tokens": u.completion_tokens,
    "cache_read": cached or 0,
}
```

- **累加方式**：无，每次调用覆盖
- **`usage_is_cumulative`**：`False`
- **缓存支持**：仅读（无 cache_create）
- **关键差异**：OpenAI 的 `prompt_tokens` 包含缓存 token。必须减去才能符合 Anthropic 约定。

---

## 3. Gemini API (`gemini.py`)

**API 调用**：`client.models.generate_content(model=..., contents=..., config=...)`

**原始响应**：
```python
response.usage_metadata:
    prompt_token_count: int       # 输入 token
    candidates_token_count: int   # 输出 token
```

**提取逻辑**（第 156-161 行）：
```python
self.last_usage = {
    "input_tokens": u.prompt_token_count,
    "output_tokens": u.candidates_token_count,
}
```

- **累加方式**：无，每次调用覆盖
- **`usage_is_cumulative`**：`False`
- **缓存支持**：无（Gemini API 不暴露缓存统计）
- **备注**：仅有 2 个字段可用。无缓存信息。

---

## 4. Codex CLI (`openai_codex.py`)

**输出格式**：JSONL 流（换行分隔的 JSON 事件）

**事件及其数据**：
```
{"type": "thread.started", "thread_id": "..."}
{"type": "thread.resumed", "thread_id": "..."}
{"type": "turn.started"}
{"type": "item.started", "item": {"type": "agent_message"|"command_execution", ...}}
{"type": "item.completed", "item": {...}}
{"type": "turn.completed", "usage": {...}}   ← TOKEN 用量在这里
{"type": "turn.failed", "error": {...}}
{"type": "error", "message": "..."}
```

**Usage 对象**（来自 `turn.completed`）：
```python
event["usage"]:
    input_tokens: int           # 输入 token 总数（缓存 + 非缓存）
    output_tokens: int          # 输出 token
    cached_input_tokens: int    # 来自缓存的 token
```

**提取逻辑**（第 438-451 行）：
```python
cached = usage.get("cached_input_tokens", 0)
total_in = usage.get("input_tokens", 0)
prev = self.last_usage or {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}
self.last_usage = {
    "input_tokens": prev["input_tokens"] + (total_in - cached),    # 累加
    "output_tokens": prev["output_tokens"] + output_tokens,         # 累加
    "cache_read": prev["cache_read"] + cached,                      # 累加
}
```

- **累加方式**：是。每个 `turn.completed` 都会累加到之前的总量上。
- **`usage_is_cumulative`**：`True`
- **缓存支持**：仅读（无 cache_create）
- **关键差异**：`input_tokens` 包含缓存（与 OpenAI 一样）。单次 exec 可能产生多个 turn。用量会在 runtime 整个生命周期内的所有 turn 上累加。

---

## 5. Claude Code CLI (`claude_code.py`)

**输出格式**：stream-json（持久进程每行输出一个 JSON 对象）

**事件及其数据**：
```
{"type": "system", ...}
{"type": "assistant", "message": {"content": [{"type": "text"|"tool_use", ...}]}}
{"type": "result", "result": "...", "usage": {...}, "duration_ms": N, "num_turns": N}  ← TOKEN 用量在这里
```

**Usage 对象**（来自 `result` 事件）：
```python
data["usage"]:
    input_tokens: int                   # 非缓存输入 token
    output_tokens: int                  # 输出 token
    cache_read_input_tokens: int        # 从 prompt 缓存读取的 token
    cache_creation_input_tokens: int    # 写入 prompt 缓存的 token
```

**可用的额外字段**（当前未提取）：
```python
data["duration_ms"]: int    # 执行的墙钟时间（毫秒）
data["num_turns"]: int      # agent turn / 交互次数
```

**提取逻辑**（第 329-338 行）：
```python
self.last_usage = {
    "input_tokens": usage.get("input_tokens", 0),
    "output_tokens": usage.get("output_tokens", 0),
    "cache_read": usage.get("cache_read_input_tokens", 0),
    "cache_create": usage.get("cache_creation_input_tokens", 0),
}
```

- **累加方式**：无，每次调用覆盖
- **`usage_is_cumulative`**：`False`
- **缓存支持**：完整（读 + 写）
- **备注**：持久进程（stdin/stdout）。字段名与 Anthropic API 相同。`result` 事件仅包含单次调用的用量，不是整个会话的累计值。

---

## 6. Gemini CLI (`gemini_cli.py`)

**输出格式**：单个 JSON 对象（`--output-format json`）

**响应**：
```python
{
    "session_id": "...",    # 用于 --resume 标志
    "response": "..."       # 响应文本
}
```

**Token 用量**：不可用。Gemini CLI 的 JSON 输出不包含任何 token 用量信息。

**提取逻辑**：无（`last_usage` 保持为 `None`）

- **累加方式**：不适用
- **`usage_is_cumulative`**：`False`
- **缓存支持**：不适用
- **备注**：无法用 Gemini CLI 跟踪 token。这是该 CLI 输出格式的限制。

---

## 汇总表

| Provider | 类型 | 原始字段 | 累加 | 缓存 | input 是否含缓存？ |
|----------|------|-----------|------------|-------|----------------------|
| Anthropic API | API | input, output, cache_read, cache_create | 否 | 完整 | 否 |
| OpenAI API | API | prompt_tokens, completion_tokens, cached_tokens | 否 | 读 | 是（需减去） |
| Gemini API | API | prompt_token_count, candidates_token_count | 否 | 无 | 不适用 |
| Codex CLI | CLI | input_tokens, output_tokens, cached_input_tokens | **是** | 读 | 是（需减去） |
| Claude Code CLI | CLI | input, output, cache_read, cache_create + duration_ms, num_turns | 否 | 完整 | 否 |
| Gemini CLI | CLI | 无 | 不适用 | 无 | 不适用 |

---

## 服务端统计设计

### Chat Agent（按会话累计，存储于 `conv["_chat_usage"]`）

| Provider | 策略 |
|----------|----------|
| Codex CLI | `last_usage` 本身已是累计值 → 直接**替换** `_chat_usage` |
| Claude Code CLI | `last_usage` 是单次调用值 → **累加**到 `_chat_usage` |
| Anthropic API | `last_usage` 是单次调用值 → **累加**到 `_chat_usage` |
| OpenAI API | `last_usage` 是单次调用值 → **累加**到 `_chat_usage` |
| Gemini API | `last_usage` 是单次调用值 → **累加**到 `_chat_usage` |
| Gemini CLI | 无用量数据 → `_chat_usage` 不变 |

### Exec Agent（按函数，来自 `exec_rt.last_usage`）

每次函数执行都会创建自己的 runtime。`last_usage` 仅代表该次执行的用量（Codex 也是如此，因为每个函数都会创建一个全新的 runtime）。

### 前端展示

| 位置 | 数据 | 格式 |
|----------|------|--------|
| 输入区（右下角） | `context_stats.chat` | `chat in:2.3k · out:450` |
| 函数卡片标题 | `result.usage` | `in:1.2k · out:200` |
| 数字格式化 | `< 1000` → 原值，`1k-999k` → `X.Xk`，`>= 1M` → `X.Xm` |
