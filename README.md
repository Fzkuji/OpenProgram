# Agentic Programming

A programming paradigm where LLM and Python co-execute functions.

---

## Why Agentic Programming?

### 🔄 Automatic Prompt Engineering
Prompts are docstrings. Every function call runs the prompt, returns structured results, and can be iterated programmatically:
```python
for version in prompt_variants:
    fn.__doc__ = version            # change the prompt
    result = fn(session, task=test) # run it
    score = evaluate(result)        # measure
    # pick the best version automatically
```

### 🧬 Self-Evolving Agents
The **Meta Agentic Function** creates new Agentic Functions at runtime. Encounter a new task → create a new function → reuse it forever. The function library grows, the agent gets smarter:
```python
# Human or LLM calls this via MCP:
meta_create(
    name="check_email",
    docstring="Open the inbox, count unread emails, report senders and subjects",
    return_fields={"unread_count": "int", "emails": "list[dict]"}
)
# check_email() is now available as an MCP tool
```

### 🧠 Agentic Context Engineering
Context is controlled by code, not by luck. Each function decides what the LLM sees through Scope and the two-layer Session design:
- **Programmer Session**: only sees result summaries (grows slowly)
- **Worker Sessions**: have full data (destroyed after each call)
- **Scope**: controls call stack visibility, peer access, compaction

### 🔌 Drop-in Replacement
Works with existing ecosystems — no need to switch platforms:
- **MCP compatible**: register as MCP Server → any MCP client can call our functions
- **CLI compatible**: every function callable from command line

---

## Core Concepts

The entire framework has only two concepts:

```
Agentic Function        — executes a task (Python + LLM cooperate)
Meta Agentic Function   — creates new Agentic Functions (is itself an Agentic Function)
```

Everything else is infrastructure that supports these two.

### Agentic Function

A function whose logic is split between Python Runtime (deterministic code) and Agentic Runtime (LLM reasoning). The docstring IS the LLM prompt.

```python
@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements.
    Check if the target described in 'task' is visible."""

# Change the docstring → change the behavior.
# Call it → Python does OCR/detection, LLM does reasoning.
```

### Meta Agentic Function

The only "hardcoded" function. Creates all other Agentic Functions. Human or LLM calls it via MCP. The entire system bootstraps from this one function.

```python
# The Meta Agentic Function itself:
@mcp.tool()
def meta_create(name: str, docstring: str, params: dict, returns: dict) -> str:
    """Create a new Agentic Function and register it as an MCP tool."""

# Everything starts from here:
#   meta_create → Agentic Function A
#   meta_create → Agentic Function B
#   meta_create → Agentic Function C
#   ...
```

---

## Architecture

```
┌─────────────────────────────────────┐
│       Human  or  LLM Agent          │
│    (Claude Code, Codex, OpenClaw)   │
└──────────────┬──────────────────────┘
               │ MCP (JSON-RPC)
               ▼
┌─────────────────────────────────────┐
│           MCP Server                │
│  ┌─────────────────────────────┐    │
│  │   Meta Agentic Function     │    │  ← creates other functions
│  ├─────────────────────────────┤    │
│  │   Agentic Function A        │    │  ← observe, act, verify...
│  │   Agentic Function B        │    │
│  │   Agentic Function C        │    │
│  │   ...                       │    │
│  └──────────┬──────────────────┘    │
└─────────────┼───────────────────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
┌──────────────┐ ┌──────────────────┐
│Python Runtime│ │ Agentic Runtime   │
│              │ │                   │
│ Deterministic│ │ LLM reasoning     │
│ code (OCR,   │ │ via Agentic       │
│ click, etc.) │ │ Session           │
└──────────────┘ └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Agentic Session  │
                 │  (history, scope, │
                 │   images, state)  │
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │       LLM        │
                 │ Claude / GPT /   │
                 │ Gemini / local   │
                 └──────────────────┘
```

The **MCP Server** is the single entry point. Everything is called via MCP — by humans or LLMs.

---

## How Agentic Functions Execute

Both Runtimes cooperate inside every function:

```python
def observe(programmer, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements."""

    # ── Python Runtime (deterministic) ──
    screenshot = take_screenshot()
    ocr_data = run_ocr(screenshot.path)
    elements = detect_all(screenshot.path)

    # ── Agentic Runtime (reasoning via Agentic Session) ──
    worker = create_session(model="sonnet")
    reply = worker.send({
        "text": f"Analyze this screen. Task: {task}\nOCR: {ocr_data}\nElements: {elements}",
        "images": [screenshot.path]
    })

    # ── Python Runtime (parse + validate) ──
    result = ObserveResult.parse(reply)
    return result
```

---

## Two-Layer Session Design

