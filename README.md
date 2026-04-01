# Agentic Programming

A programming paradigm where LLM sessions are the compute units.

## Core Idea

```
Python:     result = my_func(x, y)              → CPU executes → returns result
Agentic:    result = observe(session, task="…")  → LLM executes → returns result
```

Functions are functions. You call them, you get results. The only difference: an LLM executes the logic instead of a CPU.

## Quick Start

```python
from harness import function, Session
from harness.session import AnthropicSession
from pydantic import BaseModel

# 1. Define return type
class ObserveResult(BaseModel):
    elements: list[str]
    target_visible: bool

# 2. Define function (docstring = LLM instructions)
@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Observe the current screen state.
    Take a screenshot and identify all visible UI elements.
    Report whether the target described in 'task' is visible."""

# 3. Call it — just like any Python function
session = AnthropicSession(model="claude-sonnet-4-6")
result = observe(session, task="find the login button")

print(result.elements)        # ["Login button", "Username field", ...]
print(result.target_visible)  # True
```

That's it. The `@function` decorator handles prompt assembly, output validation, and retries. You just write the function.

## Built-in Functions

Basic operations every agent needs, ready to use:

```python
from harness import ask, extract, summarize, classify, decide

# Plain text Q&A
answer = ask(session, "What is the capital of France?")

# Extract structured data
info = extract(session, "John is 30 years old", PersonInfo)

# Summarize
summary = summarize(session, long_text, max_length=50)

# Classify
sentiment = classify(session, "I love this!", ["positive", "negative", "neutral"])

# Choose from options
choice = decide(session, "Which approach?", ["Option A", "Option B"])
```

## Sessions

A Session is the LLM interface. Any class with `send(message) -> str`:

```python
from harness.session import (
    AnthropicSession,       # Anthropic API (text + images)
    OpenAISession,          # OpenAI API (text + images)
    ClaudeCodeSession,      # Claude Code CLI (subscription, images via stream-json)
    CodexSession,           # Codex CLI (subscription, images via --image)
    OpenClawSession,        # OpenClaw gateway (/v1/chat/completions)
    CLISession,             # Any CLI agent
)

# API (needs key)
session = AnthropicSession(model="claude-sonnet-4-6")

# CLI (uses subscription, no key needed)
session = ClaudeCodeSession()

# All Sessions maintain conversation history for reuse
result1 = observe(session, task="find button")
result2 = click(session, target="login")  # same session, remembers context
```

## Scope

Controls what context a function can see. Optional — only use when you need fine-grained control.

```python
from harness import Scope

# Parameters (all optional, None = don't care):
Scope(
    depth=None,      # Call stack: 0=none, 1=caller, -1=all
    detail=None,     # Per layer: "io" or "full"
    peer=None,       # Siblings: "none", "io", "full"
    compact=None,    # Compress after execution: True/False
)

# Presets
Scope.isolated()   # No context
Scope.chained()    # Sees sibling I/O
Scope.full()       # Sees everything
```

Sessions handle Scope via polymorphism:
- API Sessions read `depth/detail/peer` → inject context into history
- CLI Sessions read `compact` → fork session after execution
- No if/else needed — each Session does what makes sense for it

## Memory

Persistent execution log. Records everything that happens:

```python
from harness import Memory

memory = Memory(base_dir="./logs")
run_id = memory.start_run(task="Click login button")

# Log events
memory.log_function_call("observe", params={"task": "find button"})
memory.log_function_return("observe", result={"found": True}, duration_ms=150)

# Save media (screenshots, etc.)
path = memory.save_media("screenshot.png", source_path="/tmp/screen.png")

memory.end_run(status="success")

# Each run generates:
# logs/run_<timestamp>/
# ├── run.jsonl    ← machine-readable events
# ├── run.md       ← human-readable summary with ✓/✗ and media links
# └── media/       ← saved images
```

## Writing Your Own Functions

Two ways:

### With decorator (recommended)

```python
@function(return_type=ClickResult, max_retries=5)
def click(session: Session, target: str, method: str = "single") -> ClickResult:
    """Click a UI element on screen.
    Find the element described by 'target' and click it.
    Use the specified click method (single, double, right)."""

result = click(session, target="Login button")
```

### Manual (full control)

```python
def click(session: Session, target: str) -> ClickResult:
    prompt = f"Click the element: {target}. Return JSON with coordinates and success."
    reply = session.send(prompt)
    return ClickResult.model_validate_json(reply)

result = click(session, target="Login button")
```

## Chaining Functions

Just call them in sequence. Python is the control flow:

```python
session = AnthropicSession()

# Sequential — same session, context preserved
screen = observe(session, task="find login form")
result = click(session, target=screen.elements[0])
status = verify(session, expected="login page loaded")

# Parallel — different sessions, isolated
import asyncio
s1, s2 = AnthropicSession(), AnthropicSession()
r1, r2 = await asyncio.gather(
    observe(s1, task="check screen A"),
    observe(s2, task="check screen B"),
)
```

## Using Python Classes

Organize related functions with plain Python classes:

```python
class GUIAgent:
    def __init__(self, session: Session):
        self.session = session

    @function(return_type=ObserveResult)
    def observe(self, session: Session, task: str) -> ObserveResult:
        """Observe the screen."""

    @function(return_type=ClickResult)
    def click(self, session: Session, target: str) -> ClickResult:
        """Click a target element."""

    def find_and_click(self, target: str):
        """High-level: find something and click it."""
        screen = self.observe(self.session, task=f"find {target}")
        if screen.target_visible:
            return self.click(self.session, target=target)

agent = GUIAgent(session)
agent.find_and_click("login button")
```

## Project Structure

```
harness/
├── function/    # @function decorator + built-in functions
├── session/     # Session interface + implementations
├── scope/       # Scope: context visibility rules
└── memory/      # Memory: persistent execution log

tests/           # 53 tests
docs/
└── DESIGN.md    # Full design specification
```

## Install & Test

```bash
pip install -e .
pytest tests/ -v   # 53 tests
```

## Design

See [docs/DESIGN.md](docs/DESIGN.md) for the full specification.
