# Providers

> Source: [`openprogram/providers/`](../../openprogram/providers/)

内置 Runtime 子类，开箱即用。每个 provider 都是**可选依赖**——只在你 import 对应类时才需要安装 SDK。

---

## 安装

框架核心没有任何 SDK 依赖。按需安装：

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

Anthropic Claude API。支持 text + image content blocks、`response_format` JSON 约束，自动 prompt caching。

```python
from openprogram.providers import AnthropicRuntime

rt = AnthropicRuntime(
    api_key="sk-ant-...",      # 或设置 ANTHROPIC_API_KEY 环境变量
    model="claude-sonnet-4-6",
    max_tokens=4096,
    system="You are a helpful assistant.",  # 可选 system prompt
    cache_system=True,          # 缓存 system prompt（默认 True）
)
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` 时读 `ANTHROPIC_API_KEY` 环境变量 |
| `model` | `str` | `"claude-sonnet-4-6"` | 默认模型 |
| `max_tokens` | `int` | `4096` | 最大输出 token 数 |
| `system` | `str \| None` | `None` | System prompt |
| `cache_system` | `bool` | `True` | 是否缓存 system prompt |
| `max_retries` | `int` | `2` | 重试次数 |

### Prompt Caching

AnthropicRuntime 自动在最后一个 content block 上添加 `cache_control: {"type": "ephemeral"}`。这意味着：

- **context 前缀被缓存**：连续调用时，相同的 Context 前缀命中缓存，大幅降低延迟和成本
- **system prompt 被缓存**：如果设置了 `system` 且 `cache_system=True`

你也可以手动控制缓存：

```python
rt.exec(content=[
    {"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "..."},
])
```

### response_format

Anthropic API 本身没有像 OpenAI 那样的原生 JSON schema 参数，这里采用文本约束方式追加一条“只返回匹配 schema 的 JSON”指令：

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

### Image 支持

```python
# 从文件
rt.exec(content=[
    {"type": "text", "text": "What's in this image?"},
    {"type": "image", "path": "screenshot.png"},
])

# 从 base64
rt.exec(content=[
    {"type": "image", "data": "<base64>", "media_type": "image/png"},
])

# 从 URL
rt.exec(content=[
    {"type": "image", "url": "https://example.com/image.png"},
])
```

---

## OpenAIRuntime

OpenAI GPT API。支持 text + image，response_format（JSON mode / structured output）。

```python
from openprogram.providers import OpenAIRuntime

rt = OpenAIRuntime(
    api_key="sk-...",          # 或设置 OPENAI_API_KEY 环境变量
    model="gpt-4o",
    max_tokens=4096,
    system="You are a helpful assistant.",
    temperature=0.7,           # 可选
    base_url="https://...",    # 可选，用于 Azure 或本地服务
)
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` 时读 `OPENAI_API_KEY` 环境变量 |
| `model` | `str` | `"gpt-4o"` | 默认模型 |
| `max_tokens` | `int` | `4096` | 最大输出 token 数 |
| `system` | `str \| None` | `None` | System prompt |
| `temperature` | `float \| None` | `None` | 采样温度 |
| `max_retries` | `int` | `2` | 重试次数 |
| `base_url` | `str \| None` | `None` | 自定义 base URL |

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

### 兼容 API

通过 `base_url` 可以连接任何 OpenAI 兼容的 API：

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

Google Gemini API。支持 text + image。

```python
from openprogram.providers import GeminiRuntime

rt = GeminiRuntime(
    api_key="...",             # 或设置 GOOGLE_API_KEY 环境变量
    model="gemini-2.5-flash",
    max_output_tokens=4096,
    system_instruction="You are a helpful assistant.",
    temperature=0.7,
)
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` 时读 `GOOGLE_API_KEY` 环境变量 |
| `model` | `str` | `"gemini-2.5-flash"` | 默认模型 |
| `max_output_tokens` | `int` | `4096` | 最大输出 token 数 |
| `system_instruction` | `str \| None` | `None` | System instruction |
| `temperature` | `float \| None` | `None` | 采样温度 |
| `max_retries` | `int` | `2` | 重试次数 |

### response_format

GeminiRuntime 支持通过 `response_format` 参数请求 JSON 输出：

```python
result = rt.exec(
    content=[{"type": "text", "text": "List 3 colors"}],
    response_format={"schema": {"type": "array", "items": {"type": "string"}}},
)
```

