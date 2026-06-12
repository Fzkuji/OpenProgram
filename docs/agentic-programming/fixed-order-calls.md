# @agentic_function calling sub-functions in a fixed order

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
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> dict:
    """Run the full research pipeline: survey → find gaps → generate ideas.

    Args:
        task: Research topic.
        runtime: LLM runtime instance.

    Returns:
        Result dict containing survey, gaps, and ideas.
    """
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## Example: one exec call to summarise

```python
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> str:
    """Run the full research pipeline and summarise the results.

    Args:
        task: Research topic.
        runtime: LLM runtime instance.

    Returns:
        The consolidated research summary.
    """
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

## Context tree

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

```python
survey = survey_topic(topic=task, runtime=runtime)
if not survey or "error" in survey.lower():
    return {"error": "Survey failed", "survey": survey}

gaps = identify_gaps(survey=survey, runtime=runtime)
```

## Versus "LLM-selected calls"

| | Fixed-order calls | LLM-selected calls |
|---|-----------|-------------|
| Who decides the call order | Python code | The LLM |
| How many sub-functions run | Several, all of them | One, chosen |
| Needs a function registry | No | Yes |
| Flexibility | Fixed pipeline | Varies with the task |
