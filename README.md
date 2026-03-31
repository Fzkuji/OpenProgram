# LLM Agent Harness

A programming framework where LLM sessions are the runtime.

## Core Idea

Traditional agent frameworks let the LLM decide everything — what to do, in what order, when to stop. This framework inverts that:

- **You** define Functions with typed inputs and outputs
- **The LLM Session** executes each Function
- **The framework** guarantees the return value matches the declared type before moving on

```python
# A Function is like a regular Python function — but executed by an LLM
observe = Function(
    name="observe",
    docstring="Observe the current screen state.",
    body="Take a screenshot and identify all visible UI elements...",
    params=["task"],
    return_type=ObserveResult,   # must return this, or retry
)

result = observe.call(session, context)  # returns ObserveResult — guaranteed
```

## Concepts

| Concept | Analogy | Description |
|---------|---------|-------------|
| `Function` | Python function | A named, typed unit of execution with a docstring, body, params, and return type |
| `Session` | Language runtime | The LLM (or agent) that executes the Function — pluggable |
| `Workflow` | Program | An ordered sequence of Function calls |
| `FunctionCall` | Function call | Binds a Function to a Session for use in a Workflow |
| `FunctionError` | RuntimeError | Raised when a Function fails to return a valid value after all retries |
| `body` | Function body | Natural language instructions — the Skill content |
| `params` | Parameters | Which context keys this Function reads as input |
| `return_type` | Return type annotation | Pydantic model the Function must return |
| `call()` | Calling a function | Executes the Function using a Session |

## Project Structure

```
harness/
├── function/    # Function definition and execution
├── session/     # Session interface and implementations
└── workflow/    # Workflow orchestration

skills/          # Natural language Skill files (body content)
├── observe/SKILL.md
├── learn/SKILL.md
├── act/SKILL.md
└── verify/SKILL.md

tests/           # Unit tests
examples/        # Usage examples
docs/            # Design documents
```

## Quick Start

```python
from pydantic import BaseModel
from harness import Function, Workflow, FunctionCall
from harness.session import AnthropicSession

# 1. Define return types
class ObserveResult(BaseModel):
    current_state: str
    elements_found: list[str]
    is_target_visible: bool

# 2. Define Functions
observe = Function(
    name="observe",
    docstring="Observe the current screen state and identify UI elements.",
    body=open("skills/observe/SKILL.md").read(),
    return_type=ObserveResult,
    params=["task"],
)

# 3. Create a Session (the runtime)
session = AnthropicSession()

# 4. Call a single Function
result = observe.call(session, context={"task": "Click the login button"})
print(result.current_state)

# 5. Or run a full Workflow
workflow = Workflow(
    calls=[
        FunctionCall(function=observe),
        FunctionCall(function=learn),
        FunctionCall(function=act),
        FunctionCall(function=verify),
    ],
    default_session=session,
)
result = workflow.run(task="Click the login button")
```

## Sessions

Any class that implements `send(message: str) -> str` is a valid Session:

| Session | Description |
|---------|-------------|
| `AnthropicSession` | Direct Anthropic API — full control |
| `OpenClawSession` | Routes through OpenClaw agent — uses its memory and tools |
| `NanobotSession` | Routes through nanobot agent |

Custom Session:

```python
from harness.session import Session

class MySession(Session):
    def send(self, message: str) -> str:
        # call your LLM or agent here
        return reply
```

## Install

```bash
pip install -r requirements.txt
```

## Run Tests

```bash
pip install pytest
pytest tests/ -v
```

## Design

See [docs/DESIGN.md](docs/DESIGN.md) for the full design document.