传入 `response_format` 时，自动设置 `response_mime_type="application/json"`。如果包含 `schema` 字段，还会设置 `response_schema`。

---

## ClaudeCodeRuntime

Claude Code CLI。适合本地开发机 / 订阅账号场景，不需要在 Python 里单独配置 API key。

```python
from openprogram.providers import ClaudeCodeRuntime

rt = ClaudeCodeRuntime(
    model="haiku",
    timeout=120,
)
```

使用前先完成：

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

说明：
- 主要面向 text 和 image 输入
- 更适合交互式开发工作流，而不是高吞吐服务端调用
- 如果传入 audio / video / file blocks，会给出 warning 并跳过不支持的内容

---

## ChatGPTSubscriptionRuntime

ChatGPT 订阅（OAuth HTTP）。适合已经在本机登录 `codex` 的开发环境。

```python
from openprogram.providers import ChatGPTSubscriptionRuntime

rt = ChatGPTSubscriptionRuntime(
    model="gpt-5.4-mini",
    timeout=120,
    full_auto=True,
)
```

使用前先完成：

```bash
npm install -g @openai/codex
codex login --device-auth
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | `str \| None` | `None` | 默认模型；`None` 时使用 CLI 默认值 |
| `timeout` | `int` | `120` | 单次 CLI 调用超时秒数 |
| `cli_path` | `str \| None` | `None` | Codex CLI 可执行文件路径；为空时自动查找 |
| `session_id` | `str \| None` | `"auto"` | `"auto"` 会在首次调用后捕获 CLI 的真实线程 ID；`None` 表示无状态 |
| `workdir` | `str \| None` | `None` | 通过 `--cd` 指定工作目录 |
| `full_auto` | `bool` | `True` | 是否添加 `--full-auto` |
| `sandbox` | `str` | `"workspace-write"` | CLI sandbox 模式 |
| `max_retries` | `int` | `2` | 重试次数 |

说明：
- 支持 text 与 image 文件输入
- image URL 会降级为文本提示
- audio / video / file blocks 会 warning 并跳过

---

## GeminiSubscriptionRuntime

Gemini 订阅（OAuth HTTP）。`openprogram.providers.GeminiSubscriptionRuntime` 是当前正式类名，旧别名 `GeminiSubscriptionRuntime` 和 `GeminiSubscriptionRuntime` 均保留兼容。

```python
from openprogram.providers import GeminiSubscriptionRuntime

rt = GeminiSubscriptionRuntime(
    model="gemini-2.5-flash",
    timeout=120,
    yolo=True,
)
```

如果你想显式使用类，也可以直接这样写：

```python
from openprogram.providers.google_gemini_cli import GeminiSubscriptionRuntime

rt = GeminiSubscriptionRuntime(model="gemini-2.5-flash")
```

使用前先完成：

```bash
npm install -g @google/gemini-cli
gemini
```

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | `str \| None` | `None` | 默认模型；`None` 时使用 CLI 默认值 |
| `timeout` | `int` | `120` | 单次 CLI 调用超时秒数 |
| `cli_path` | `str \| None` | `None` | Gemini CLI 可执行文件路径；为空时自动查找 |
| `sandbox` | `bool` | `False` | 是否启用 CLI sandbox 标志 `-s` |
| `yolo` | `bool` | `True` | 是否启用自动确认 `-y` |
| `max_retries` | `int` | `2` | 重试次数 |

说明：
- text blocks 原样拼接为 prompt
- image 会降级为文本占位并给出 warning
- audio / video / file blocks 也会降级为文本占位并 warning
- `response_format` 会附加为 “只返回 JSON” 的文本约束

---

## 自定义 Provider

所有内置 provider 都是 `Runtime` 的子类。你可以用同样的方式创建自己的：

```python
from openprogram import Runtime

class MyRuntime(Runtime):
    def __init__(self, api_key, model="my-model"):
        super().__init__(model=model)
        self.api_key = api_key

    def _call(self, content, model="default", response_format=None):
        # 1. 把 content blocks 转成你的 API 格式
        # 2. 调用 API
        # 3. 返回 str
        texts = [b["text"] for b in content if b["type"] == "text"]
        return my_api_call("\n".join(texts), model=model)
```

关键：`_call()` 接收 `content: list[dict]`，返回 `str`。就这么简单。
