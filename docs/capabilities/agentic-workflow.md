# Self-programmed agentic workflows

`agentic_workflow` is OpenProgram's self-programmed agentic workflow: the agent writes an actual Python program for the task, and the framework executes it. The planner composes the program out of the framework's building blocks — three-tier LLM primitives plus registered agentic functions — and control flow is plain Python: `if`, `for`, exceptions. The run model is the same as a developer's: the whole program runs top to bottom; if it crashes, the planner reads the traceback, fixes the code, and reruns it, with completed calls replayed from recorded results so the rerun effectively continues from the point of failure.

Run it from the Programs panel as `agentic_workflow`, or call it from Python:

```python
from openprogram.programs.agentic_functions.agentic_workflow import agentic_workflow

# Start a new workflow
result = agentic_workflow("port the auth module to the new client and update its tests")

# Auto-resume: tasks starting with "continue"/"resume" automatically resume the latest workflow
result = agentic_workflow("continue the optimization")
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
| `llm` | Single model request, no tools, no loop. Signature: `llm(prompt, model="", effort="", response_format=None, ...)` |
| `agent` | Tool loop, repeatedly calls llm + executes tools until done. Signature: `agent(prompt, model="", effort="", tools=None, max_iterations=20, ...)` |
| `goal` | Judgment loop, repeatedly calls agent + uses llm to judge condition until satisfied. Signature: `goal(prompt, condition, model="", effort="", max_rounds=10, ...)` |
| `validate_and_retry` | Validation and retry: execute action, use llm to check result, retry if failed. Signature: `validate_and_retry(action, check, retry, max_retries=2)` |
| `route` | Route selection: let llm choose one option from a list. Signature: `route(question, options, context="")` |
| `conditional` | Conditional branch: llm judges condition (YES/NO) and executes corresponding branch. Signature: `conditional(condition, context="", if_true, if_false)` |
| registered agentic functions | Every function in the `AGENTIC_MODULES` registry, callable by name. The planner's prompt carries this catalog. |

The three tiers compose: goal is based on agent, agent is based on llm. Control flow primitives use llm for judgment, simplifying planner's code generation.

**Control flow primitives example**:

```python
def workflow() -> str:
    # Validation and retry: retry if first result doesn't satisfy check
    files = validate_and_retry(
        action=lambda: agent("Find auth related files"),
        check="File count >= 3 and includes oauth",
        retry=lambda: agent("Expand search to include oauth and openid files")
    )
    
    # Route selection: let llm choose strategy
    strategy = route(
        question="Choose migration strategy",
        options=["Direct migration", "Refactor then migrate"],
        context=files
    )
    
    # Conditional branch: execute different branches based on llm judgment
    plan = conditional(
        condition="strategy is direct migration",
        context=strategy,
        if_true=lambda: agent("Write direct migration plan: " + files),
        if_false=lambda: agent("Write refactor plan: " + files)
    )
    
    return plan
```

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