```
Caller's Session (sees only summaries, grows slowly)
  │
  │ "observe → {app: Discord, target: found}"     ← summary
  │ "act → {clicked: login, success: true}"        ← summary
  │
  ├── observe() → Worker Session A (destroyed after)
  │     Full OCR data, 156 detected elements, screenshot
  │
  ├── act() → Worker Session B (destroyed after)
  │     Coordinate matching, template match, click execution
  │
  └── verify() → Worker Session C (destroyed after)
        Screenshot + OCR, success judgment
```

Worker Sessions have full data but are destroyed after each call. Only the return value survives.

---

## Definitions

| Concept | Definition |
|---------|-----------|
| **Agentic Function** | A function executed by Python Runtime + Agentic Runtime together. Docstring = prompt. |
| **Meta Agentic Function** | An Agentic Function that creates other Agentic Functions. The bootstrap point. |
| **Python Runtime** | The Python interpreter. Executes deterministic code: OCR, detection, clicking, file I/O. |
| **Agentic Runtime** | The LLM execution engine. Handles reasoning, accessed through Agentic Session. |
| **Agentic Session** | Interface to the Agentic Runtime. Manages history, context, multimodal input. |
| **Agentic Scope** | Intent declaration for context visibility. Controls what a Session can see. |
| **Agentic Memory** | Persistent execution log. Records calls, results, decisions, media. |
| **Agentic Type** | Pydantic model that guarantees output format. |
| **MCP Server** | The single entry point. All functions registered as MCP tools. |

---

## Quick Start

### Define an Agentic Function

```python
from harness import function, Session
from pydantic import BaseModel

class ObserveResult(BaseModel):
    elements: list[str]
    target_visible: bool

@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements.
    Check if the target described in 'task' is visible.
    List every element you can see."""
```

### Call it from Python

```python
from harness.session import AnthropicSession

session = AnthropicSession(model="sonnet")
result = observe(session, task="find the login button")
print(result.target_visible)  # True
```

### Call it from MCP (any LLM agent)

```json
// .mcp.json
{
  "mcpServers": {
    "my-functions": {
      "command": "python3",
      "args": ["mcp_server.py"]
    }
  }
}
```

Then any MCP client (Claude Code, Codex, OpenClaw) can call `observe(task="find the login button")` directly.

### Built-in Functions

```python
from harness import ask, extract, summarize, classify, decide

answer = ask(session, "What is the capital of France?")
info = extract(session, "John is 30 years old", PersonInfo)
summary = summarize(session, long_text, max_length=50)
sentiment = classify(session, "I love this!", ["positive", "negative"])
choice = decide(session, "Which approach?", ["Option A", "Option B"])
```

---

## Agentic Session

| Session | Backend | Images | History | Auth |
|---------|---------|--------|---------|------|
| AnthropicSession | Anthropic API | ✅ | We manage `_history` | API key |
| OpenAISession | OpenAI API | ✅ | We manage `_history` | API key |
| ClaudeCodeSession | Claude Code CLI | ✅ | `--session-id` | Subscription |
| CodexSession | Codex CLI | ✅ | `--session-id` | Subscription |
| OpenClawSession | OpenClaw gateway | ✅ | Server-side | Gateway token |
| CLISession | Any CLI | ❌ | None | Depends |

---

## Agentic Scope

Controls what an Agentic Session can see. All parameters optional:

```python
from harness import Scope

Scope(
    depth=None,      # Call stack: 0=none, 1=caller, -1=all
    detail=None,     # Per layer: "io" or "full"
    peer=None,       # Siblings: "none", "io", "full"
    compact=None,    # Compress after execution
)

# Presets
Scope.isolated()   # No context
Scope.chained()    # Sees sibling I/O
Scope.full()       # Sees everything
```

---

## Comparison

| | MCP / Tool Calling | Agentic Programming |
|---|---|---|
| **Direction** | LLM → Python (give LLM hands) | Python + LLM cooperate (give Python a brain) |
| **Functions contain** | Python code (CPU executes) | Docstring (Python + LLM execute) |
| **Execution** | Single runtime (CPU) | Dual runtime (Python + LLM) |
| **Context** | Implicit (one conversation) | Explicit (Agentic Scope) |
| **Self-evolving** | No | Yes (Meta Agentic Function) |

MCP is the **transport protocol** (how to call). Agentic Programming is the **execution model** (how functions run). They are orthogonal — our functions are exposed via MCP.

---

## Project Structure

```
harness/
├── __init__.py      Exports: function, ask, extract, classify, ...
├── function/        @function decorator + built-in Agentic Functions
├── session/         Agentic Session interface + 6 implementations
├── scope/           Agentic Scope: context visibility rules
└── memory/          Agentic Memory: persistent execution log

tests/               53 tests
docs/
└── DESIGN.md        Full specification with diagrams
```

## Install & Test

```bash
pip install -e .
pytest tests/ -v   # 53 tests
```

## Links

- **Design Specification**: [docs/DESIGN.md](docs/DESIGN.md)
- **GitHub**: [Fzkuji/Agentic-Programming](https://github.com/Fzkuji/Agentic-Programming)
