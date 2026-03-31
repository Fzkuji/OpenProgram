# LLM Agent Harness

A session-typed LLM programming framework. The LLM is the runtime, not the orchestrator.

## Core Idea

Traditional agent frameworks let the LLM decide everything. This framework inverts that:
- **You** define the structure (Steps, Workflows, schemas)
- **The LLM** executes within that structure
- **The framework** guarantees outputs match schemas before proceeding

## Concepts

- **Step** — a typed function executed by an LLM session
- **Session** — the runtime (OpenClaw, nanobot, Anthropic API, anything that can send/receive messages)
- **Workflow** — an ordered sequence of Steps
- **Skill** — natural language instructions inside a Step (the "comments" of the function)
- **Context** — shared state that flows through the Workflow

## Project Structure

```
harness/
├── step/        # Step definition and execution
├── session/     # Session interface and implementations
└── workflow/    # Workflow orchestration

docs/            # Design documents
examples/        # Usage examples
```

## Quick Start

```python
from harness.step import Step
from harness.session import AnthropicSession
from harness.workflow import Workflow
from pydantic import BaseModel

class ObserveResult(BaseModel):
    current_state: str
    elements_found: list[str]

step = Step(
    name="observe",
    description="Observe the current screen state",
    instructions=open("skills/observe/SKILL.md").read(),
    output_schema=ObserveResult,
)

session = AnthropicSession()
result = step.run(session=session, context={"task": "Click the login button"})
```
