# Getting Started | 快速上手

[English](#english) | [中文](#中文)

---

<a id="english"></a>

## 🚀 3-Minute Quick Start

### Step 1: Install

The one-command installer sets up everything — Python package + web UI + terminal UI + the GUI agent (with its model weight and OCR):

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

Requires Python ≥ 3.11, Node ≥ 20, git. PyTorch auto-selects your hardware (NVIDIA GPU → CUDA, else CPU; force with `--cpu` / `--cuda cu124`). Host only (skip the GUI agent)? add `--no-gui`. Full dependency matrix and flags: [docs/install.md](install.md).

### Step 2: Connect a provider

No separate command needed — **the first time you run `openprogram`, it walks you through provider setup** (importing credentials from a logged-in Claude Code / Codex / Gemini CLI, or asking for an API key), then opens the chat. Re-run it any time with `openprogram setup`.

Or set a key manually and skip the wizard:

```bash
export ANTHROPIC_API_KEY=sk-ant-...                 # Claude
export OPENAI_API_KEY=sk-...                        # GPT
export GOOGLE_API_KEY=...                           # Gemini
# Or CLI-based (no API key, uses your existing subscription):
#   npm i -g @anthropic-ai/claude-code && claude login
#   npm i -g @openai/codex && codex auth
#   npm i -g @google/gemini-cli && gemini auth login
```

Sanity-check: `openprogram providers` lists what's detected.

### Step 3: Write your first agentic function

```python
from openprogram import agentic_function
from openprogram.providers.registry import create_runtime

runtime = create_runtime()                          # auto-picks the first available provider

@agentic_function
def greet(name):
    """Greet someone in a creative, fun way."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Say hello to {name} in a creative way. Keep it short (1-2 sentences)."},
    ])

print(greet(name="World"))
```

```bash
python your_script.py
```

That's it. Your function now **thinks**.

---

## Choose Your Provider

Agentic Programming supports 6 built-in runtimes out of the box. Pick one:

### Option A: Claude Code CLI (Recommended for Getting Started)

**No API key needed.** Uses your Claude Code subscription.

**Prerequisites:**
```bash
npm install -g @anthropic-ai/claude-code
claude login
```

**Usage:**
```python
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")
```

**Pros:** Zero API key setup, uses existing subscription.
**Cons:** Slower than direct API (subprocess overhead), text-only.

---

### Option B: Anthropic API (Claude)

**Best for production.** Direct API access with prompt caching.

**Setup:**
```bash
pip install -e .          # anthropic SDK is included by default
export ANTHROPIC_API_KEY="sk-ant-..."
```

**Usage:**
```python
from openprogram.providers import AnthropicRuntime

runtime = AnthropicRuntime(
    model="claude-sonnet-4-6",
    # api_key="sk-ant-..."  # or use ANTHROPIC_API_KEY env var
)
```

**Supports:** Text, images (base64/URL/file), prompt caching, system prompts.

---

### Option C: OpenAI API (GPT)

**Setup:**
```bash
pip install -e .          # openai SDK is included by default
export OPENAI_API_KEY="sk-..."
```

**Usage:**
```python
from openprogram.providers import OpenAIRuntime

runtime = OpenAIRuntime(
    model="gpt-4o",
    # api_key="sk-..."  # or use OPENAI_API_KEY env var
)
```

**Supports:** Text, images (base64/URL/file), response_format (JSON mode), system prompts.

---

### Option D: Google Gemini API

**Setup:**
```bash
pip install -e .          # google-genai SDK is included by default
export GOOGLE_API_KEY="..."
```

**Usage:**
```python
from openprogram.providers import GeminiRuntime

runtime = GeminiRuntime(
    model="gemini-2.5-flash",
    # api_key="..."  # or use GOOGLE_API_KEY env var
)
```

**Supports:** Text, images (base64/URL/file), system instructions, JSON schema output.

---

### Option E: Codex CLI

**No Python API key needed.** Uses the Codex CLI you already signed into.

**Prerequisites:**
```bash
# install Codex CLI first, then sign in
codex login --device-auth
```

**Usage:**
```python
from openprogram.providers import OpenAICodexRuntime

runtime = OpenAICodexRuntime(model="gpt-5.5")
```

**Pros:** Local CLI workflow, easy to reuse an existing Codex setup.
**Cons:** Subprocess overhead, text-only.

---

### Option F: Gemini CLI

**No Python API key needed.** Uses the Gemini CLI session on your machine.

**Prerequisites:**
```bash
# install Gemini CLI first, then sign in
gemini
```

**Usage:**
```python
from openprogram.providers import GeminiCLIRuntime

runtime = GeminiCLIRuntime()
```

**Pros:** Local CLI workflow, no Python-side SDK setup.
**Cons:** Subprocess overhead, text-only.

---

## Complete Working Example

Here's a full script you can copy, paste, and run:

```python
"""
Full working example: Task decomposition with Agentic Programming.
Uses ClaudeCodeRuntime (no API key needed, just `claude` CLI).
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# Initialize runtime (no API key needed)
runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def analyze(topic):
    """Analyze a topic and list 3 key points."""
    return runtime.exec(content=[
        {"type": "text", "text": f"List exactly 3 key points about: {topic}\nOne line per point, numbered 1-3."},
    ])


@agentic_function
def elaborate(point):
    """Elaborate on a single point with one insightful sentence."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Elaborate on this point in exactly one insightful sentence:\n{point}"},
    ])


@agentic_function
def research(topic):
    """Analyze a topic, then elaborate on each point."""
    # Step 1: Get key points (Python controls the flow)
    points_text = analyze(topic=topic)
    print(f"📋 Key points:\n{points_text}\n")

    # Step 2: Elaborate on each point (Python controls the loop)
    lines = [l.strip() for l in points_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        detail = elaborate(point=line)
        print(f"  💡 {detail}\n")

    # Step 3: Return summary (LLM sees full context automatically)
    return runtime.exec(content=[
        {"type": "text", "text": "Based on the analysis above, write a one-paragraph summary."},
    ])


if __name__ == "__main__":
    result = research(topic="Why Rust is gaining popularity in systems programming")
    print(f"\n📝 Summary:\n{result}")
```

Save this as `demo.py` and run with `python demo.py`.

---

## Key Concepts

| Concept | What It Is |
|---------|-----------|
| `@agentic_function` | Decorator. Records each call as a node in the session DAG |
| `runtime.exec()` | Calls the LLM — context is computed from the DAG automatically |
| Session DAG | Every user message / LLM call / function call is a node — see `openprogram/context/` |
| Docstring | Documents the function; the per-call prompt lives in `runtime.exec(content=...)` |

### The Core Pattern

```python
@agentic_function
def my_function(param):
    """This docstring IS the prompt. The LLM reads it."""

    data = do_something_deterministic(param)   # Python: guaranteed execution
    result = runtime.exec(content=[...])       # LLM: reasoning step
    return result                              # Python: guaranteed return
```

**Python controls flow. LLM does reasoning. That's the whole idea.**

---

## Next Steps

- 📖 [API Reference](API.md)
- 🔗 [Claude Code Integration](INTEGRATION_CLAUDE_CODE.md) — Use without any API key
- 🔗 [OpenClaw Integration](INTEGRATION_OPENCLAW.md) — Use as OpenClaw skill/tool
- 📂 [Examples](../examples/) — More runnable demos

---

---

<a id="中文"></a>

## 🚀 3 分钟快速上手

### 第 1 步：安装

一条命令装齐——Python 包 + 网页 UI + 终端 UI + GUI agent（含模型权重/OCR）：

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

需要 Python ≥ 3.11、Node ≥ 20、git。PyTorch 会自动识别显卡（有 N 卡装 CUDA 版，否则 CPU；可用 `--cpu` / `--cuda cu124` 强制）。只要 host、不装 GUI？加 `--no-gui`。完整依赖矩阵与参数见 [docs/install.md](install.md)。

### 第 2 步：写你的第一个 Agentic Function

```python
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# ClaudeCodeRuntime 使用 Claude Code CLI，不需要 API key
runtime = ClaudeCodeRuntime(model="haiku")

@agentic_function
def greet(name):
    """用创意的方式跟人打招呼。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"用创意的方式跟 {name} 打招呼，简短一点（1-2 句话）。"},
    ])

result = greet(name="World")
print(result)
```

### 第 3 步：运行

```bash
python your_script.py
```

完成。你的函数现在**会思考**了。

---

## 选择 Provider

Agentic Programming 内置 6 个 runtime / provider，选一个：

### 方案 A：Claude Code CLI（推荐新手使用）

**不需要 API key。** 使用你的 Claude Code 订阅。

**前置条件：**
```bash
npm install -g @anthropic-ai/claude-code
claude login
```

**用法：**
```python
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")
```

**优点：** 零配置，用已有订阅。
**缺点：** 比直接 API 慢（子进程开销），仅支持文本。

---

### 方案 B：Anthropic API（Claude）

**适合生产环境。** 直接 API 访问，支持 prompt caching。

**配置：**
```bash
pip install -e .          # anthropic SDK is included by default
export ANTHROPIC_API_KEY="sk-ant-..."
```

**用法：**
```python
from openprogram.providers import AnthropicRuntime

runtime = AnthropicRuntime(
    model="claude-sonnet-4-6",
    # api_key="sk-ant-..."  # 或者用 ANTHROPIC_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、prompt caching、系统提示词。

---

### 方案 C：OpenAI API（GPT）

**配置：**
```bash
pip install -e .          # openai SDK is included by default
export OPENAI_API_KEY="sk-..."
```

**用法：**
```python
from openprogram.providers import OpenAIRuntime

runtime = OpenAIRuntime(
    model="gpt-4o",
    # api_key="sk-..."  # 或者用 OPENAI_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、response_format（JSON 模式）、系统提示词。

---

### 方案 D：Google Gemini API

**配置：**
```bash
pip install -e .          # google-genai SDK is included by default
export GOOGLE_API_KEY="..."
```

**用法：**
```python
from openprogram.providers import GeminiRuntime

runtime = GeminiRuntime(
    model="gemini-2.5-flash",
    # api_key="..."  # 或者用 GOOGLE_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、系统指令、JSON schema 输出。

---

### 方案 E：Codex CLI

**不需要在 Python 里配置 API key。** 直接复用你已经登录好的 Codex CLI。

**前置条件：**
```bash
# 先安装 Codex CLI，然后登录
codex login --device-auth
```

**用法：**
```python
from openprogram.providers import OpenAICodexRuntime

runtime = OpenAICodexRuntime(model="gpt-5.5")
```

**优点：** 本地 CLI 工作流友好，适合已经在用 Codex 的环境。
**缺点：** 有子进程开销，仅支持文本。

---

### 方案 F：Gemini CLI

**不需要在 Python 里配置 API key。** 直接使用你机器上的 Gemini CLI 会话。

**前置条件：**
```bash
# 先安装 Gemini CLI，然后登录
gemini
```

**用法：**
```python
from openprogram.providers import GeminiCLIRuntime

runtime = GeminiCLIRuntime()
```

**优点：** 本地 CLI 工作流友好，不需要额外装 Python SDK。
**缺点：** 有子进程开销，仅支持文本。

---

## 完整可运行示例

把下面的脚本复制粘贴就能跑：

```python
"""
完整示例：用 Agentic Programming 做任务分解。
使用 ClaudeCodeRuntime（不需要 API key，只需 claude CLI）。
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# 初始化 runtime（不需要 API key）
runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def analyze(topic):
    """分析一个话题，列出 3 个关键点。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"列出关于以下话题的 3 个关键点：{topic}\n每行一个，编号 1-3。"},
    ])


