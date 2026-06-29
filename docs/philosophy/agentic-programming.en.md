# Agentic Programming — Design Philosophy

> OpenProgram is the productized implementation of the Agentic Programming paradigm.
> This document is about the paradigm itself: what problem it solves, why it inverts control, and what its core primitives are.

## The Problem

Every current LLM Agent framework hands control to the model:
- **What to do** is decided by the LLM (a planner plans first, then the agent executes)
- **When to do it** is decided by the LLM (a while loop runs until the agent says "I'm done")
- **How to do it** is decided by the LLM (tool calls, arguments, ordering)

The cost:
- **Unpredictable execution** — the same input produces a different trajectory every time
- **Context explosion** — every step crams the history back into the model
- **No output guarantees** — no one can say "this task is guaranteed to run to completion"
- **Debugging hell** — when something breaks, you can't tell whether it's a prompt problem, a tool problem, or a model hallucination

The root cause: **using a black-box probabilistic system to do work that could have been done with deterministic code in the first place**.

## The Inversion: Python Controls Flow, the LLM Reasons

Agentic Programming gives control back to the programmer:

| Dimension | Traditional Agent | Agentic Programming |
|------|-----------|---------------------|
| Flow | LLM plans | Python code |
| Decisions | LLM judges at every step | Python decides whether to call the LLM |
| State | Crammed into the context | Function variables, return values |
| Testability | Prompt regression | Unit tests |

Decompose a complex task into a function call graph. For each node on the graph, you decide:
- **Doesn't need reasoning** — use a plain Python function
- **Needs understanding / generation / judgment** — decorate it with `@agentic_function`, and call `runtime.exec(...)` inside the function body to trigger the LLM

The LLM becomes a tool that you call, constrain, and compose.

## The Three Primitives

The entire paradigm is just three things:

### 1. `@agentic_function`

A decorator. For a function it decorates, the docstring automatically becomes the instruction given to the LLM, and `runtime.exec(...)` triggers the model call.

```python
from openprogram import agentic_function

@agentic_function
def summarize(text: str) -> str:
    """Summarize this content in one sentence, preserving the core point."""
    return runtime.exec(content=[{"type": "text", "text": text}])
```

External callers can't tell the difference — `summarize(article)` looks just like any Python function.

### 2. `Runtime`

The runtime abstraction for LLM calls. It is responsible for:
- Packaging the current conversation history
- Calling the underlying provider (Anthropic / OpenAI / Claude Code / ...)
- Writing the result back into the context

`Runtime.exec()` is the only LLM entry point. Every model call goes through it.

### 3. `Context`

The automatic record of the function call graph. Every time an `@agentic_function` is entered/returned or `runtime.exec()` is triggered, a node is attached to a tree. Each node records: inputs, outputs, token usage, elapsed time, failure reason.

This tree **is not shown to the model** (unless you deliberately splice it into the prompt). It's shown to you — for debugging, visualization, and replaying failure paths.

The context isn't the selling point; it's a byproduct of LLM calls. The selling point is that "you can call the model seamlessly inside a function".

## Derived Concepts

### The LLM Writes Code Too

The LLM isn't just the runtime's reasoning engine; it can also **write code** — generating, modifying, and fixing `@agentic_function`s that conform to the spec. This doesn't need dedicated `create()` / `fix()` framework functions (they too used to just wrap one LLM call plus one file write); the agent does it directly with ordinary file-editing tools, following the [`agentic-programming` skill](../../skills/agentic-programming/SKILL.md) as the spec — where files go, decorator metadata, the division of labor between the docstring and `content`, and the validation checklist.

Code is data, the LLM is the compiler, and functions are the product — the loop closes.

### Dual Mode

Agentic Programming is at the same time:
- **A library** — you write `@agentic_function`s and wire up the pipeline by hand
- **A CLI** — `openprogram create "task description" --name my_fn`, letting the LLM write it for you

Beginners start with the CLI, and the generated code is a complete, readable Python file. Those who want to dig deeper can then import and hand-write. This is a tool that **can be understood incrementally**.

## Comparison with Traditional Agent Frameworks

| Scenario | LangChain / AutoGPT | Agentic Programming |
|------|---------------------|---------------------|
| "Fetch 10 pages and generate a summary for each" | The agent decides ordering and parallelism itself | Python writes `for url in urls: summarize(fetch(url))` |
| "Remember context across 3 consecutive conversations" | Stuff the conversation into a memory store and query it each time | It's just a local variable in a Python function |
| "Let the LLM decide which tool to call" | function calling + agent loop | Write `tool = runtime.exec(...); dispatch(tool)` |
| "Retry on error" | The agent decides itself | `try / except + retry` |

This isn't to say agent frameworks are wrong; they suit a class of tasks (fully open-ended, with fuzzy goals). But most of what you want to do can actually be done more reliably with Agentic Programming.

## OpenProgram = the Productized Paradigm

The `agentic_programming/` subpackage is the paradigm's engine code. `providers/` adapts the various LLMs. `programs/` holds the functions and applications already written under this paradigm. `webui/` lets beginners run things without writing code.

The paradigm comes first; the product exists to use it.

---

Further reading:
- [Getting Started](../GETTING_STARTED.md)
- [API Reference](../api/)
- [Design Details](../design/)
