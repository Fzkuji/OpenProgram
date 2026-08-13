# Self-programmed agentic workflows

`agentic_workflow` is OpenProgram's self-programmed agentic workflow: the agent writes an actual Python program for the task, and the framework executes it. The planner composes the program out of the framework's building blocks — free-form `agent()` calls and the registered agentic functions — and control flow is plain Python: `if`, `for`, exceptions. The run model is the same as a developer's: the whole program runs top to bottom; if it crashes, the planner reads the traceback, fixes the code, and reruns it, with completed calls replayed from recorded results so the rerun effectively continues from the point of failure.

Run it from the Programs panel as `agentic_workflow`, or call it from Python:

```python
from openprogram.programs.agentic_functions.agentic_workflow import agentic_workflow

result = agentic_workflow("port the auth module to the new client and update its tests")
```

Every call creates an independent workflow instance with its own directory under the session repository — `workflows/<run_id>/` holding `code.py` and `state.json`. Instances share nothing: run as many concurrent workflows as you want.

## Small tasks are not split

The planner's first decision is whether the task needs a program at all. A task one agent finishes in a single pass — a small edit, one question answered, one file written — is executed directly, because writing a program for it costs more than doing it. Programs are for tasks that genuinely exceed one pass: several deliverables, work spread across stages, or steps that depend on earlier output.

## The generated program

The planner has read-only tools (read, grep, glob, list) and receives the task plus the function registry — the list of building blocks. It produces a plain Python module with a `def workflow()` entry point:

```python
def find_issues() -> str:
    return agent(
        "Review openprogram/auth/ file by file for error handling and "
        "concurrency issues. Output a findings list graded HIGH / MEDIUM / LOW "
        "with file paths and line numbers.",
        description="find issues")


def workflow() -> str:
    findings = find_issues()
    if "HIGH" in findings:
        agent("Fix the HIGH findings and get the related tests passing: " + findings,
              description="fix auth")          # a working agent, its profile carries tools
        checks = run_tests()                    # a registered agentic function, called by name
        if "failed" in checks:
            raise RuntimeError("tests still failing after fix: " + checks)
    return agent("Write a report summarizing the results above", description="report")
```

Imports are forbidden — the framework injects everything the program may call:

| Injected name | What it is |
|---|---|
| `agent` | The one LLM primitive — the existing agent spawn tool, injected as-is (`agent(prompt, description="", agent_id="", …)`). Model and tool set come from the agent profile selected by `agent_id`. |
| registered agentic functions | Every function in the `AGENTIC_MODULES` registry, callable by name. The planner's prompt carries this catalog. |

The module is validated before it runs: it must parse, it must define `workflow()`, and it must not import. Invalid code is sent back to the planner with the concrete error, as many times as it takes.

There is no checkpoint syntax in the program. Recording is the framework's job: every injected callable is wrapped, and each real execution is written to `state.json` before and after it happens, keyed by function name, invocation order, and an argument digest. Verification is not imposed by the framework either — the planner writes checks into the program and raises when they fail, which routes into the revision loop.

## Resuming: rerun the whole program, replay completed calls

There is no scheduler and no "next item" logic. Resuming means executing `workflow()` again from its first line. The only mechanism is short-circuiting at call boundaries: a call already completed in `state.json` — same name, same order, same arguments — is not executed again; it returns its recorded result instantly. The rerun flashes past finished work and starts doing real work at the first incomplete call. Control flow is re-evaluated every time (`if` re-branches, `for` re-loops); only the expensive call results are restored.

A killed process resumes the same way: state changes hit disk before the work happens, so rerunning the instance by its `run_id` continues it. Resuming is explicit — each `agentic_workflow(task)` call creates a fresh instance and returns its `run_id`; to continue an existing one, pass that `run_id` back. Nothing is matched by task text.

## Revision after an error

When the program raises — a syntax error, a failed call, or a check the planner wrote itself — the handling is what a developer does: the planner gets the traceback, the current code, and the run records in `state.json`, rewrites `code.py`, and the whole program reruns. Untouched completed calls replay as before, so a revision never discards finished work. Old code versions are archived in the instance directory, and the revision history is part of the return value.

There is no abandoned state. Invalid code and runtime failures both go back to the planner with the concrete error, as many rounds as needed. The only forced stop is `capped`: 40 real executions (replays don't count) ends the run, because a program still growing at that point is a planning failure.

## What it returns

```python
{"status": "completed", "run_id": "…", "task": "…", "result": …, "revisions": [...]}
```

| Status | Meaning |
|---|---|
| `completed` | `workflow()` returned. The result and full run records come with it. |
| `capped` | The run hit its ceiling of 40 real executions. |

## Compared to a session goal

A [goal](goal.md) keeps one session working until a condition holds; the conversation is continuous and grows with every turn. A workflow writes the plan down as a program and runs each call in its own bounded context. Use a goal when the finish line is clear but the path is not; use a workflow when the work is long enough that carrying all of it in one context is the problem. The todo planning board is not involved — a workflow's state lives in its own instance directory.
