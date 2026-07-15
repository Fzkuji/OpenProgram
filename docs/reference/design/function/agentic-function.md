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
def translate_to_chinese(text: str, runtime) -> str:
    """Translate text to Chinese."""
    return runtime.exec(content=[{"type": "text", "text": (
        "Translate the following text to Chinese. Return only the translation.\n\n"
        f"Text:\n{text}"
    )}])
```

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

Use `runtime.exec(tools=[...])` when the model should choose which function to
call.

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
        tool_choice="auto",
    )
```

`@agentic_function` provides `.spec` and `.execute`, so decorated functions can
be passed directly into `tools=[...]`.

## Decorator fields

The decorator fields (`expose`, `render_range`, `input`, `no_tools`,
`system`, `workdir_mode`, …) are documented in **one place**:
[`function-metadata.md`](function-metadata.md) §3. The API reference at
[`../../api/agentic-function.md`](../../api/agentic-function.md) carries
the same table for quick lookup.

This page covers usage *patterns*; it intentionally does not duplicate
the field-by-field reference. If you're looking for what a field does,
go to `function-metadata.md`.
