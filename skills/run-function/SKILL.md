---
name: run-function
description: "Run a saved agentic function by name with arguments. Use 'agentic list' to see available functions. Triggers: 'run function', 'execute function', 'call function', 'use function', 'run sentiment', 'run extract'."
---

# Run Function

Run a previously created agentic function.

```bash
agentic run <NAME> --arg key=value
```

List available functions first:

```bash
agentic list
```

## Examples

```bash
agentic run sentiment --arg text="I love this project"
agentic run extract_domain --arg url="https://github.com/Fzkuji"
agentic run word_count --arg text="hello world"
```

## Options

| Flag | Description |
|------|-------------|
| `--arg`, `-a` | Arguments as key=value (repeatable) |
| `--provider`, `-p` | LLM provider override (for functions that use LLM) |
| `--model`, `-m` | Model override |
