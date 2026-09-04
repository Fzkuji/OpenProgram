# Goal Workflow

Goal repeatedly runs a working agent and asks a separate completion judge to
evaluate the available evidence. The objective, progress, resource usage, and
latest checkpoint are stored with the session, so reloading the page does not
remove the Goal.

The working agent performs changes and runs verification commands within its
existing permissions. The judge and specification refinement only receive
file-reading and search tools, not a shell or artifact-editing tools. When a
verification command is missing, the judge requests another working turn;
it does not modify the deliverable to make its own check pass. These are tool
capability restrictions, not a claim that retrieval has no cache or network effects.

## Start a Goal

Open **Programs → Workflow → goal → Use**, or enter:

```text
/goal all unit tests pass and the README documents the new flag
```

Programs and Python calls default to isolated context. `/goal` includes a
snapshot of the current conversation as initial evidence. The working agent
and judge otherwise use the same implementation.

Optional limits are `max_rounds`, `max_tokens`, `max_elapsed_s`, and
`max_cost_usd`. Limits and usage remain cumulative when a Goal is resumed.
The default working-turn timeout is 300 seconds; Python and CLI callers can
override it with `timeout_s`. Cumulative budgets are checked at controller
boundaries, so the current phase can consume resources beyond a total limit
before that limit stops the next phase.

Python callers can select the working role with `model` and `effort`, and
the judging role with `judge_model`, `judge_effort`, and `judge_timeout_s`.
An omitted judge model uses `goal.judge_model` if configured, otherwise the
resolved working model. Provider-qualified names accept `provider:model` or
`provider/model`. The Goal saves both roles' resolved model identity,
authentication route, reasoning setting, and timeout, but never credentials.
Resume retains these settings even if the current session defaults change.
An unavailable role leaves the Goal recoverably paused without starting work;
retry keeps the requested roles rather than selecting another provider.
Legacy Goals without saved roles resolve them once on their first resume and
show this migration in their details. Custom callable runtimes must be supplied
again by the Python caller; they are not reconstructed as hosted providers.

The details dialog and `/goal` status show the saved work and judge settings.

## Inspect and control it

The Goal chip in the composer opens a detail dialog. It shows the objective,
status, checklist, resource usage, last decision reason, and all pending
questions. The dialog supports editing, pausing, resuming, answering individual
questions, changing execution limits, and cancelling.

Achieved, cancelled, and impossible Goals no longer occupy the composer.
Their original objective and output remain available in the corresponding
execution record. Paused, waiting, and recoverable failed Goals keep the detail
entry so you can inspect and continue them after a reload.

Expand an execution record to inspect its LLM replies. The tree shows the
stored prompt preview and actual output; an empty reply is labelled “No text
output”. Copy includes the reply, including records using the older reply field.

The same operations are available in the TUI:

```text
/goal
/goal pause
/goal resume
/goal edit focus the survey on knowledge editing
/goal answer use papers published since 2023
/goal answer <question-id> use papers published since 2023
/goal clear
```

Editing creates a new objective revision and pauses the Goal until it is
resumed. Pending questions from the old revision are retained as superseded
audit records and no longer block the new revision. Answers are stored before
they are consumed, so a worker restart cannot discard them. The short answer
form targets the oldest pending question; include a question ID to answer
another item in the queue.

## Waiting and restart recovery

Questions are asynchronous. The judge records a question when some required
work needs information that cannot be established safely. If independent work
remains, the Goal keeps running that work and leaves the question in the Goal
dialog. It enters `waiting_user` only when every remaining required action
depends on an answer. It never guesses or performs answer-dependent work.

Several questions may accumulate. Answer them individually in the Goal dialog
or with `/goal answer`. An answer submitted to an active Goal is consumed at
the next controller boundary without starting a second execution. A waiting
Goal resumes after any new answer to perform newly unblocked work; other
questions remain pending. A Goal explicitly paused by the user stays paused.
Goal questions never replace or disable the normal chat composer.

In attended mode, the judge may record a question for a high-impact ambiguity,
missing access, or required approval, but ordinary implementation choices stay
autonomous. In unattended mode, questions never interrupt execution; the Goal
finishes all safe independent work and then waits if necessary. The composer's
Unattended control selects the mode for that session; reconnecting the page
restores the selected mode to the worker.

If the worker restarts while a Goal is refining, working, or evaluating, the
stored status becomes `paused_recoverable`. The Goal dialog still displays its
objective, checklist, usage, checkpoint, and reason. Use Resume or
`/goal resume` to start a new execution from that saved state.

Resume restores confirmed answers for the current objective revision and a
bounded window of prior work evidence for both the working agent and judge.
Editing the objective starts a new revision; earlier decisions remain in the
history but are not applied to the new objective automatically.

On one host, workers sharing the same session store use an exclusive Goal
controller lock. A second controller is rejected before model work, and a
starting worker leaves another worker's live Goal untouched. The OS releases
ownership when the controller exits, including a process crash. This does not
provide distributed ownership across hosts or network filesystems.

`waiting_external` also stops work turns, but automatic external-event wakeup
is not implemented. Resume it explicitly after the external dependency changes.

## Statuses

| Status | Meaning |
| --- | --- |
| `refining`, `active`, `running`, `evaluating` | Goal execution is active. |
| `waiting_user` | The Goal requires a user answer. |
| `waiting_external` | An external dependency must change before resume. |
| `paused`, `paused_recoverable` | Paused by the user or recovered after restart. |
| `blocked` | A recoverable dependency or permission prevents progress. |
| `impossible` | The current objective and constraints cannot be satisfied. |
| `stalled` | Repeated rounds produced no accepted progress. |
| `budget_exhausted` | A configured turn, token, time, or cost limit was reached. |
| `failed` | Execution or repeated judge evaluation failed. |
| `achieved` | The judge accepted the result and checklist. |
| `cancelled` | The user ended the Goal. |

Elapsed-time limits count active controller time. Time spent in
`waiting_user`, `waiting_external`, `paused`, or `paused_recoverable` is not
charged to the execution budget. Token and cost accounting uses an active-run
cursor, so unrelated session activity while the Goal is waiting is excluded.
