# GUI agent — call structure & context flow

This document records how context flows through `gui_agent` under the current
`expose` / `render_range` default semantics, and why each `@agentic_function`'s
decorator arguments are set the way they are.

References:
- Decorator semantics: [`agentic-programming/function-metadata.md`](../../../capabilities/agentic-programming/writing-functions/function-metadata.md)
- render_context implementation: `openprogram/context/nodes.py`
- Code: `openprogram/functions/agentics/GUI-Agent-Harness/gui_harness/`

## 1. Call structure

```
gui_agent(task)                   ← top-level @agentic_function (no render_range)
  └─ loop N times:
     gui_step(task, feedback)     ← @agentic_function orchestration (no runtime.exec)
       ├─ observe()               Python: screenshot + detect components + read state
       ├─ verify_step(...)        @agentic_function LLM leaf
       ├─ plan_next_action(...)   @agentic_function LLM leaf
       └─ dispatch_action(...)    Python: run the action plan picked
     ↓ return dict(goal, action, target, success, error, ...)
     ↓ passed in as the next gui_step's feedback
  └─ conclusion(task, ...)        @agentic_function LLM leaf
```

`gui_step` doesn't call `runtime.exec` directly; its job is to chain the four
stages together. All LLM calls happen inside the three leaf functions
`verify_step` / `plan_next_action` / `conclusion`.

## 2. render_range config per function

| Function | render_range | Rationale |
|---|---|---|
| `gui_agent` | unset (default) | Top level — should see the full conversation history plus the io of every gui_step in its own frame. `callers=None` (keep all chat history) + `subcalls=-1` (the gui_step chain accumulates naturally) is exactly what's wanted |
| `gui_step` | unset | It doesn't call `runtime.exec` itself, so render_range has no practical meaning for it |
| `verify_step` | `{"callers": 0}` | A one-shot snapshot judgment of "did the previous step succeed?". Everything it needs (the previous step's goal/action/target/outcome, the current screenshot, the components detected this step) is pushed in explicitly via `content=[...]`. `callers=0` walls off the upper chat history and the prior gui_step chain so they don't drown the snapshot judgment |
| `plan_next_action` | unset (default) | **The planner must see history** to make non-repeating decisions. The default `callers=None` lets it see the task description + the io of every prior gui_step (goal/action/target/success) + this round's verify io. `subcalls=-1` has no effect in a leaf (a leaf has no in-frame nodes) |
| `conclusion` | `{"callers": 0}` | The summary should be grounded in the final screen state, not polluted by the step-by-step narrative. Everything it needs (task, completed, steps_taken, final screenshot) is pushed in explicitly via `content=[...]` |

The helper leaves (the ones in component_memory, `learn`, `observe`,
`general_action`) all use `{"callers": 0}` — they are independent judges whose
input is fully supplied via `content=[...]` and need no conversation context.

## 3. Key design points

### What plan_next_action sees

From the perspective of plan step #5, the inputs to `render_context`:

- `frame_entry_seq` = the seq of the plan_next_action #5 code node
- `head_seq` = the current max seq in the DAG
- `render_range` = `None` (use defaults `callers=None, subcalls=-1`)

Walking render_context:
- pre-frame = all nodes with seq ≤ frame_entry_seq = the top-level chat user
  message, gui_agent's code node, the code nodes of gui_step #1..#4 (with their
  io), this round's gui_step #5 verify_step code node (with io), and the node for
  the observe Python result (if it goes through the DAG)
- in-frame = the nodes inside plan_next_action #5's own frame = empty (the leaf
  hasn't issued exec yet)
- pre-frame is not truncated; in-frame is not truncated (nothing to truncate)
- expose filtering: each gui_step is `expose="io"`, so the gui_step io nodes are
  kept while the LLM calls of verify_step / plan_next_action inside gui_step are
  hidden

So plan_next_action #5's prompt ends up with:

```
user: <original task>
... prior chat history ...
[gui_step #1] input={...} output={"goal": "open Firefox", "action": "click", "target": "Firefox icon", "success": true}
[gui_step #2] input={...} output={"goal": "open url bar", "action": "click", ...}
[gui_step #3] input={...} output={...}
[gui_step #4] input={...} output={...}
[verify_step #5] input={...} output={"step_succeeded": true, "observation": ...}
[plan_next_action #5] input={...}   ← itself
```

The planner can therefore see "what the first four steps did, and how the most
recent verify judged it" and make a non-repeating next-step decision.

### Why verify_step is the opposite

verify_step is a passive judgment: "did the thing the previous step did succeed,
as seen from the current screenshot?". Its evidence is entirely local:

- the previous step's feedback dict (goal/action/target/success/error) — already
  pushed in via `content=[feedback_text]`
- the current screenshot — already pushed in via `content=[{"type": "image", ...}]`
- the components detected this step — already pushed in via `content=[component_info]`

Letting it see the full chat + gui_step chain would instead introduce noise that
biases this simple "did the last step succeed" judgment with the macro narrative.
Hence the explicit `callers=0` wall.

### conclusion, same reasoning

The summary should state "what is visibly on the final screen", not narrate
"step 3 did this, step 7 did that". `callers=0` plus a hard constraint in the
prompt to "use the concrete text visible on screen".

## 4. Difference from the old version

The earlier code used a full-wall `{"callers": 0, "subcalls": 0}` strategy and
then explicitly built a feedback dict in Python and threaded it down level by
level. The problem was that the planner could really only see the previous step's
feedback, not the full trace, leading to a bug where it repeatedly performed the
same action.

The current design:

- the planner sees history naturally via the DAG default behavior (no more
  explicit accumulation)
- isolate the leaves that need it (verify / conclusion / tool-style judges) with
  an explicit `callers=0`
- there's no longer a "top-level is a special case" branch — the gui_agent top
  level and the gui_step inside it go through exactly the same render_context code
  path

The old analyses about a "collapsed mode proposal", "hidden side effects",
"sub-function io redundancy" no longer apply:
- the old "io leak: exposing code sub-calls" analysis is outdated — `expose="io"`
  now means: the frame's own input/output is exposed, the LLM inside the frame is
  hidden; a nested sub-function decides whether its own io is exposed via its own
  expose
- a "collapsed" mode is no longer needed — `expose="io"` hides the inner LLM by
  default, and a sub-function's io is either exposed or hidden under its own
  expose; there's no in-between need to "expose io while hiding nested grandchildren"

## 5. What to watch after the change

- For long tasks, plan_next_action's prompt may grow large because the gui_step
  chain gets long. Under token pressure, give `plan_next_action` or `gui_agent` a
  `render_range={"callers": N}` to explicitly truncate to the most recent N
  gui_steps
- A screenshot is an image node; N large images will blow up the context. We may
  need a "compress old screenshots into text descriptions" path, but that's a
  prompt-layer optimization, not a render_range-layer concern
