# Agentic Programming

A programming paradigm where LLM and Python co-execute functions.

---

## Architecture

```
┌─────────────────────────────────┐
│            User                 │
│   "Open Safari, search hello"   │
└──────────────┬──────────────────┘
               ▼
┌─────────────────────────────────┐
│      Agentic Programmer         │
│   An LLM that receives tasks,   │
│   decides which Functions to     │
│   call and in what order.        │
└──────────────┬──────────────────┘
               ▼
┌─────────────────────────────────┐
│      Agentic Function            │
│   Defined by us. Docstring =     │
│   behavior. Executed by two      │
│   Runtimes working together.     │
└───────┬─────────────┬───────────┘
        ▼             ▼
┌──────────────┐ ┌──────────────────┐
│Python Runtime│ │ Agentic Runtime   │
│              │ │                   │
│ CPU executes │ │ LLM executes      │
│ deterministic│ │ reasoning via     │
│ code (OCR,   │ │ Agentic Session   │
│ click, etc.) │ │                   │
└──────────────┘ └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Agentic Session  │
                 │  Manages history, │
                 │  context, images  │
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │       LLM        │
                 │ Claude / GPT /   │
                 │ Gemini / local   │
                 └──────────────────┘
```

---

## Definitions

Every concept in this framework has a precise definition:

| Concept | Definition | Analogy |
|---------|-----------|---------|
| **Agentic Programmer** | The LLM that faces the user. Receives tasks, decides which Agentic Functions to call and in what order. | A human programmer |
| **Agentic Function** | A function we define. Its docstring describes what it does. Executed by Python Runtime + Agentic Runtime working together. | A function in source code |
| **Python Runtime** | The Python interpreter. Executes deterministic code: screenshots, OCR, detection, clicking, file I/O. | CPU |
| **Agentic Runtime** | The LLM execution engine. Handles reasoning: understanding screens, finding targets, making decisions. Accessed through Agentic Session. | LLM as a "CPU" for reasoning |
| **Agentic Session** | The interface to the Agentic Runtime. Manages conversation history, context visibility, multimodal input (text + images). | Instruction set / system calls |
| **Agentic Scope** | Intent declaration for context visibility. Controls what an Agentic Session can see (call stack depth, peer visibility, compaction). | Variable scope (LEGB) |
| **Agentic Memory** | Persistent execution log. Records every function call, result, decision, and media file. | Debug log |
| **Agentic Type** | Pydantic model that guarantees the output format of an Agentic Function. | Type signature |
| **LLM** | The underlying large language model (Claude, GPT, Gemini, local models). The hardware that powers the Agentic Runtime. | Physical CPU/GPU |

---

## How Agentic Functions Execute

An Agentic Function is **not** purely LLM or purely Python. Both Runtimes cooperate:

```python
def observe(programmer, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements."""

    # ── Python Runtime (deterministic) ──
    screenshot = take_screenshot()        # Python: capture screen
    ocr_data = run_ocr(screenshot.path)   # Python: extract text
    elements = detect_all(screenshot.path) # Python: detect UI elements
    state = identify_state(app_name)      # Python: check visual memory

    # ── Agentic Runtime (reasoning via Agentic Session) ──
    worker = create_session(model="sonnet")
    reply = worker.send({
        "text": f"Analyze this screen. Task: {task}\nOCR: {ocr_data}\nElements: {elements}",
        "images": [screenshot.path]
    })

    # ── Python Runtime (parse result) ──
    result = ObserveResult.parse(reply)   # Python: validate output

    # ── Report to Agentic Programmer (summary only) ──
    report_to_programmer(programmer, "observe", result)

    return result
```

The **Agentic Programmer** only sees: `"observe returned: {app: Discord, target: found}"`.
The **worker session** (with full OCR data, 156 elements, screenshots) is destroyed after execution.

---

## Two-Layer Session Design

```
Agentic Programmer Session (knows everything, sees only summaries)
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

- **Agentic Programmer Session** grows slowly (only summaries)
- **Worker Sessions** have full data but are destroyed after each function call
- Like Python's local variables: function returns → locals gone, only return value survives

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

# Docstring = prompt. Change the docstring → change the behavior.
```

### Call it

```python
from harness.session import ClaudeCodeSession

session = ClaudeCodeSession(model="sonnet")
result = observe(session, task="find the login button")

print(result.elements)        # ["Login button", "Username field", ...]
print(result.target_visible)  # True
```

### Built-in Agentic Functions

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

The interface to the Agentic Runtime. Any class with `send(message) -> str`:

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
    depth=None,      # Call stack: 0=none, 1=caller, -1=all  (Agentic Runtime)
    detail=None,     # Per layer: "io" or "full"              (Agentic Runtime)
    peer=None,       # Siblings: "none", "io", "full"         (Agentic Runtime)
    compact=None,    # Compress after execution                (Python Runtime)
)

# Presets
Scope.isolated()   # No context
Scope.chained()    # Sees sibling I/O
Scope.full()       # Sees everything
```

Each Agentic Session handles Scope via polymorphism:
- API Sessions: `depth/detail/peer` → inject context into history
- CLI Sessions: `compact` → fork to new session

---

## Agentic Memory

Persistent execution log:

```python
from harness import Memory

memory = Memory(base_dir="./logs")
run_id = memory.start_run(task="Click login button")
memory.log_function_call("observe", params={"task": "find button"})
memory.log_function_return("observe", result={"found": True}, duration_ms=150)
memory.save_media("screenshot.png", source_path="/tmp/screen.png")
memory.end_run(status="success")

# Output:
# logs/run_<timestamp>/
# ├── run.jsonl    ← machine-readable
# ├── run.md       ← human-readable with ✓/✗ and media links
# └── media/       ← saved images
```

---

## Comparison with Other Approaches

| | MCP / Tool Calling | Agentic Programming |
|---|---|---|
| **Direction** | LLM → Python → LLM | Python + LLM → cooperate |
| **Who decides flow** | LLM | Agentic Programmer (LLM) or human code |
| **Functions contain** | Python code (CPU executes) | Docstring (Python Runtime + Agentic Runtime) |
| **Execution** | Single runtime (CPU) | Dual runtime (Python + LLM) |
| **Context management** | Implicit (one conversation) | Explicit (Agentic Scope) |

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
