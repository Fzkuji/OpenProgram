# Pure Python

## When to use

The task is pure deterministic logic with no need for LLM reasoning. For
example:
- Word counting
- File reading / writing
- Data format conversion
- Math

## Design points

- Do **not** use the `@agentic_function` decorator
- Do **not** call `runtime.exec()`
- No `runtime` parameter needed
- Use a standard Google-style docstring

## Examples

```python
def word_count(text: str) -> int:
    """Count the number of words in a text.

    Args:
        text: Input text.

    Returns:
        The word count.
    """
    return len(text.split())
```

```python
def extract_emails(text: str) -> list[str]:
    """Extract every email address from a text.

    Args:
        text: Input text.

    Returns:
        List of email addresses.
    """
    import re
    return re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
```

## Session DAG

Pure Python functions leave no node on the session DAG (unless decorated
with `@traced`).

If you want the call recorded on the DAG, add `@traced`:

```python
from openprogram.agentic_programming.function import traced

@traced
def word_count(text: str) -> int:
    """Count the number of words in a text."""
    return len(text.split())
```

The node records the function name, the bound arguments (with `self`/`cls`/`runtime`/`callback` stripped), and the return value, with `expose` fixed to `'io'`. `async def` functions are also supported.

## Pure Python vs. @agentic_function

| Criterion | Pure Python | @agentic_function |
|---------|----------|-------------------|
| Fixed input → fixed output | ✓ | |
| Needs semantic understanding | | ✓ |
| Needs natural-language generation | | ✓ |
| Needs classification / judgement / reasoning | | ✓ |
| Has a clear algorithm / rule | ✓ | |
