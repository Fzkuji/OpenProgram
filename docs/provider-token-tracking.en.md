# Provider Token Tracking

## Unified Format (Anthropic Convention)

All providers normalize to:
```python
{
    "input_tokens": int,   # Non-cached input tokens
    "output_tokens": int,  # Output tokens
    "cache_read": int,     # Cached input tokens (read from cache)
    "cache_create": int,   # Tokens written to cache (Anthropic/Claude Code only)
}
```

---

## 1. Anthropic API (`anthropic.py`)

**API Call**: `client.messages.create(**kwargs)`

**Raw Response**:
```python
response.usage:
    input_tokens: int           # Non-cached input tokens
    output_tokens: int          # Output tokens
    cache_read_input_tokens: int    # Tokens read from prompt cache
    cache_creation_input_tokens: int # Tokens written to prompt cache
```

**Extraction** (lines 147-154):
```python
self.last_usage = {
    "input_tokens": u.input_tokens,
    "output_tokens": u.output_tokens,
    "cache_read": u.cache_read_input_tokens,
    "cache_create": u.cache_creation_input_tokens,
}
```

- **Accumulation**: None, overwritten each call
- **`usage_is_cumulative`**: `False`
- **Cache support**: Full (read + create)
- **Notes**: Native format, no normalization needed. Supports prompt caching via `cache_control` on system messages.

---

## 2. OpenAI API (`openai.py`)

**API Call**: `client.chat.completions.create(**kwargs)`

**Raw Response**:
```python
response.usage:
    prompt_tokens: int          # TOTAL input tokens (cached + non-cached)
    completion_tokens: int      # Output tokens
    prompt_tokens_details:
        cached_tokens: int      # Tokens from prompt cache
```

**Extraction** (lines 148-159):
```python
details = getattr(u, 'prompt_tokens_details', None)
cached = getattr(details, 'cached_tokens', 0) if details else 0
total_in = getattr(u, 'prompt_tokens', 0)
self.last_usage = {
    "input_tokens": total_in - (cached or 0),  # Subtract cached to get non-cached
    "output_tokens": u.completion_tokens,
    "cache_read": cached or 0,
}
```

- **Accumulation**: None, overwritten each call
- **`usage_is_cumulative`**: `False`
- **Cache support**: Read only (no cache_create)
- **Key difference**: OpenAI `prompt_tokens` INCLUDES cached tokens. Must subtract to match Anthropic convention.

---

## 3. Gemini API (`gemini.py`)

**API Call**: `client.models.generate_content(model=..., contents=..., config=...)`

**Raw Response**:
```python
response.usage_metadata:
    prompt_token_count: int       # Input tokens
    candidates_token_count: int   # Output tokens
```

**Extraction** (lines 156-161):
```python
self.last_usage = {
    "input_tokens": u.prompt_token_count,
    "output_tokens": u.candidates_token_count,
}
```

