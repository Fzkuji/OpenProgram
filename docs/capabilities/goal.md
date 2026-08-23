# Goal Workflow

Goal repeatedly runs an agent and asks a separate completion judge whether a
condition has been met. There is one Goal Workflow with two ways to supply its
inputs.

## Use the Programs form

Open **Programs → Workflow → goal → Use** and fill in `prompt` and `condition`.
This is a direct invocation: the GoalRun starts without reading earlier chat
history. Its runtime card is still recorded in the owning conversation.

## Use the current conversation

Type a condition after `/goal`:

```text
/goal all unit tests pass and the README documents the new flag
```

This invokes the same Goal Workflow, but includes the current conversation's
compacted context as initial evidence. The Workflow then uses the same
refinement, judge, round limit, question handling, progress state, and terminal
statuses as the Programs form.

The active Goal appears in the composer GoalChip. A question from the judge
uses the standard question panel and resumes the same Workflow execution after
you answer.

## Status and control

```text
/goal
/goal clear
```

`/goal` reports the Goal associated with the current session. `/goal clear`
marks an active or waiting Goal as cleared; the Workflow checks that state
between rounds and stops without replacing it.

The ordinary Stop control cancels the current Goal function run. Clearing and
cancelling are different: clear changes the Goal state, while Stop cancels the
execution boundary.

## Terminal statuses

| Status | Meaning |
| --- | --- |
| `achieved` | The judge accepted the condition and every checklist item. |
| `capped` | The configured round limit was reached. |
| `error` | Repeated judge failure or checklist stall stopped the run. |
| `waiting_user` | The judge asked a question and is waiting for an answer. |
| `cleared` | The current session cleared the Goal. |