@agentic_function
def elaborate(point):
    """用一句有洞察力的话展开一个观点。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"用一句有洞察力的话展开这个观点：\n{point}"},
    ])


@agentic_function
def research(topic):
    """分析一个话题，然后展开每个要点。"""
    # 第 1 步：获取关键点（Python 控制流程）
    points_text = analyze(topic=topic)
    print(f"📋 关键点：\n{points_text}\n")

    # 第 2 步：展开每个要点（Python 控制循环）
    lines = [l.strip() for l in points_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        detail = elaborate(point=line)
        print(f"  💡 {detail}\n")

    # 第 3 步：返回总结（LLM 自动看到完整上下文）
    return runtime.exec(content=[
        {"type": "text", "text": "根据上面的分析，写一段总结。"},
    ])


if __name__ == "__main__":
    result = research(topic="为什么 Rust 在系统编程领域越来越流行")
    print(f"\n📝 总结：\n{result}")
```

保存为 `demo.py`，运行 `python demo.py`。

---

## 核心概念

| 概念 | 作用 |
|------|------|
| `@agentic_function` | 装饰器。每次调用记录为 session DAG 的一个节点 |
| `runtime.exec()` | 调用 LLM,上下文从 DAG 自动算出 |
| Session DAG | 每条用户消息 / LLM 调用 / 函数调用都是一个节点,见 `openprogram/context/` |
| Docstring | 描述函数本身;本次调用的指令写在 `runtime.exec(content=...)` 里 |

### 核心模式

```python
@agentic_function
def my_function(param):
    """这个 docstring 就是 prompt。LLM 会读到它。"""

    data = do_something_deterministic(param)   # Python：确定性执行
    result = runtime.exec(content=[...])       # LLM：推理步骤
    return result                              # Python：确定性返回
```

**Python 控制流程。LLM 做推理。就这么简单。**

---

## 下一步

- 📖 [API 参考](API.md)
- 🔗 [Claude Code 集成指南](INTEGRATION_CLAUDE_CODE.md) — 不需要 API key
- 🔗 [OpenClaw 集成指南](INTEGRATION_OPENCLAW.md) — 作为 OpenClaw skill/tool 使用
- 📂 [示例](../examples/) — 更多可运行的 demo
