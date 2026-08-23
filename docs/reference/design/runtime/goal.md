# Goal Workflow

The Goal capability has one executable Workflow:

```python
goal(
    prompt,
    condition,
    *,
    model="",
    effort="",
    max_rounds=None,
    timeout_s=None,
    context_mode="isolated",
)
```

`goal()` alone owns specification refinement, working-agent rounds, completion
judgment, user questions, progress state, stopping rules, and the terminal
result. The dispatcher and slash-command layer do not contain a second Goal
loop.

## Two call surfaces

The Programs form invokes `goal()` with `context_mode="isolated"`. Its DAG and
runtime card may belong to the current session, but model calls inside the
Workflow cannot read conversation history that predates the Goal call.

`/goal <condition>` invokes the same function with
`context_mode="session"`. At function entry, it snapshots the current compacted
session view and supplies that view as initial evidence. The context source is
the only behavioral difference between the form and slash command.

`/goal` with no arguments reports the current Goal state. `/goal clear` marks
the active or waiting Goal as cleared; the running Workflow re-reads this state
between work rounds and stops without overwriting the clear.

## One state machine

Both surfaces use the same Goal state and statuses:

- `active`: the Workflow is refining, working, or judging;
- `waiting_user`: the judge requested a decision and the Runtime question is
  waiting for an answer;
- `achieved`: the judge accepted the condition and every checklist item;
- `capped`: the configured round limit was reached;
- `error`: repeated judge failure or checklist stall stopped the Workflow;
- `cleared`: the user cleared the Goal.

The Workflow persists this state under the owning session so GoalChip,
status/clear, reload, and every invocation surface observe the same object.
The state stores control data, not the copied session-context snapshot.

## Context and trust boundary

`render_range={"callers": 0}` isolates every Goal invocation from pre-call DAG
history. Session mode restores only the explicit session snapshot produced at
entry. The working model is told that this snapshot is untrusted conversation
data. The completion judge receives the same initial snapshot plus the results
produced by the current GoalRun.

Refinement and judgment run as inspection-only spawned calls. They may inspect
the working directory with their existing restricted tool sets, do not move the
session head, and do not create another Goal.

## User questions and cancellation

`need_user` uses the Runtime question channel inside the same Workflow
execution. The Goal state changes to `waiting_user`, the question crosses the
existing subprocess question bridge, and the answer resumes the same loop.
No ordinary chat message is reinterpreted as an answer to a separate session
loop.

The normal function-run cancellation boundary owns Stop. `/goal clear` is a
cooperative state change checked between rounds; it does not introduce another
execution controller.

## Implementation status

The single-Workflow contract is implemented when all of the following hold:

- Programs and `/goal` both reach the registered `goal` function;
- no `continue_goal_turns` dispatcher loop remains;
- isolated mode never reads or adopts session Goal context;
- session mode explicitly supplies the session snapshot;
- focused Goal, command, dispatcher, Web, question, cancellation, and reload
  checks pass;
- default App verification shows the same Goal state and runtime execution for
  both surfaces.
