# Pure Python Functions

## When to Use

The task is pure deterministic logic that requires no LLM reasoning. For example:
- Word counting
- File I/O
- Data format conversion
- Mathematical computation

## Design Points

- **Do not** use the `@agentic_function` decorator
- **Do not** use `runtime.exec()`
- **No** `runtime` parameter needed
- Use a standard Google-style docstring

## Examples

```python
def word_count(text: str) -> int:
    """Count the number of words in the text.

    Args:
        text: The input text.

    Returns:
        The number of words.
    """
    return len(text.split())
```

```python
def extract_emails(text: str) -> list[str]:
    """Extract all email addresses from the text.

    Args:
        text: The input text.

    Returns:
        A list of email addresses.
    """
    import re
    return re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
```

## Context Tree

Pure Python functions do not appear in the context tree (unless the `@traced` decorator is added).

If you want the call to show up in the execution tree, add `@traced`:

```python
from openprogram.agentic_programming.function import traced

@traced
def word_count(text: str) -> int:
    """Count the number of words in the text."""
    return len(text.split())
```

## When to Use Pure Python vs. @agentic_function

| Criterion | Pure Python | @agentic_function |
|---------|----------|-------------------|
| Deterministic input → deterministic output | ✓ | |
| Requires understanding semantics | | ✓ |
| Requires generating natural language | | ✓ |
| Requires classification/judgment/reasoning | | ✓ |
| Has a clear algorithm/rule | ✓ | |
