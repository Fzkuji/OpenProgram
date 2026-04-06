---
name: create-app
description: "Generate a complete runnable Python app with LLM runtime, agentic functions, argparse, and main(). Triggers: 'create an app', 'build an app', 'generate a script', 'make a tool', 'scaffold an application'."
---

# Create App

Generate a self-contained Python script with `create_runtime()`, `@agentic_function` definitions, argparse, and a `main()` entry point.

```bash
agentic create-app "<DESCRIPTION>" --name <NAME>
```

The generated app:
- Auto-detects the LLM provider via `create_runtime()`
- Accepts `--provider` and `--model` flags for user override
- Python controls all flow; LLM only reasons inside `@agentic_function`

## Examples

```bash
agentic create-app "A tool that summarizes articles from URLs" --name summarizer
agentic create-app "A CLI that takes a topic and generates a mini-lesson" --name mini_lesson
agentic create-app "Analyze a codebase and generate a review report" --name code_reviewer
```

Run the generated app:

```bash
python agentic/apps/summarizer.py "https://example.com/article"
python agentic/apps/mini_lesson.py "quantum computing" --provider openai
```

## Options

| Flag | Description |
|------|-------------|
| `--name`, `-n` | App name, used as filename (default: app) |
| `--provider`, `-p` | LLM provider override |
| `--model`, `-m` | Model override |
