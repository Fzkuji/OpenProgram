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
The creation form keeps the objective as its primary field. Expand **Advanced**
for limits, role settings, and context mode. Explicit arguments in a typed
`goal(...)` call are preserved; internal recovery fields are not editable here.
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

In a paused or waiting Goal, expand **Configure agents** to edit the work and
judge provider, model, reasoning effort, and timeout. Provider identifies the
configured authentication route, not a credential. Saving keeps the objective,
evidence, questions, and cumulative usage unchanged; the selected models are
validated on resume. If a model is unavailable, correct the saved selection
and resume again. Running Goals must be paused before role changes.

The TUI exposes the same settings without opening a browser:

```text
/goal help
/goal role work openai_codex gpt-5.4 effort=high timeout_s=300
/goal role judge openai_codex gpt-5.4 effort=medium timeout_s=180
/goal budget max_turns=10 max_tokens=10000 max_elapsed_s=3600 max_cost_usd=5
```

Use your configured provider and model names; these examples do not select a
model automatically. Zero removes a budget limit. `/goal` displays resolved or
pending role selections and reports unknown cost explicitly.

## Inspect and control it

The Goal chip in the composer opens a detail dialog. It shows the objective,
status, checklist, resource usage, last decision reason, and all pending
questions. The dialog supports editing, pausing, resuming, answering individual
questions, changing execution limits, and cancelling.

Progress updates leave unsaved text and limits intact. If another client edits
the same values, the dialog preserves your draft and offers an explicit
“Use latest” action; it does not silently overwrite either version. Requests
include the displayed Goal identity and version. Save failures retain the
draft. Resume waits until edits are saved or discarded, including when an
answer unblocks work. Closing and reopening the same Goal keeps its draft;
switching sessions resets the editor to that session's saved Goal. Unknown
provider cost is shown as unknown, not as a zero-dollar charge.

An already-open editor remains open when the Goal completes, so completion
does not discard unsaved text. Its composer chip still disappears.
Ending a Goal asks for confirmation in the same dialog; saved work and history
remain available. Escape leaves the confirmation before closing the dialog.

Achieved, cancelled, and impossible Goals no longer occupy the composer once
any requested execution stop has been confirmed.
Their original objective and output remain available in the corresponding
execution record. Paused, waiting, and recoverable failed Goals keep the detail
entry so you can inspect and continue them after a reload.

Expand an execution record to inspect its LLM replies. The tree shows the
stored prompt preview and actual output; an empty reply is labelled “No text
output”. Copy includes the reply, including records using the older reply field.

The TUI displays the current Goal above the prompt: status, phase, objective
summary, checklist progress, and pending-question count. This read-only area
does not replace the prompt or require an immediate answer. Use `/goal` for
full details and controls. Completed Goals leave this area; their execution
results remain in the conversation history.

On session entry and reconnection, the TUI reads the saved Goal and applies
live updates in version order. While offline, it retains the last confirmed
snapshot and labels it as offline. A failed refresh is shown explicitly; it
does not clear the Goal or resume execution.

Pausing, editing, and ending first save your Goal change, then request
cancellation of its exact execution. If cancellation cannot be delivered,
the change remains saved and the UI reports that the execution stop is not
confirmed. **Retry stop**, or `/goal stop`, retries only that execution's
existing cancellation command; it does not start another Goal or reset usage.
The Web dialog and TUI distinguish an unknown execution state, stopping, and
a confirmed stop. They refresh on related execution events and reconnection;
the dialog also offers **Refresh status**. A cancelled Goal with an unconfirmed
stop keeps its controls visible. Resume stays unavailable until the previous
execution and all its descendants have ended. Starting a replacement
Goal also checks any saved stop request before initializing work or usage.
Script-only Goals without an execution record stop cooperatively at a Goal
boundary; the UI cannot confirm
their physical execution state.

The same operations are available in the TUI:

```text
/goal
/goal pause
/goal stop
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

The Goal HTTP action endpoint accepts an optional `expected` object containing
`goal_id`, `revision`, `run_id`, or `version` from the displayed snapshot.
Mismatches return a conflict without applying the action. State commits precede
cancellation delivery; a save conflict does not cancel the running execution.
Resume descriptors from HTTP and `/goal` bind to the observed snapshot and are
rechecked by the Workflow before starting work. If the Goal changes in between,
request resume again from its current state.

## Waiting and restart recovery

Saving a Goal pause request does not prove that its current execution has
stopped. Resume checks the linked execution before returning an invocation
and checks again at the actual Goal entry. An unavailable execution record,
a nonterminal execution, or an active descendant prevents a new run. `/goal`
reports the execution observation separately from the Goal status.
If an answer is saved while the old execution is still stopping, it remains
saved; the interface explains why automatic resume did not start.

Script-only calls without an execution record still use the cross-process
Goal controller lock. Sequential calls inside the same running parent
execution can resume after the previous Goal call returns, but this exception
does not bypass a user pause or edit.

Storage errors are not treated as a missing Goal. If its saved state cannot
be read, controls report that the state is unavailable and do not start or
resume work. The Goal API returns HTTP 503, not 404. A failed final save does
not publish a completion snapshot or completion notice; the last successfully
saved state remains the recovery point.

Startup recovery checks the recorded host, process ID, process start identity,
and attempt lease before treating an execution as abandoned. A second local
controller leaves a live owner alone; a confirmed exited process can be
recovered without waiting for its lease to expire. Admission has a bounded
30-second grace period before an attempt exists. Unknown process identity is
treated conservatively within the valid lease; this is not cross-host owner
discovery. Older records without process evidence retain legacy recovery.

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