- **Accumulation**: None, overwritten each call
- **`usage_is_cumulative`**: `False`
- **Cache support**: None (Gemini API doesn't expose cache stats)
- **Notes**: Only 2 fields available. No cache info.

---

## 4. Codex CLI (`openai_codex.py`)

**Output Format**: JSONL stream (newline-delimited JSON events)

**Events and their data**:
```
{"type": "thread.started", "thread_id": "..."}
{"type": "thread.resumed", "thread_id": "..."}
{"type": "turn.started"}
{"type": "item.started", "item": {"type": "agent_message"|"command_execution", ...}}
{"type": "item.completed", "item": {...}}
{"type": "turn.completed", "usage": {...}}   ← TOKEN USAGE HERE
{"type": "turn.failed", "error": {...}}
{"type": "error", "message": "..."}
```

**Usage Object** (from `turn.completed`):
```python
event["usage"]:
    input_tokens: int           # TOTAL input tokens (cached + non-cached)
    output_tokens: int          # Output tokens
    cached_input_tokens: int    # Tokens from cache
```

**Extraction** (lines 438-451):
```python
cached = usage.get("cached_input_tokens", 0)
total_in = usage.get("input_tokens", 0)
prev = self.last_usage or {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}
self.last_usage = {
    "input_tokens": prev["input_tokens"] + (total_in - cached),    # CUMULATIVE
    "output_tokens": prev["output_tokens"] + output_tokens,         # CUMULATIVE
    "cache_read": prev["cache_read"] + cached,                      # CUMULATIVE
}
```

- **Accumulation**: YES. Each `turn.completed` ADDS to previous totals.
- **`usage_is_cumulative`**: `True`
- **Cache support**: Read only (no cache_create)
- **Key difference**: `input_tokens` INCLUDES cached (like OpenAI). A single exec may produce multiple turns. Usage accumulates across ALL turns in the runtime's lifetime.

---

## 5. Claude Code CLI (`claude_code.py`)

**Output Format**: stream-json (one JSON object per line from persistent process)

**Events and their data**:
```
{"type": "system", ...}
{"type": "assistant", "message": {"content": [{"type": "text"|"tool_use", ...}]}}
{"type": "result", "result": "...", "usage": {...}, "duration_ms": N, "num_turns": N}  ← TOKEN USAGE HERE
```

**Usage Object** (from `result` event):
```python
data["usage"]:
    input_tokens: int                   # Non-cached input tokens
    output_tokens: int                  # Output tokens
    cache_read_input_tokens: int        # Tokens read from prompt cache
    cache_creation_input_tokens: int    # Tokens written to prompt cache
```

**Extra fields available** (NOT currently extracted):
```python
data["duration_ms"]: int    # Wall-clock execution time in ms
data["num_turns"]: int      # Number of agent turns/interactions
```

**Extraction** (lines 329-338):
```python
self.last_usage = {
    "input_tokens": usage.get("input_tokens", 0),
    "output_tokens": usage.get("output_tokens", 0),
    "cache_read": usage.get("cache_read_input_tokens", 0),
    "cache_create": usage.get("cache_creation_input_tokens", 0),
}
```

- **Accumulation**: None, overwritten each call
- **`usage_is_cumulative`**: `False`
- **Cache support**: Full (read + create)
- **Notes**: Persistent process (stdin/stdout). Same field names as Anthropic API. The `result` event includes per-call usage only, NOT cumulative across the session.

---

## 6. Gemini CLI (`gemini_cli.py`)

**Output Format**: Single JSON object (`--output-format json`)

**Response**:
```python
{
    "session_id": "...",    # For --resume flag
    "response": "..."       # The response text
}
```

**Token Usage**: NOT AVAILABLE. Gemini CLI JSON output does not include any token usage information.

**Extraction**: None (`last_usage` remains `None`)

- **Accumulation**: N/A
- **`usage_is_cumulative`**: `False`
- **Cache support**: N/A
- **Notes**: Cannot track tokens with Gemini CLI. This is a limitation of the CLI's output format.

---

## Summary Table

| Provider | Type | Raw Fields | Cumulative | Cache | input includes cached? |
|----------|------|-----------|------------|-------|----------------------|
| Anthropic API | API | input, output, cache_read, cache_create | No | Full | No |
| OpenAI API | API | prompt_tokens, completion_tokens, cached_tokens | No | Read | YES (subtract) |
| Gemini API | API | prompt_token_count, candidates_token_count | No | None | N/A |
| Codex CLI | CLI | input_tokens, output_tokens, cached_input_tokens | **YES** | Read | YES (subtract) |
| Claude Code CLI | CLI | input, output, cache_read, cache_create + duration_ms, num_turns | No | Full | No |
| Gemini CLI | CLI | None | N/A | None | N/A |

---

## Server-Side Tracking Design

### Chat Agent (cumulative per conversation, stored in `conv["_chat_usage"]`)

| Provider | Strategy |
|----------|----------|
| Codex CLI | `last_usage` is already cumulative → **replace** `_chat_usage` directly |
| Claude Code CLI | `last_usage` is per-call → **add** to `_chat_usage` |
| Anthropic API | `last_usage` is per-call → **add** to `_chat_usage` |
| OpenAI API | `last_usage` is per-call → **add** to `_chat_usage` |
| Gemini API | `last_usage` is per-call → **add** to `_chat_usage` |
| Gemini CLI | No usage data → `_chat_usage` unchanged |

### Exec Agent (per-function, from `exec_rt.last_usage`)

Each function execution creates its own runtime. `last_usage` represents usage for that execution only (even Codex, since a fresh runtime is created per function).

### Frontend Display

| Location | Data | Format |
|----------|------|--------|
| Input area (bottom-right) | `context_stats.chat` | `chat in:2.3k · out:450` |
| Function card header | `result.usage` | `in:1.2k · out:200` |
| Number formatting | `< 1000` → raw, `1k-999k` → `X.Xk`, `>= 1M` → `X.Xm` |
