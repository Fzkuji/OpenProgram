---
name: create-function
description: "Generate a Python function from a natural language description using Agentic Programming. The LLM writes the code, the framework validates and sandboxes it. Triggers: 'create a function', 'generate a function', 'write a function that', 'make a function', 'I need a function'."
---

# Create Function

Generate an `@agentic_function` from a description. If the task is purely deterministic (no LLM reasoning needed), a plain Python function is generated instead.

```bash
agentic create "<DESCRIPTION>" --name <NAME>
```

Add `--as-skill` to also generate a SKILL.md so this function becomes discoverable as a skill:

```bash
agentic create "<DESCRIPTION>" --name <NAME> --as-skill
```

## Examples

```bash
agentic create "Analyze text sentiment, return positive/negative/neutral" --name sentiment
agentic create "Extract all email addresses from text" --name extract_emails
agentic create "Count words in a text string" --name word_count
```

## Options

| Flag | Description |
|------|-------------|
| `--name`, `-n` | Function name (required) |
| `--as-skill` | Also generate a SKILL.md |
| `--provider`, `-p` | LLM provider override |
| `--model`, `-m` | Model override |
