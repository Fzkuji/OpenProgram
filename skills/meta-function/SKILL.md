---
name: meta-function
description: "Create, fix, or publish Python functions using Agentic Programming. Use when: (1) need a new function from a description, (2) need to fix a broken function, (3) want to publish a function as a skill. Triggers: 'create a function', 'generate a function', 'fix this function', 'make a skill'."
---

# Meta Function

Create, fix, and publish functions via CLI.

## Create a new function

```bash
agentic create "<DESCRIPTION>" --name <NAME>
```

Add `--as-skill` to also generate a SKILL.md.

## Fix a function

```bash
agentic fix <NAME> --instruction "<WHAT_TO_CHANGE>"
```

## Run a function

```bash
agentic run <NAME> --arg key=value
```

## List all functions

```bash
agentic list
```

## Publish as skill

```bash
agentic create-skill <NAME>
```
