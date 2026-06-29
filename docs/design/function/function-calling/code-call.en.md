# @agentic_function Calling Sub-Functions in a Fixed Order

Optionally calls the LLM, then invokes multiple sub-functions in the order hard-coded in the source.

## Use Cases

- Research flow: survey → find gap → generate ideas
- Paper flow: write draft → review → revise
- Data flow: collect → clean → analyze
- Any multi-step task with a fixed step order

## Design Points

- Use the `@agentic_function` decorator
- Call multiple sub-`@agentic_function`s in a fixed order
- `exec()` is optional: don't call it (pure chaining), or call it multiple times (each call creates an exec child node)
- Data is passed between sub-functions through Python variables
- A single function can call `exec()` multiple times, and can call any number of other `@agentic_function`s

## Example: No exec, Pure Chaining

```python
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> dict:
    """Run the full research flow: survey → find gap → generate ideas.

    Args:
        task: Research topic.
        runtime: LLM runtime instance.

    Returns:
        A result dict containing survey, gaps, and ideas.
    """
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## Example: Calling exec Once to Summarize

```python
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> str:
    """Run the full research flow and summarize the result.

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

## Context Tree

```
research_pipeline
├── survey_topic       ← step 1
├── identify_gaps      ← step 2
└── generate_ideas     ← step 3
```

## Passing Data Between Steps

Data is passed between sub-functions through Python variables, with no LLM involvement:

```python
survey = survey_topic(topic=task, runtime=runtime)
gaps = identify_gaps(survey=survey, runtime=runtime)
```

The return value of `survey_topic` is used directly as the input argument to `identify_gaps`.

## Inserting Python Processing Between Steps

```python
survey = survey_topic(topic=task, runtime=runtime)

# Insert ordinary Python processing in between
key_points = extract_key_points(survey)
filtered = [p for p in key_points if p["relevance"] > 0.5]

gaps = identify_gaps(survey="\n".join(filtered), runtime=runtime)
```

## Error Handling

```python
survey = survey_topic(topic=task, runtime=runtime)
if not survey or "error" in survey.lower():
    return {"error": "Survey failed", "survey": survey}

gaps = identify_gaps(survey=survey, runtime=runtime)
```

## Difference from "LLM-Chosen Calls"

| | Fixed-order calls | LLM-chosen calls |
|---|-----------|-------------|
| Who decides the call order | Python code | The LLM |
| How many sub-functions are called | Multiple, all executed | One, chosen to execute |
| Whether a function registry is required | Not required | Required |
| Flexibility | Fixed flow | Varies with the task |
