# Getting Started

## 🚀 3-Minute Quick Start

### Step 1: Install

The one-command installer sets up everything — Python package + web UI + terminal UI + the GUI agent (with its model weight and OCR):

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

Requires Python ≥ 3.11, Node ≥ 20, git. The default install brings up the host itself — web UI, TUI, browser tool + channels; agent programs (GUI / Research / Wiki) are picked in the installer's interactive menu or added later via `openprogram programs install <gui|research|wiki|all>` (GUI downloads PyTorch); `--minimal` installs a bare host. Full dependency matrix and flags: [docs/install.md](install.md).

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

### Option A: Claude subscription via the Meridian proxy (Recommended for Getting Started)

**No API key needed.** Uses your Claude Code subscription through a local
HTTP bridge — the `claude-code` provider talks to a Meridian daemon (which
routes through the official Claude Code SDK underneath), not a spawned CLI.

**Prerequisites:**
```bash
# 1. The Claude Code SDK + login (Meridian routes through it)
npm install -g @anthropic-ai/claude-code && claude login
# 2. The Meridian proxy daemon — exposes a local OpenAI-compatible endpoint
npm install -g @rynfar/meridian && meridian        # listens on :3456
```

(Override the port with `CLAUDE_MAX_PROXY_URL` if you ran Meridian elsewhere.)

**Usage:**
```python
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")
```

**Pros:** Zero API-key setup, uses your existing subscription, full
multimodal content (unlike the older `claude-max-api-proxy`).
**Cons:** One extra local daemon; slightly slower than a direct API key.

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
