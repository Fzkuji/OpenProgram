# Agentic workflows

This page covers the ready-made agents included in every supported OpenProgram release and how to use them. If you want to use agents directly instead of writing your own functions, start here.

## What they are

An agentic workflow is a finished workflow built with [Agentic Programming](../agentic-programming/README.md) — called a **harness** or **agentic program** in the code: a self-contained git repository holding a set of `@agentic_function`s. The fixed release versions register into OpenProgram and appear like built-in functions in chat, on the Web UI Programs page, and in `openprogram programs run`.

Three first-party workflows:

| Workflow | Release status | In one line |
|---|---|---|
| [GUI Agent](gui-agent.md) | Included | Give it a task in one sentence and it operates the desktop autonomously (screenshot, detect, click, verify loop) |
| [Research Agent](research-agent.md) | Included | From research topic to submission-ready paper, with a deterministic verification layer |
| [Wiki Agent](wiki-agent.md) | Included | Distills sessions and notes into a templated HTML knowledge base |

## Management commands

```bash
openprogram programs list          # all registered functions and programs
openprogram programs available     # first-party status + installed third-party harnesses
openprogram programs install <owner>/<repo>   # any third-party harness (git URL also works)
openprogram programs install <ref> --upgrade  # reinstall / upgrade
openprogram programs uninstall <Harness-Name> # remove a third-party harness
openprogram programs run <name> -a key=value  # run a program directly
```

`programs run` also accepts `--provider` (claude-code / openai-codex / gemini-cli / anthropic / openai / gemini, auto-detected by default) and `--model` to override the model.

First-party Programs are immutable product components. In a mutable extension or development environment, `programs install` clones an additional third-party harness, installs its declared dependencies, and records the approved source.

## How to trigger them

- **In chat**: entry functions register as tools (`as_tool=True`). Describe the task in natural language and the model calls them (e.g. `gui_agent`, `research_agent`, `wiki_agent`).
- **From the command line**: `openprogram programs run gui_agent -a task="Open Firefox"`.
- **From Python**: harness functions are ordinary importable Python functions.

## Writing your own

Any repository that follows the directory contract (`<package>/agentics/__init__.py` exposing `AGENTIC_FUNCTIONS`) can be installed with the same `programs install` command. See [Installing and writing harnesses](../installing-harnesses.md) for the contract, a minimal template, and the publishing flow.

That harness contract is different from a single self-programming Workflow package. For the package contract, a complete tested example, relative paths, and the `workflows validate/test/publish` commands, see [Write, test, and publish a Workflow](authoring.md). Generated `create_workflow` and `revise_workflow` packages use the same required behavior-test gate.

Workflow forms ask for task information, not execution settings. Text polishing infers its style from the text in the same model request; it does not require a style selection. Browser backend and session handles remain internal. Document page ranges and output destinations are optional task requirements. Explicit Python callers retain supported overrides.

## Weekly reports

If `weekly_report` is installed in your Workflow catalog, call it with the report text or the change you want. For example, ask it to submit this week's report, update only next week's plan, inspect the existing record, or prepare a local draft at a named path.

It reads the configured Feishu form and matches the name and year/week before writing. A missing record is submitted once; an existing editable record is opened from the submission history and saved. Partial updates preserve untouched fields. Explicit draft and read-only requests do not submit. Unknown inspection results, login failures and ambiguous records stop the operation. A submission cap does not by itself rule out editing an existing record.

Success requires a fresh page confirmation and persisted values. If a write result is uncertain, the Workflow reports it without automatically retrying. Ordinary browser authorization remains required. It uses bounded browser steps rather than a `goal()` loop, and does not invent progress, metrics or paper titles.
