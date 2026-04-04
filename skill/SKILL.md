---
name: agentic-programming
description: "Create, run, and fix LLM-powered Python functions using Agentic Programming. Use when: (1) you need to create a new function that calls an LLM, (2) a function failed and needs fixing, (3) you want to run a multi-step task with deterministic Python flow + LLM reasoning. Triggers: 'create a function', 'agentic function', 'fix this function', 'run with agentic'."
---

# Agentic Programming Skill

## What This Does

Agentic Programming lets you create Python functions that combine deterministic code with LLM reasoning. Python controls the flow, LLM only handles thinking.

## Installation

```bash
cd ~/.openclaw/workspace
git clone https://github.com/Fzkuji/Agentic-Programming.git skills/agentic-programming
pip install -e skills/agentic-programming
```

## How to Use

### Create a new function

Tell your OpenClaw agent:
> "Create an agentic function that summarizes text into 3 bullet points"

The agent runs:
```bash
python -c "
from agentic.meta_function import create
from agentic.providers import ClaudeCodeRuntime
runtime = ClaudeCodeRuntime()
fn = create('Summarize text into 3 bullet points', runtime=runtime)
print(fn(text='Your text here'))
"
```

### Run a multi-step task

Tell your agent:
> "Use agentic-programming to analyze this code: [paste code]"

The agent writes a Python script using `@agentic_function` decorators, runs it, and returns the result.

### Fix a broken function

Tell your agent:
> "Fix the summarize function, it's returning numbered lists instead of bullet points"

The agent runs:
```bash
python -c "
from agentic.meta_function import fix
from agentic.providers import ClaudeCodeRuntime
runtime = ClaudeCodeRuntime()
fixed = fix(fn=original_fn, runtime=runtime, instruction='Use bullet points not numbered lists')
"
```

## Key Concepts

- `@agentic_function` — Decorator that auto-tracks execution into a Context tree
- `Runtime` — LLM connection (`ClaudeCodeRuntime` needs no API key)
- `create(description, runtime)` — Generate new functions from natural language
- `fix(fn, runtime, instruction)` — Fix broken functions with LLM analysis
- `fn.context.tree()` — View the execution trace

## Examples

See `skills/agentic-programming/examples/` for runnable demos.
