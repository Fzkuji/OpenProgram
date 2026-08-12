# Task lists

A task list turns a todo list into the control flow of a run. Instead of one agent working a long task in a single growing conversation, the task is planned into items, and each item runs as its own agent whose entire context is that item plus the results handed to it. The list is the program; each item is a bounded sub-task. Two things follow: a long task stays stable, because scope is pinned per item and a failure is retried at item level rather than restarting everything, and the context stops growing with the step count, because step twenty does not carry steps one through nineteen.

Run it from the Functions panel as `run_task_list`, or call it from Python:

```python
from openprogram.functions.agentics.task_list import run_task_list

result = run_task_list("port the auth module to the new client and update its tests")
```

## Small tasks are not split

The planner's first decision is whether to split at all. A task one agent finishes in a single pass — a small edit, one question answered, one file written — runs as a single item, because planning it and handing work between agents costs more than just doing it. Splitting starts when a task genuinely exceeds one pass: several distinct deliverables, work spread across files or stages, or steps that depend on what the step before produced.

## The list is the todo board

There is no second list. Items are entries on the session's [todo planning board](tools.md) — the same `todos.json` that `todo_create`, `todo_update` and `todo_list` read and write — so a running workflow shows up wherever todos already show up, moving through `pending` → `in_progress` → `completed` as it goes. `todo_list` renders workflow items like any other entry, and todos you wrote by hand sit alongside untouched.

Three optional fields ride on an entry when a workflow owns it:

| Field | Meaning |
|---|---|
| `done_criteria` | The criterion judged after the item runs — a file that must exist, a command that must pass, an output that must appear |
| `context_spec.upstream` | How many earlier completed items' results this one receives: `0` stands alone, `N` takes the last N, `-1` takes all of them |
| `result_summary` | The hand-off text passed to the items downstream |

They are optional everywhere else, so entries written before this existed, or written by the plain todo tools, keep working.

`upstream` is the item's context budget, decided at planning time rather than guessed while running. It is also what bounds the DAG slice a nested call renders, so a narrow item stays narrow all the way down.

The repository is shared state, so hand-off summaries carry conclusions and paths, never file contents. A summary that grows past its limit is truncated with a note telling the next agent to read the named paths instead — a summary that becomes a transcript gives back everything the split was for.

## How an item is judged

The same judge that decides [session goals](goal.md) decides items, with the item's `done_criteria` handed to it as the goal. There is no second completion judge: it reads the compacted session view, has inspection tools, and answers a strict yes or no with a reason. Anything other than a clear "met" — including a judge that wants to ask you something, or one that fails outright — leaves the item not done.

This is one layer above the goal loop, not the same one. A session goal drives whole turns until a condition holds; a task list drives items, and calls the judge once per item. Setting a goal and running a task list are independent.

A miss is retried once, with the judge's reason passed to the executor so the second attempt knows what was wrong. A second miss goes to the planner instead of a third identical attempt — retrying the same failure again does not fix it, but splitting the item or correcting its criterion does.

## Revision while running

The list is not frozen once planned. When an item fails twice, the planner rewrites the part of the list that has not run yet: splitting the failed item, restating its criterion, inserting work that turned out to be a prerequisite, or dropping it. Completed items are never touched — their results stand, and each revision is recorded with the item ids before and after, so the change is visible next to the list it changed.

## Resuming after an interruption

Every transition is written to the board before the work happens, which makes `todos.json` a checkpoint rather than a log. A run killed halfway resumes from where it stopped:

```python
run_task_list("the same task text", resume=True)   # resume=True is the default
```

An item left `in_progress` by a killed process is picked up and run again; completed items keep their results and are skipped. Resuming matches on the task text, so entries belonging to a different task — or to a todo you wrote yourself — are never adopted, and a task with no entries on the board is planned fresh.

## What it returns

```python
{"status": "completed", "task": "…", "items": [...], "revisions": [...], "summary": "…"}
```

| Status | Meaning |
|---|---|
| `completed` | Every item is settled — done, or dropped by a revision. |
| `abandoned` | A revision emptied the remaining list, or the planner could not be reached to revise. |
| `capped` | The run hit its ceiling of 40 executed items. A list still growing at that point is a planning failure, not progress. |

## Compared to a session goal

A [goal](goal.md) keeps one session working until a condition holds; the conversation is continuous and grows with every turn. A task list splits the work up front and runs each piece in its own bounded context. Use a goal when the finish line is clear but the path is not; use a task list when the work is long enough that carrying all of it in one context is the problem.
