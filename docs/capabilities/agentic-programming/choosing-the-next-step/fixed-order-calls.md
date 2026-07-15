# Fixed-order calls

Call the LLM (optionally) while invoking multiple sub-functions in an order
hard-coded in Python.

## When to use

- Research pipeline: survey → find gaps → generate ideas
- Paper pipeline: draft → review → revise
- Data pipeline: collect → clean → analyze
- Any multi-step task whose step order is known ahead of time

## Design points

- Use the `@agentic_function` decorator
- Call multiple sub-`@agentic_function`s in a fixed order
- `exec()` is optional: skip it (pure chaining), or call it multiple times
  (each call creates one exec child node)
- Data flows between sub-functions through plain Python variables
- One function may call `exec()` multiple times AND call any number of other
  `@agentic_function`s

## Example: no exec, pure chaining

```python
from openprogram import agentic_function

@agentic_function(input={
    "task": {"description": "Research topic."},
})
def research_pipeline(task: str, runtime=None) -> dict:
    """Run the full research pipeline: survey, find gaps, generate ideas."""
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## Example: one exec call to summarise

```python
@agentic_function(input={
    "task": {"description": "Research topic."},
})
def research_pipeline(task: str, runtime=None) -> str:
    """Run the full research pipeline and summarise the results."""
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Survey:\n{survey}\n\n"
            f"Gaps:\n{gaps}\n\n"
            f"Ideas:\n{ideas}"
        )},
    ])
```

## Session DAG

Each call is one node; the `caller` edge points at the orchestrator:

```
research_pipeline
├── survey_topic       ← step 1
├── identify_gaps      ← step 2
└── generate_ideas     ← step 3
```

## Passing data between steps

Sub-functions hand data to each other through Python variables — no LLM
involved:

```python
survey = survey_topic(topic=task, runtime=runtime)
gaps = identify_gaps(survey=survey, runtime=runtime)
```

The return value of `survey_topic` goes straight in as the input argument of
`identify_gaps`.

## Inserting Python processing between steps

```python
survey = survey_topic(topic=task, runtime=runtime)

# plain Python processing in between
key_points = extract_key_points(survey)
filtered = [p for p in key_points if p["relevance"] > 0.5]

gaps = identify_gaps(survey="\n".join(filtered), runtime=runtime)
```

## Error handling

The primary mechanism is exception propagation: when a sub-function raises,
its DAG node is recorded with `status='error'` and the exception re-raises
into the orchestrator. Catch it there with a plain `try/except`:

```python
try:
    survey = survey_topic(topic=task, runtime=runtime)
except Exception as e:
    return {"error": f"Survey failed: {e}"}

gaps = identify_gaps(survey=survey, runtime=runtime)
```

Optionally, if a sub-function reports failure in-band (returning an error
string instead of raising), check the value:

```python
survey = survey_topic(topic=task, runtime=runtime)
if not survey or "error" in survey.lower():
    return {"error": "Survey failed", "survey": survey}

gaps = identify_gaps(survey=survey, runtime=runtime)
```

## Versus "LLM-selected calls"

| | Fixed-order calls | [Tool calling](tool-calling.md) / [next-step decision](next-step-decision.md) |
|---|-----------|-------------|
| Who decides the call order | Python code | The LLM |
| How many sub-functions run | Several, all of them | Tool loop: many, across rounds; decision menu: one |
| Flexibility | Fixed pipeline | Varies with the task |
