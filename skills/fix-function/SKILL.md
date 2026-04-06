---
name: fix-function
description: "Fix a broken agentic function using LLM analysis. Reads source code and error history, then rewrites the function. Triggers: 'fix this function', 'fix function', 'this function is broken', 'repair', 'rewrite function', 'function doesn't work'."
---

# Fix Function

Rewrite a broken function by analyzing its source code and error history. The LLM diagnoses the root cause and produces a corrected version.

```bash
agentic fix <NAME> --instruction "<WHAT_TO_CHANGE>"
```

## Examples

```bash
agentic fix sentiment --instruction "return JSON with score instead of plain text"
agentic fix extract_emails --instruction "handle URLs that contain @ symbols"
agentic fix word_count
```

If `--instruction` is omitted, the LLM infers what to fix from the error history.

## Options

| Flag | Description |
|------|-------------|
| `--instruction`, `-i` | What to change (optional, LLM infers if omitted) |
| `--provider`, `-p` | LLM provider override |
| `--model`, `-m` | Model override |
