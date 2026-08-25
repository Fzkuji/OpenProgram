# Goal Workflow

Goal repeatedly runs an agent and asks a separate completion judge whether a
condition has been met. There is one Goal Workflow. How it is started decides
whether the current conversation is included. The judge uses the session's
picked model unless `goal.judge_model` is set to a `provider/model` or a bare
model name.

## Start it yourself

A user-started run always includes the current conversation as initial
evidence (`context_mode=session`): the Programs form, `/goal`, the welcome
button, and Retry. Open **Programs → Workflow → goal → Use** and fill in the
task (`prompt`); `condition` stays hidden and defaults to that text. Or type
the condition after `/goal`:

```text
/goal all unit tests pass and the README documents the new flag
```

The runtime card is recorded in the owning conversation. The Workflow then
uses the same refinement, judge, round limit, question handling, progress
state, and terminal statuses for every entry.

## When the agent or Python starts it

An agent-issued `goal` call can pass `context_mode`: `isolated` (no session
view) or `session` (include the current conversation). Omitting it defaults
to `isolated`. A direct Python `goal(...)` call also defaults to `isolated`.

The active Goal appears in the composer GoalChip. A question from the judge
uses the standard question panel and stays there while work continues. When
you answer, the next work round receives that answer and restarts its round
budget. If you decline or leave the answer empty, the Workflow continues on
its own with the most reasonable plan instead of stopping.

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
| `capped` | The round limit was reached (`goal.max_turns`, default 150; zero or negative in config = unlimited). |
| `error` | Repeated judge failure, checklist stall, or consecutive tool-less rounds stopped the run. |
| `waiting_user` | The judge asked a question and is waiting for an answer. |
| `cleared` | The current session cleared the Goal. |
