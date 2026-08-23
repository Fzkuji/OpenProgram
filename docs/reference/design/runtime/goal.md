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
- `error`: repeated judge failure, checklist stall, zero-tool idle spin,
  or worker-restart reconciliation stopped the Workflow;
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
session head, and do not create another Goal. The completion judge uses
`goal.judge_model` when that setting is a non-empty `provider/model` or bare
model name; empty (the default) uses the session's picked model.

Those spawned calls open their own DAG branches (`metadata.spawn_branch_root`).
`render_path` does not enter a spawn-branch root via the parent's `caller`
edge, so the working agent never reads judge or refine instructions and
verdicts from its own history. The exclusion is directional: rendering from a
head inside the spawn branch still sees the branch via the spine. Results
return through the spawn's return value (and an attach pointer when the caller
writes one), not by leaking spawn internals into the parent context.

## Stopping rules

The loop stops on the first rule that fires:

- **Round budget.** Every run starts with a turn budget: `goal.max_turns` from
  config, or 150 when unset. An explicit zero or negative value (in config or
  as the `max_rounds` argument) removes the cap. Reaching the budget ends the
  run with status `capped`.
- **Judge failure.** Three consecutive malformed or failed judge decisions end
  the run with status `error`.
- **Checklist stall.** When a checklist exists and the done count does not
  grow for three consecutive `unmet` rounds, the run ends with status `error`.
- **Zero-tool idle spin.** Each work round is checked for tool use via the
  ambient Runtime's frozen block list. A zero-tool round whose verdict is
  still `unmet` injects an explicit warning into the next work prompt ("you
  must actually use tools; consecutive tool-less rounds are treated as giving
  up"); a second consecutive zero-tool round ends the run with status `error`
  and an idle-spin reason. Any round that uses a tool resets the counter.

The evidence passed to the completion judge is tail-truncated to the judge's
view budget (24 000 characters); truncation keeps the tail and prefixes a
single `[earlier evidence truncated]` line.

On worker startup, run-state reconciliation also settles goals a dead worker
left at `active` or `waiting_user`: they become `error` with a
"worker restarted while the goal loop was running" reason.

## User questions and cancellation

`need_user` uses the Runtime question channel inside the same Workflow
execution. The Goal state changes to `waiting_user`, the question crosses the
existing subprocess question bridge, and the answer resumes the same loop.
No ordinary chat message is reinterpreted as an answer to a separate session
loop.

The question waits indefinitely — an unanswered question never times a goal
out. When the user declines, the answer is empty, or the ask channel fails,
the run does not error: it downgrades to an `unmet` continuation whose next
work prompt tells the agent the user did not answer and to pick the most
reasonable plan itself, stating the decision and its rationale. A real answer
resets the runaway accounting — turn budget, idle counter, stall counter, and
judge-failure counter restart while collected evidence is kept.

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
