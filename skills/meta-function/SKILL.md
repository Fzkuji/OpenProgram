---
name: meta-function
description: "Create, fix, or publish Python functions using Agentic Programming. Use when: (1) need a new function from a description, (2) need to fix a broken function, (3) want to publish a function as a skill for other agents. Triggers: 'create a function', 'generate a function', 'fix this function', 'make a skill', 'publish as skill'."
---

# Meta Function

Create, fix, and publish Python functions using LLM.


## Create a new function

```python
from agentic.meta_function import create
from agentic.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime()
fn = create("<DESCRIPTION>", runtime=runtime, name="<NAME>")
result = fn(<PARAMS>)
```

- Deterministic tasks → generates pure Python
- Reasoning tasks → generates `@agentic_function` with `runtime.exec()`
- Auto-saved to `agentic/functions/<NAME>.py`
- Add `as_skill=True` for a quick skill template:

```python
fn = create("...", runtime=runtime, name="my_tool", as_skill=True)
```

## Fix a function

```python
from agentic.meta_function import fix

fixed = fix(fn=broken_fn, runtime=runtime, instruction="<WHAT_TO_CHANGE>")
```

## Create a skill (LLM-written)

Use `create_skill()` to generate a SKILL.md with LLM-written description and trigger words:

```python
from agentic.meta_function import create_skill

path = create_skill(
    fn_name="my_tool",
    description="What the function does",
    code=source_code,
    runtime=runtime,
)
# → skills/my_tool/SKILL.md
```

Use for top-level entry-point functions that agents should discover.
Don't use for internal helpers.
