# Self-Programming AI Agents

A self-programming AI agent can create or revise executable workflows while it
works. In OpenProgram, those workflows use the `@agentic_function` decorator:
the agent edits a source file with normal tools, the runtime validates and loads the
function, and later turns can call it from the same registry as existing tools.

This is narrower than unrestricted self-modification. OpenProgram does not let a
model silently replace the runtime or bypass validation. The editable unit is a
reviewable function with a declared interface, explicit tool access, recorded
execution, and normal source control.

## What the agent programs

An agentic function combines deterministic control flow with model decisions:

```python
from openprogram import agentic_function


@agentic_function
def review_then_revise(draft: str, runtime=None) -> str:
    """Review a draft, then revise it against the review."""
    review = runtime.exec(
        content=f"Identify concrete defects in this draft:\n\n{draft}",
        toolset="none",
    )
    return runtime.exec(
        content=f"Revise the draft using this review:\n\n{review}\n\n{draft}",
        toolset="none",
    )
```

The Python body fixes the required order. The model handles the two semantic
steps. Each call remains visible in OpenProgram's execution context.

## How an agent creates one

Install and start OpenProgram:

```bash
curl -fsSL https://openprogram.io/install | sh
openprogram
```

Then ask the agent for a bounded workflow, for example:

```text
Create an agentic function named review_then_revise. It must review a draft,
revise it against the review, expose only its final output, and include a smoke
test. Show me the diff before committing it.
```

The bundled
[`agentic-programming` skill](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/skills_bundled/agentic-programming/SKILL.md)
defines the file layout, decorator contract, validation steps, and smoke tests.
A watcher can load an approved function without restarting the worker.

## Runtime controls that still apply

- Tool access comes from the function's explicit runtime call and configured
  approval policy.
- Function calls, model calls, and their context relationships are recorded in
  the session DAG.
- Resource limits, cancellation, structured output validation, and provider
  accounting remain runtime responsibilities.
- Source changes remain ordinary files: they can be inspected, tested,
  reverted, and reviewed before publication.

## When to use it

Use self-programming when a repeated task needs a reusable, inspectable process
but the semantic steps still require model judgment. Use a plain function when
the entire operation is deterministic. Use an existing tool when the operation
already has a stable implementation.

Read the [Agentic Programming guide](README.md),
the [`@agentic_function` reference](../../reference/api/agentic-function.md), and the
[design rationale](philosophy.md) for the full
execution contract.
