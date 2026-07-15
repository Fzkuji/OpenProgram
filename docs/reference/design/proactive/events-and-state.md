# Events and State (The Foundation)

These are the two bottom-most bricks of the whole layer: what an **event** looks like, and how **state** is accumulated from events.
The overview only touches on this; here we go all the way. Read `overview.md` before this one.

## 1. What an Event Looks Like

An event is a small data packet that records "something that just happened." Its fields are as follows:

```python
@dataclass
class Event:
    id: str            # unique id for this event
    ts: float          # when it happened (timestamp)
    type: str          # what kind of event, see the table below
    origin: str        # who caused it: user / agent / tool / proactive / system
    session_id: str    # which session it belongs to
    payload: dict      # the specifics of this event (what command, which file was changed, ...)
```

Possible values for `type` (enough for now; more can be added later):

| type | When it's produced | What's in payload |
|---|---|---|
| `user.prompt_submitted` | The user sent a message | The message text |
| `model.response_started` | The model began replying | — |
| `model.response_completed` | The model finished replying | Reply text, and whether it claims completion |
| `tool.before` | A tool is about to execute | Tool name, arguments (e.g. the command string) |
| `tool.after` | A tool finished executing | Tool name, result, and whether it errored |
| `file.changed` | A file was changed | File path |

The `origin` field matters a lot — it records "who caused this event." Most events are caused by user / agent / tool,
but **the framework also produces events when it acts on its own** (for example, when it pops up a reminder, it records a `origin=proactive` event).
Why distinguish? Because events the framework produces itself must not in turn trigger the framework to act again, or it would loop into a deadlock —
that bottom line is covered in `invariants.md`.

## 2. Where Events Come From

Events aren't written out of thin air; they translate the things that **already happen** while the agent works into a unified format. Your framework already
"knows" things are happening at these points; it just doesn't yet record them uniformly as events:

- Before and after a tool runs, `agent_loop` already fires `tool.before_use` / `tool.after_use` (the existing hooks).
- When the model streams a reply, there are already "start/end" signals.
- When a user message comes in, the dispatcher is already handling it.

What the proactive layer does is, at these existing points, **translate** what happened **into an Event and drop it into the event stream**.
(Exactly which lines to hook in is an implementation detail; see [the implementation plan](../plans/proactive-implementation.md).)

## 3. The Event Stream: An Append-Only Ledger

All events flow together into one **stream** — ordered by occurrence, appended to only, never altering what's already recorded.

This "only append, never overwrite" property (called **append-only** in English) yields a very pleasant result: **the "current situation" at any moment
can be computed from this stream from scratch.** And that leads to the next brick — state.

## 4. State: Accumulating the Event Stream into the "Current Situation"

### 4.1 What fold Is

When a policy makes a decision, it often looks not just at the event in front of it but also at "the situation that has built up" — which files were changed, how many times a tool
has failed, whether the model just claimed completion. This "situation" isn't stored separately; it's **computed from the event stream**.

The way it's computed is called **fold** (rolling a snowball): start from an empty situation, run through the events one by one, update the situation after each one,
and when you're done you have the current situation.

```python
def fold(event_stream):
    state = empty_state()              # snowball starts from zero
    for e in event_stream:             # roll through one by one
        state = update(state, e)       # each event grows the snowball a little
    return state                       # done rolling = current state

def update(state, e):
    if e.type == "file.changed":
        state.changed_files.add(e.payload["path"])
    elif e.type == "tool.after" and e.payload["errored"]:
        state.tool_failure_count[e.payload["tool"]] += 1
    elif e.type == "tool.after" and not e.payload["errored"]:
        state.tool_failure_count[e.payload["tool"]] = 0   # reset on success
    # ... for each event you care about, update the corresponding state
    return state
```

Walk through it concretely and watch the snowball grow:

![fold: events pass one by one, the situation grows step by step](diagrams/events-fold.svg)

At this point the "current situation" is `{changed files: {auth.py}, bash failures: 2}` — **nobody maintains this count by hand;
it's purely a byproduct of accumulating events.** This is what the overview means by "event-driven manages memory for you in one place."

### 4.2 How Policies Use State

A policy's `evaluate` receives the current event **and** the current state, and looks at both together:

```python
class StuckToolWatcher:
    on = {"tool.after"}
    def evaluate(self, event, state):
        tool = event.payload["tool"]
        if state.tool_failure_count[tool] >= 3:     # read the accumulated state
            return remind(f"{tool} has failed repeatedly, may be stuck")
        return None
```

A memory-dependent judgment like "three failures in a row" is this straightforward to write precisely because of the fold-derived state.

### 4.3 No Need to Recompute from Scratch Every Time

You might worry: if you fold the entire stream from scratch on every incoming event, won't it be slow when the stream is long?

No need. The actual implementation is **incremental**: keep one "current state" around, and when a new event arrives just update it one step (that is, one call to
`update(state, e)`), without recomputing history. "Folding from scratch" is only the **definition** — it defines what the state should equal;
incremental update is the efficient implementation of that definition. The two must produce the same result, and that's the only rule to uphold.

## 5. A Pitfall to Watch: Multiple Subtasks Running at the Same Time

OpenProgram supports running multiple subagents (background, parallel subtasks) at the same time. If all their events pile into one stream and
get folded together, they cross-contaminate: the files changed by subtask A and the files changed by subtask B get mixed into one "changed files" set,
and policies will then pair A's changes with B's situation, throwing off every judgment.

The fix: **fold separately per "execution flow."** Each subagent has its own copy of state, with no cross-contamination. Each event carries
a marker of "which execution flow I belong to" (using `session_id` plus a subtask identifier), and the fold groups by it.

This is the only concurrency problem this version needs to handle seriously. The rest (crash recovery, tamper-proofing) is out of scope for this version.

## 6. Summary

| Concept | In one sentence | Mental model |
|---|---|---|
| Event | A small data packet of "something just happened" | An entry in the ledger |
| Event stream | All events ordered and appended to only | An ever-growing ledger |
| fold | Accumulate the event stream into the current situation | Rolling a snowball |
| State | The "current situation" that's accumulated | What the snowball looks like by now |

Next up, `execution-model.md`: how to actually write policies (Policy), and the considerations for the two kinds of policies (blocking / observing).
