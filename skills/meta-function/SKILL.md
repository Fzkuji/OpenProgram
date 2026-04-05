---
name: meta-function
description: "Create, fix, or publish Python functions and apps using Agentic Programming. Use when: (1) need a new function from a description, (2) need a complete app with main entry point, (3) need to fix a broken function, (4) want to publish a function as a skill. Triggers: 'create a function', 'generate a function', 'create an app', 'build an app', 'fix this function', 'make a skill'."
---

# Meta Function

## Create a new function

```bash
agentic create "<DESCRIPTION>" --name <NAME>
```

Add `--as-skill` to also generate a SKILL.md for agent discovery.

## Create a complete app

Generate a self-contained script with runtime setup, argparse, and main():

```bash
agentic create-app "<DESCRIPTION>" --name <NAME>
```

The generated app accepts `--provider` and `--model` flags so users can choose their LLM.

## Fix a function

```bash
agentic fix <NAME> --instruction "<WHAT_TO_CHANGE>"
```

## Run a function

```bash
agentic run <NAME> --arg key=value
```

## Publish as skill

```bash
agentic create-skill <NAME>
```

## Provider override

All commands accept `--provider` and `--model`:

```bash
agentic create "..." --name foo --provider openai --model gpt-4o
agentic run foo --arg text="hello" --provider anthropic
```
