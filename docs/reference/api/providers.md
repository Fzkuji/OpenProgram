# Providers

> Source: [`openprogram/providers/`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/)

Built-in Runtime subclasses, ready to use out of the box. Every provider is an **optional dependency** — you only need to install the SDK when you import the corresponding class.

---

## Installation

The framework core has no SDK dependencies at all. Install them as needed:

```bash
# Anthropic Claude API
pip install anthropic

# OpenAI GPT / Responses API
pip install openai

# Google Gemini API
pip install google-genai

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

# OpenAI Codex CLI
npm install -g @openai/codex

# Gemini CLI
npm install -g @google/gemini-cli
```

---

## AnthropicRuntime

Anthropic Claude API. Supports text + image content blocks, `response_format` JSON constraints, and automatic prompt caching.

```python
from openprogram.providers import AnthropicRuntime

rt = AnthropicRuntime(
    api_key="sk-ant-...",      # or set the ANTHROPIC_API_KEY environment variable
    model="claude-sonnet-4-6",
    max_tokens=4096,
    system="You are a helpful assistant.",  # optional system prompt
    cache_system=True,          # cache the system prompt (default True)
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. When `None`, reads the `ANTHROPIC_API_KEY` environment variable |
| `model` | `str` | `"claude-sonnet-4-6"` | Default model |
| `max_tokens` | `int` | `4096` | Maximum number of output tokens |
| `system` | `str \| None` | `None` | System prompt |
| `cache_system` | `bool` | `True` | Whether to cache the system prompt |
| `max_retries` | `int` | `2` | Number of retries |

### Prompt Caching

AnthropicRuntime automatically adds `cache_control: {"type": "ephemeral"}` to the last content block. This means:

- **The context prefix is cached**: across successive calls, an identical Context prefix hits the cache, substantially reducing latency and cost
- **The system prompt is cached**: if `system` is set and `cache_system=True`

You can also control caching manually:

```python
rt.exec(content=[
    {"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "..."},
])
```

### response_format

The Anthropic API has no native JSON schema parameter like OpenAI's. Here a text-constraint approach is used: it appends an instruction to "return only JSON that matches the schema":

```python
result = rt.exec(
    content=[{"type": "text", "text": "Extract title and authors"}],
    response_format={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "authors": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    },
)
```

### Image support

```python
# from a file
rt.exec(content=[
    {"type": "text", "text": "What's in this image?"},
    {"type": "image", "path": "screenshot.png"},
])

# from base64
rt.exec(content=[
    {"type": "image", "data": "<base64>", "media_type": "image/png"},
])

# from a URL
rt.exec(content=[
    {"type": "image", "url": "https://example.com/image.png"},
])
```

---

## OpenAIRuntime

OpenAI GPT API. Supports text + image, and response_format (JSON mode / structured output).

```python
from openprogram.providers import OpenAIRuntime

rt = OpenAIRuntime(
    api_key="sk-...",          # or set the OPENAI_API_KEY environment variable
    model="gpt-4o",
    max_tokens=4096,
    system="You are a helpful assistant.",
    temperature=0.7,           # optional
    base_url="https://...",    # optional, for Azure or a local service
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. When `None`, reads the `OPENAI_API_KEY` environment variable |
| `model` | `str` | `"gpt-4o"` | Default model |
| `max_tokens` | `int` | `4096` | Maximum number of output tokens |
| `system` | `str \| None` | `None` | System prompt |
| `temperature` | `float \| None` | `None` | Sampling temperature |
| `max_retries` | `int` | `2` | Number of retries |
| `base_url` | `str \| None` | `None` | Custom base URL |

### response_format

```python
# JSON mode
result = rt.exec(
    content=[{"type": "text", "text": "List 3 colors as JSON array"}],
    response_format={"type": "json_object"},
)

# Structured output (JSON schema)
result = rt.exec(
    content=[{"type": "text", "text": "Rate this idea"}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "rating",
            "schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer"},
                    "reasoning": {"type": "string"},
                },
            },
        },
    },
)
```

### Compatible APIs

Through `base_url` you can connect to any OpenAI-compatible API:

```python
# Azure OpenAI
rt = OpenAIRuntime(
    api_key="...",
    base_url="https://your-resource.openai.azure.com/openai/deployments/gpt-4o",
    model="gpt-4o",
)

# Local server (vLLM, Ollama, etc.)
rt = OpenAIRuntime(
    api_key="not-needed",
    base_url="http://localhost:8000/v1",
    model="meta-llama/Llama-3-70B",
)
```

---

## GeminiRuntime

Google Gemini API. Supports text + image.

```python
from openprogram.providers import GeminiRuntime

rt = GeminiRuntime(
    api_key="...",             # or set the GOOGLE_API_KEY environment variable
    model="gemini-2.5-flash",
    max_output_tokens=4096,
    system_instruction="You are a helpful assistant.",
    temperature=0.7,
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. When `None`, reads the `GOOGLE_API_KEY` environment variable |
| `model` | `str` | `"gemini-2.5-flash"` | Default model |
| `max_output_tokens` | `int` | `4096` | Maximum number of output tokens |
| `system_instruction` | `str \| None` | `None` | System instruction |
| `temperature` | `float \| None` | `None` | Sampling temperature |
| `max_retries` | `int` | `2` | Number of retries |

### response_format

GeminiRuntime supports requesting JSON output through the `response_format` parameter:

```python
result = rt.exec(
    content=[{"type": "text", "text": "List 3 colors"}],
    response_format={"schema": {"type": "array", "items": {"type": "string"}}},
)
```

When `response_format` is passed, `response_mime_type="application/json"` is set automatically. If it includes a `schema` field, `response_schema` is also set.

---

## ClaudeCodeRuntime

Claude Code CLI. Suited to local development machines / subscription-account scenarios, with no need to configure a separate API key in Python.

```python
from openprogram.providers import ClaudeCodeRuntime

rt = ClaudeCodeRuntime(
    model="haiku",
    timeout=120,
)
```

Before using it, first complete:

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

Notes:
- Primarily intended for text and image input
- Better suited to interactive development workflows than to high-throughput server-side calls
- If you pass audio / video / file blocks, it emits a warning and skips the unsupported content

---

## OpenAICodexRuntime

ChatGPT / Codex subscription runtime. It reads OAuth credentials from the Codex
CLI auth file and uses the ChatGPT Responses backend.

```python
from openprogram.providers import OpenAICodexRuntime

rt = OpenAICodexRuntime(model="gpt-5.5")
```

Before using it, first complete:

```bash
npm install -g @openai/codex
codex login --device-auth
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|------|------|
| `model` | `str` | `"gpt-5.5-mini"` | Default model |
| `system` | `str \| None` | `None` | Forwarded as `instructions` |
| `profile` | `str \| None` | active profile | Specifies the OpenProgram auth profile |

Notes:
- Requires an OAuth credential; it does not use a bare OpenAI API key
- Compatible with subprocess-era parameters passed by older callers; these extra parameters are ignored
- If you only have an OpenAI API key, use `OpenAIRuntime`

---

## GeminiCLIRuntime

Gemini CLI / subscription runtime. It reads Gemini CLI OAuth credentials and
uses the Gemini HTTP backend through OpenProgram's provider layer.

```python
from openprogram.providers import GeminiCLIRuntime

rt = GeminiCLIRuntime(model="gemini-2.5-flash")
```

If you want to use the class explicitly, you can also write it directly like this:

```python
from openprogram.providers.google_gemini_cli import GeminiCLIRuntime

rt = GeminiCLIRuntime(model="gemini-2.5-flash")
```

Before using it, first complete:

```bash
npm install -g @google/gemini-cli
gemini
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `model` | `str` | `"gemini-2.5-flash"` | Default model |
| `system` | `str \| None` | `None` | Forwarded as `instructions` |
| `profile` | `str \| None` | active profile | Specifies the OpenProgram auth profile |

Notes:
- Requires a Gemini CLI OAuth credential
- Compatible with subprocess-era parameters passed by older callers; these extra parameters are ignored
- If you only have a Google API key, use `GeminiRuntime`

---

## Custom Providers

All built-in providers are subclasses of `Runtime`. You can create your own in the same way:

```python
from openprogram.agentic_programming.runtime import Runtime

class MyRuntime(Runtime):
    def __init__(self, api_key, model="my-model"):
        super().__init__(model=model)
        self.api_key = api_key

    def _call(self, content, model="default", response_format=None):
        # 1. convert the content blocks into your API's format
        # 2. call the API
        # 3. return a str
        texts = [b["text"] for b in content if b["type"] == "text"]
        return my_api_call("\n".join(texts), model=model)
```

The key point: `_call()` receives `content: list[dict]` and returns a `str`. It's that simple.
