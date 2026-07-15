# Agentic workflows

This page covers the ready-made agents that ship with OpenProgram: how to install them and how to manage them. If you want to use agents directly instead of writing your own functions, start here.

## What they are

An agentic workflow is a finished workflow built with [Agentic Programming](../agentic-programming/README.md) — called a **harness** or **agentic program** in the code: a self-contained git repository holding a set of `@agentic_function`s. Once installed, its functions register into OpenProgram and appear like built-in functions in chat, on the Web UI Functions page, and in `openprogram programs run`.

Three first-party workflows:

| Workflow | Install name | In one line |
|---|---|---|
| [GUI Agent](gui-agent.md) | `gui` | Give it a task in one sentence and it operates the desktop autonomously (screenshot, detect, click, verify loop) |
| [Research Agent](research-agent.md) | `research` | From research topic to submission-ready paper, with a deterministic verification layer |
| [Wiki Agent](wiki-agent.md) | `wiki` | Distills sessions and notes into a templated HTML knowledge base |

## Management commands

```bash
openprogram programs list          # all registered functions and programs
openprogram programs available     # installable items + status of installed third-party harnesses
openprogram programs install gui   # gui | research | wiki | all
openprogram programs install <owner>/<repo>   # any third-party harness (git URL also works)
openprogram programs install <ref> --upgrade  # reinstall / upgrade
openprogram programs uninstall research       # uninstall
openprogram programs run <name> -a key=value  # run a program directly
```

`programs run` also accepts `--provider` (claude-code / openai-codex / gemini-cli / anthropic / openai / gemini, auto-detected by default) and `--model` to override the model.

`programs install` clones the harness into `openprogram/functions/agentics/` and installs the dependencies its own `pyproject.toml` / `requirements.txt` declares. Everything under that directory — an installed harness or a manual `git clone` — auto-registers the next time the worker restarts.

## How to trigger them

- **In chat**: entry functions register as tools (`as_tool=True`). Describe the task in natural language and the model calls them (e.g. `gui_agent`, `research_agent`, `wiki_agent`).
- **From the command line**: `openprogram programs run gui_agent -a task="Open Firefox"`.
- **From Python**: harness functions are ordinary importable Python functions.

## Writing your own

Any repository that follows the directory contract (`<package>/agentics/__init__.py` exposing `AGENTIC_FUNCTIONS`) can be installed with the same `programs install` command. See [Installing and writing harnesses](../installing-harnesses.md) for the contract, a minimal template, and the publishing flow.
