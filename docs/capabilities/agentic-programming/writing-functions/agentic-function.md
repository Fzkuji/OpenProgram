# @agentic_function

`@agentic_function` wraps a Python function whose body may run LLM calls through
`runtime.exec()`. The wrapper records the function call in the session DAG unless
`expose="hidden"` is used.

This page explains the usage patterns. Metadata rules live in
[`function-metadata.md`](function-metadata.md).

## Basic pattern

```python
from openprogram import agentic_function

@agentic_function(input={
    "text": {"description": "Text to translate."},
})
def translate_to_chinese(text: str, runtime=None) -> str:
    """Translate text to Chinese."""
    return runtime.exec(content=[{"type": "text", "text": (
        "Translate the following text to Chinese. Return only the translation.\n\n"
        f"Text:\n{text}"
    )}])
```

Declare a `runtime` parameter and never pass it yourself: the framework
injects it on direct Python calls and on tool dispatch alike (a missing or
`None` value is filled before the signature is enforced), and filters it out
of the tool schema so the model never sees it. `runtime=None` is the
conventional spelling; a bare `runtime` without a default also works.

The docstring is the function-level description. The `content` block is the
actual instruction and data for this LLM call.

## Direct composition

Use direct Python calls when the order is fixed.

```python
@agentic_function(input={
    "task": {"description": "Research task."},
})
def research_pipeline(task: str, runtime) -> dict:
    """Run a fixed research pipeline."""
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)
    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## LLM-selected tools

Use `runtime.exec(tools=[...])` when the model should choose from a specific
menu of functions. Note that a bare `runtime.exec(content=...)` is not
tool-free: with neither `tools=` nor `toolset=` passed, the call resolves the
full registry toolset by default, so the model can already search, run code,
and edit files. Pass `toolset="none"` (or `tools=[]`) for a pure reasoning
call — see [`tool-calling.md`](../choosing-the-next-step/tool-calling.md).

```python
@agentic_function(input={
    "task": {"description": "User task."},
})
def research_assistant(task: str, runtime) -> str:
    """Choose and run the appropriate research helper."""
    return runtime.exec(
        content=[{"type": "text", "text": (
            "Choose the appropriate helper for this task and complete the work.\n\n"
            f"Task:\n{task}"
        )}],
        tools=[survey_topic, identify_gaps, generate_ideas],
    )
```

`@agentic_function` provides `.spec` and `.execute`, so decorated functions can
be passed directly into `tools=[...]`.

Besides direct composition and tools, a third way to pick the next step is a
decision menu via `exec(choices=...)` or `decision.make` — see
[`next-step-decision.md`](../choosing-the-next-step/next-step-decision.md).

## Decorator fields

The decorator fields (`expose`, `render_range`, `input`,
`system`, `workdir_mode`, …) are documented in **one place**:
[`function-metadata.md`](function-metadata.md) §3. The API reference at
[`../../../reference/api/agentic-function.md`](../../../reference/api/agentic-function.md) carries
a condensed quick-reference table.

This page covers usage *patterns*; it intentionally does not duplicate
the field-by-field reference. If you're looking for what a field does,
go to `function-metadata.md`.
