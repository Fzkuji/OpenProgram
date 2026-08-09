# Agent collaboration: one cross-branch communication primitive

All of agent collaboration collapses into **a single primitive: cross-branch
communication**. An agent can spawn other agents, send messages to other
branches or other sessions, and pull several branches together for one model to
synthesize. These look like different operations, but **underneath they are the
same thing**: deliver content to a branch, trigger that branch to run a turn,
and send the result back to the caller automatically. Everything is a tool call,
and everything is built on the existing event layer.

> Scope: this is design, not code. Implementation status is in the final section.

---

## 0. Core: there is only one primitive

The whole collaboration story has exactly one primitive:

> **Cross-branch communication** = **deliver content** to a branch (another
> branch of the same session, a different session, one created on the spot, or
> one that already exists) → **trigger** that branch to run a turn (the model
> reads the delivered content) → the result is **sent back automatically** to
> the caller (append a new message + trigger the caller to run a turn, so the
> caller wakes up, reads it, and continues).

Every collaboration operation is a **parameterization** of that primitive:

| Operation | Which use of communication it is | Tool |
|---|---|---|
| **Spawn a sub-agent** | **Create** a branch + deliver a message + auto-reply | `agent` |
| **Message an agent** | Deliver a message to an **existing** branch + auto-reply | `send_message` |
| **Synthesize several branches** | Deliver content from **multiple source** branches so the target model synthesizes | `send_message(sources=…)` |

Delivered content is always read and used by the target model. Count is
arbitrary (spawn can create N, synthesis can merge N, a message can go to many),
so it is not a distinguishing dimension. All three uses share one
deliver → trigger → reply-back path.

`attach` is not an operation. It is how a communication result is drawn as a
"return edge" on the DAG (marking which branch the result came back from).

---

## 1. Vocabulary (reuse existing abstractions, invent nothing)

| Concept | Definition | Source |
|---|---|---|
| **session** | An independent conversation with a `session_id`, backed by one git repository | `SessionStore` |
| **branch** | A `(session_id, head_id)` pair. Different heads in the same session = two branches of one conversation; different sessions = cross-conversation | Established in `merge.py`; same-session and cross-session take the same path |
| **deliver** | Append a message node to a branch | `append_message` (any session_id, no permission restriction) |
| **trigger** | Make a branch run one agent turn | `process_user_turn(TurnRequest(...))` |
| **auto reply-back** | When the target finishes, feed its reply back to the caller as new input + trigger it to run | `TaskRunner._dispatch_followup` (already exists) |
| **attach edge** | The pointer node on the DAG marking "which branch the result flowed back from" (drawing only) | `write_attach_pointer_for_spawn` |

The DAG rendering is already settled in `dag/dag-live.html` (cross-branch
communication scenario: asynchronous, send returns instantly, reply comes back
asynchronously, communication point-line shown on hover; spawn = sub-branch
service scenario; return flow = soft link edge).

---

## 2. The primitive as tools

Wrap the primitive into tools an agent can call. The division of labor
mirrors Claude Code: **`agent` creates agents, `send_message` talks to
them, `list_agents` sees them.**

### 2.1 The tools

**`agent` — spawn a new agent (the only tool that creates branches):**

```
agent(
    prompt: str,                        # instruction for the spawned agent
    description: str = "",              # short label, becomes the branch name
    agent_id: str = "",                 # agent profile; defaults to the session's
    context: str = "clean",             # "clean" / "inherit" / "SID:MSG_ID"
    wait: bool = true,                  # true=block for the reply; false=task_id
) -> str
```

`context` picks where the new branch starts: `"clean"` (default) is a new
root seeing only the prompt; `"inherit"` forks off the calling turn with the
full chain; `"SID:MSG_ID"` forks off that exact node (any session),
inheriting the chain up to it. `wait=False` returns a `task_id`; its
companions `task_output(task_id)` (block for the result) and
`task_stop(task_id)` (cancel) manage the async form.

**`send_message` — talk to an EXISTING agent:**

```
send_message(
    message: str,                       # content/instruction delivered to the target
    to: str,                            # see to values below
    sources: list[str] = [],            # also carry content from these branches (used when synthesizing)
    agent_id: str = "main",             # which agent the target runs as
    wait: bool = false,                 # false=async (default, returns instantly); true=block for the reply
) -> str
```

**`to` values — every value names a branch that already exists:**

| to | Meaning |
|---|---|
| `"sid:head"` | Deliver message to an existing branch. The node names the branch, not a fork point: delivery always lands on the branch's current tip, so a stale head (the branch ran more turns since) is still a valid address and never forks off history. A node that is a shared ancestor of several branches is ambiguous — the error lists the candidates (name + `sid:current-tip`). To fork off a specific node, use `agent(context="sid:msg_id")`. |
| `"<branch name>"` | Deliver to a named branch. Tried when the value is not `SID:HEAD` syntax: exact name match wins, a unique prefix is accepted next; several matches return an error listing the candidates (name + `sid:head`), zero matches point to `list_agents`. `list_agents` marks each branch's name so the model can address by name directly. |

The removed spawn addressing (`to="new"` / `"new:sid:msg_id"`) is rejected
with an error that points to the `agent` tool.

Every delivery (direct or queued, see §5.4) is prefixed with a
sender-receipt header —
`[message from SID:HEAD] To reply, use send_message(to="SID:HEAD"). Replying is
optional …` — so the receiver knows who sent it, how to answer, and that not
answering is legitimate. Agent-tool spawns carry the bare prompt: they are
workers, not correspondents.

Two uses:

- **Message an existing branch/session**: `to="sid:head"` → deliver message
  to that branch, trigger one turn, and the answer is sent back automatically.
  Cross-session uses the same path (`to` can be any session).
- **Synthesize several branches**: `sources=["s1:h1","s2:h2",...]` → carry the
  content of those branches along with the delivery so the target model reads
  and synthesizes them. Count is arbitrary. Combines with any `to`.

Both tools drive the same primitive; a spawn is the same
deliver → trigger → reply-back flow with a freshly created branch as the
target.

**Unified execution flow** (whichever tool starts it):
1. Resolve the target branch: `agent` creates it (`context` picks the fork
   point); `send_message` resolves `to` to an existing branch's current tip.
2. Assemble the delivered content: `message` plus (if `sources` is given) the
   content of each source branch.
3. Deliver and trigger: `process_user_turn(TurnRequest(session_id=to,
   user_text=delivered content, branch_from=fork point))` → the target branch
   runs one turn and **the model reads everything delivered**.
4. **Reply back**:
   - `wait=false` (default): returns "delivered + delivery_id" instantly, the
     caller keeps going unblocked; when the target finishes,
     `_dispatch_followup` **automatically** feeds the reply into the caller
     session as a new message + triggers it to run a turn, so the caller wakes
     up and reads it.
   - `wait=true`: blocks until the target finishes and returns the reply text
     directly.
- Events: delivery emits `branch.message_sent`; reply-back emits
  `branch.message_replied` (see §3).

### 2.2 How much content each source contributes (the key to synthesis)

How each branch in `sources` is fed to the target model — **have each branch
summarize itself first, then collect the summaries**:

1. For each source branch, first produce a **summary aimed at this
   communication** ("condense the conclusions of your branch into key points"),
   reusing `branch_summarization`.
2. Concatenate those summaries into `<branch label="...">summary</branch>`
   blocks and deliver them together with `message` for the target model to
   synthesize.

This keeps context from blowing up, allows many sources, and hands the model
condensed points instead of raw long conversations. Everything goes through
"self-summarization"; there is no "full text vs. summary" parameter choice.

### 2.3 `list_agents` — seeing each other (a precondition for communication)

```
list_agents(limit=50, agent_id?, source?) -> str   # db.list_sessions + db.list_branches
```

An agent's conversation is stored as a branch in the session DAG, so "which
agents can I talk to" = "which sessions exist, and which branches does each
one have". One call lists them all, grouped by session: each session line
carries its id, title, agent, and busy/idle status
(`run_control.is_turn_running`; omitted when the probe fails), and each
branch line carries its name (if any), a ready-to-use `to="SID:HEAD"`
address, and a preview of its tip. This is the entry point for "two agents
seeing each other".

### 2.4 New branches must have names

Every time the `agent` tool creates a branch,
**the branch must be given a name** — otherwise the web UI can only show an
8-digit hex short id and a pile of branches becomes indistinguishable.

- **Named immediately (Stage 1)**: at creation, pass a short label to
  `run_agent_turn(... label=…)` → `store.set_branch_name`. The label is taken
  from the delivered prompt (truncated to ~24 characters), or the model
  supplies a name explicitly in the call (`description`). This way a branch has a readable name
  from birth, with no wait on an LLM.
- **Renamed automatically in the background (Stage 2)**: once the branch is
  actually in conversation, `finalize_turn` fires when `turns` hits the
  thresholds `{1,6,16,40}`, and a background thread uses an LLM to generate a
  more fitting title from the branch content, overwriting the Stage 1 temporary
  name. The rules are in [branch-naming](operations/branch-naming.md), which
  defines the naming tiers, locks, and trigger points. This section only
  stresses one thing: **branches spawned by the agent tool and branches the user
  forks by hand use the same naming path (both get a Stage 1 placeholder name
  plus Stage 2 automatic renaming); neither may be skipped.**

### 2.5 Where the reply-back node lands: the initiator's current tail, serialized

For asynchronous reply-back (`wait=false`), `_dispatch_followup` feeds the
target branch's reply into the delivery session as a **synthetic user-role
turn**. **Key rule: the reply-back `TurnRequest` leaves `branch_from` unset
(INHERIT_PARENT) — the dispatcher resolves it to the delivery session's
current HEAD and advances it.** A per-delivery-session follow-up lock
(`TaskRunner._followup_lock`) serialises concurrent completions, so N
sub-tasks finishing produce one serial chain
`… → notice₁ → answer₁ → notice₂ → answer₂` — each follow-up reads a HEAD
that already contains the previous answer.

Why the reply is not pinned to the spawn node (`caller_msg_id`): with N
parallel sub-tasks forked from one turn, every reply-back would land as a
sibling hanging off that same node, and the single user message that
triggered the spawns would be answered N times on N parallel branches.
Anchoring at HEAD keeps one conversation lane through all N completions.

The return-flow provenance is not lost by this anchoring: the **attach
pointer** written at spawn time does hang off
`predecessor = caller_msg_id`, so the DAG still shows which turn each
sub-branch forked from and which branch each result flowed back from.
The sub-branch itself stays a parallel independent branch and **does not
merge back into the mainline**.

---

## 3. Foundation: the event layer (the whole design, self-contained)

The communication primitive is built on the event layer. This section spells the
event layer out in full — it is the framework-wide unified event stream
(the `openprogram/events/` package: `bus.py` + `tool_gate.py` + `bridges.py`), and
communication is just one more set of sources and consumers on it.

### 3.1 Why an event layer exists

Signals for "something happened" were scattered across several mechanisms in the
framework (AgentEvent in the agent loop, `_emit` in auth, on_event in context, WS
broadcast in channels, poll in memory, logging in store). The event layer unifies
them into **one bus: sources emit into it, consumers subscribe from it, and the
two never know about each other** — to "do something at a certain moment", just
subscribe to the matching type.

### 3.2 The Event model

The three essentials (what happened, its content, when) are fixed; correlation
information goes into an open metadata pocket rather than being hard-coded.

```python
@dataclass(frozen=True)
class Event:
    id: str          # unique id
    ts: float        # when it happened
    type: str        # what happened (see §3.4)
    origin: str      # who caused it: user / agent / tool / system / proactive
    payload: dict    # the content of this event (command, file path, which branch got a message, ...)
    metadata: dict   # open pocket: {"session":..., "turn":..., "lane":...}, filled only when needed
```

session/turn/lane go in the pocket rather than becoming fixed fields: they are
extra correlations and mean nothing for half the events (auth/channel), and an
open dict lets new correlation dimensions be added later without changing the
model. `make_event(type, origin, payload, metadata)` fills in the current
session/turn automatically from ContextVars.

### 3.3 A process-level singleton bus

All components (webui, agent loop, channels, memory, auth, task runner,
communication tools) live in **the same worker process** (each as a daemon
thread), so the bus is a **process-level singleton** `get_event_bus()`. Every
thread in the process gets the same instance and emits/subscribes directly, with
no cross-process bridging.

```python
bus.emit(event)                              # broadcast, fire-and-forget, never blocks the caller
bus.subscribe(handler, types={...})          # subscribe by type, returns unsubscribe
emit_safe(type, origin, payload, metadata)   # for sources: build + emit, swallowing all exceptions
emit_ws_frame(frame)                         # for sources: send a ready-made WS frame to the frontend via the bus (decouples webui)
```

### 3.4 Two kinds of event source + every existing event type

| | Class A: agent activity (has a turn) | Class B: system state (maybe no agent running) |
|---|---|---|
| Examples | user message, model reply, before/after tool, file change, turn end, subtask start/stop | credential rate limit, context overflow, inbound external message, skill change |

**Event types the framework already has:**

| Class | type | When | Source |
|---|---|---|---|
| A | `user.prompt_submitted` | User sends a message | dispatcher |
| A | `model.response_started`/`.completed` | Model starts / finishes speaking | agent_loop |
| A | `tool.before` | Tool is about to execute (**interceptable**, see §3.5) | agent_loop |
| A | `tool.after` | Tool finished executing | agent_loop |
| A | `file.changed` | A file was modified | write/edit/apply_patch |
| A | `subagent.started`/`.ended` | Subtask start/stop | TaskRunner |
| B | `credential.cooldown`/`.exhausted`/`.rotated` | Credential throttled / exhausted / rotated | events/bridges.py←AuthStore |
| B | `context.compaction_recommended`/`.compacted` | Context hit threshold / was compacted | context/engine |
| B | `channel.message_inbound` | External message arrives | channels |
| B | `memory.ingest_started`/`.ended` | Wiki ingest start/stop | memory watcher |
| B | `skills.changed`/`plugins.update_available` | Skill changed / new plugin version | webui watcher |

**Events introduced by communication** (class A):

| type | When | origin | Key payload fields |
|---|---|---|---|
| `branch.message_sent` | send_message delivers | agent | from, to, sources, delivery_id, is_new, chain |
| `branch.message_replied` | Target finishes and replies back automatically | agent | from, to, delivery_id, is_error |
| `branch.created` / `.started` / `.failed` / `.cancelled` | Branch state transitions | agent | branch, parent, agent_id, status |
| `sessions.listed` / `branches.listed` | Listing | agent | count |

`chain` (the spawn chain) travels in metadata and is used for depth-based loop
prevention (§5.1); state events support progress monitoring, auditing, and
troubleshooting.

Communication reuses the existing `subagent.started`/`.ended` (TaskRunner emits
them as usual for the spawn use).

### 3.5 Two interaction modes: observe vs. intercept

- **Observation (default, asynchronous)**: emit and go; subscribers receive
  asynchronously and the source does not wait. The vast majority of events work
  this way.
- **Interception (only `tool.before`, synchronous)**: downstream can say "do not
  execute" before a tool runs. The single entry point `_execute_tool_calls` has a
  synchronous consultation point before `tool.execute()` (`openprogram/events/tool_gate.py`,
  `register_tool_gate`). It must be fast (no LLM calls); when multiple parties
  weigh in, the strictest answer wins; it also applies to subagents (it sits
  outside the approval wrapper, and `permission_mode=bypass` cannot turn it off).
  **The communication tool `send_message` uses it for unattended
  interception** (see §5).

### 3.6 How communication uses the event layer

- Every communication action calls `emit_safe(...)` (delivery, reply-back,
  listing) — proactive, auditing, and frontend refresh are all subscribers to
  that one stream, mutually uncoupled.
- **Frontend notifications all go through `emit_ws_frame(frame)`**: for
  cross-session traffic, the target session's frontend receives a `ws.frame`
  event via the bus, webui subscribes and rebroadcasts it verbatim, and both
  frontends see "message received from X" and "X replied" in real time. The
  frontend protocol needs no changes, and the communication tool knows nothing
  about webui.
- **Unattended interception uses the synchronous `tool.before` point**: delivery
  is a side effect, so under an unattended + deny policy it is held for
  confirmation.

### 3.7 One principle

**Not every call is an event; only moments that some consumer wants to react to
are.** The tables above are filtered by that rule, and every event communication
introduces has a definite responder (frontend rendering, proactive, auditing).
Evolution is additive only: new event types and new payload fields never affect
existing subscribers (they read only the fields they care about).

---

## 4. End to end: two agents see each other and communicate

A and B run at the same time (different branches of one session, or different
sessions):

1. **See**: A calls `list_agents` → sees B's session and its active
   branch `(B_session, B_head)`.
2. **Send**: A calls `send_message("...", to="B_session:B_head")` →
   returns instantly and A carries on.
3. **B receives**: the message lands in B's branch (a △ "message from A" on B's
   side), and B runs a turn to answer it (△). Both frontends see it live via
   ws.frame.
4. **Reply back to A**: when B finishes, `_dispatch_followup` automatically
   appends the reply to the end of A (△) + triggers A to run a turn; A wakes up,
   reads it, and can continue.
5. **Repeatable**: A can `send_message` B again — neither branch blocks and
   nothing is serialized.

Spawn (the `agent` tool) and synthesis (with sources) are two more
parameterizations of the same flow and are not listed separately.

---

## 5. Robustness and safety

Communication creates branches, triggers other branches to run, and writes
across sessions. Those side effects need boundaries.

### 5.1 Recursive spawning + infinite-loop protection

**Recursive spawning is allowed** (a sub-branch can `send_message` further
sub-branches for multi-level task decomposition), kept in check by:

- **Depth limit**: every delivery carries a **spawn chain** in Event metadata
  (`chain: [issuing branch, …, current]`). When `send_message` runs and the
  chain length is ≥ `MAX_DEPTH` (default 8), it refuses and returns the reason to
  the model. Reply-back (the automatic followup) inherits the same chain and does
  not reset it, so back-and-forth between A and B counts toward the depth and
  stops automatically at the ceiling.
- **Self-send refusal**: a `to` pointing at **the issuing branch itself** (a
  direct cycle) is refused immediately.
- Chain information flows only in metadata and never enters model-visible
  content.

### 5.2 Concurrency limit + queueing

- Spawning runs on the `TaskRunner` thread pool, capped by
  `OPENPROGRAM_TASK_WORKERS` (default 4). Spawn dozens at once and anything over
  the cap **queues**, running as slots free up, without blowing anything up.
- Optional **token budget**: cap the total spawns / total tokens for one
  collaboration and refuse new spawns at the ceiling (guards against one runaway
  decomposition spawning hundreds). Not enforced by default in this document; the
  parameter is left available.

### 5.3 Cancellation propagation (cascading)

- Cancelling a task **also cancels every task it spawned**. Each spawn made
  from inside a running task records the chain on the Task entity
  (`parent_task_id`, defaulted from the runner's current-task ContextVar).
  `TaskRunner.cancel_task` walks the persisted entities breadth-first over
  that chain (visited-set guard, so even a malformed cycle terminates):
  pending/queued descendants flip straight to cancelled without ever running;
  running ones go through the same per-task cancel path as the root —
  session cancel event + `kill_active_runtime` + the 30s force-cancel
  watchdog. No zombie threads or subprocesses remain.
- Session-level cancel (the user's Stop on a session) additionally clears the
  session's send_message inbox (`inbox.clear`): the queued messages are new
  work that has not started yet, and a user stopping a session wants all of
  its work to stop. Each dropped entry leaves a system notice in its sender's
  session so the sender knows the message was never delivered.

### 5.4 Sending to a branch that is "already running" (race)

When A messages B, B may be mid-turn. **Do not interrupt, do not drop — queue.**
The busy check is `run_control.is_turn_running(target)` — every concurrent turn
entry point (webui chat, task runner workers) registers its cancel token in
`run_control._current_tokens` and unregisters it in a finally block, so
presence there is the authoritative in-process "a turn is running" signal.
Only cross-session sends check it: a same-session send runs inside the
sender's own turn, whose token is the one the check would see.

- **Queueing**: a busy target's message is persisted to the target session's
  inbox (`<session-repo>/inbox.json`, `openprogram/agent/inbox.py` — same
  placement pattern as `tasks.json`), recording the delivery body, sender
  `SID:HEAD`, sender agent, spawn depth at send time, and enqueue time. The
  sender immediately gets back "target busy, message queued, processed when
  its current turn ends".
- **Draining**: the dispatcher drains the inbox at turn end
  (`_process_turn_once` → `_drain_send_message_inbox`, on both the success and
  the error return), delivering each entry as one async turn through the
  normal path (`run_agent_turn_async` → auto-followup back to the sender),
  continued from the target's current head. Delivery-then-delete: an entry is
  removed only after its delivery turn was submitted — a crash between the two
  may re-deliver (acceptable); the reverse order could lose a message (not
  acceptable). The queued turn inherits the spawn depth recorded at enqueue
  (+1), so a queued hop still counts toward the §5.1 depth guard.
- **Limits** (mirroring Claude Code cross-session messaging): at most 50
  pending entries per target — a full inbox drops the oldest and leaves a
  system notice in the dropped message's sender session; an identical message
  from the same sender within 60s of a still-queued copy is rejected as a
  duplicate, and the sender is told so.

If B is idle, delivery is immediate (the pre-queue behavior).

### 5.5 Failure reply-back

If the sub/target branch fails (crash / timeout / model error), **it still
replies back**, with `is_error` and the reason in the content ("B failed:
<reason>"), and the caller's model decides for itself whether to resend, reroute,
or give up. **No built-in retry or circuit breaker** — the parent is a model, and
its judgment beats a fixed policy.

### 5.6 Result truncation

If the reply-back content exceeds `max_result_chars` (reusing the 30k default
from `@function`), it is **truncated head and tail and the full text is stored in
a file**, with the file path included in the reply. Huge intermediate results do
not blow up the caller's context or block the main flow.

### 5.7 Sub-branch identity / least privilege

- `agent_id` picks which agent the sub-branch runs as (different agent =
  different system prompt + toolset + model).
- model supports `inherit` (inherit the caller's model), or an explicitly weaker
  one.
- **By default a sub-branch has no more privilege than its caller** (least
  privilege); dangerous tools (deleting files and the like) still go through the
  §5.8 interception, which `permission_mode=bypass` cannot disable.
- A sub-branch **sees only the delivered message and the responses after it**,
  and does not inherit the caller's full history (saves context and isolates).

### 5.8 Unattended interception + validation

- `send_message` goes through the event layer's synchronous `tool.before`
  consultation point: under an unattended + deny policy it is held for
  confirmation (this also applies to sub-branches, sitting outside the approval
  wrapper).
- Before delivering, validate that `to` actually exists
  (`db.get_session` is not None); if it does not, raise an error rather than
  silently creating it. The existing three-layer gating (check_fn / can_use /
  requires_approval) still applies.

### 5.9 Branch visibility

Branches are marked **internal (sub-spawned) vs. user-visible**: an internal
branch can only be triggered by `send_message` and does not appear in the UI's
session picker (but it is still drawn in the DAG and can be listed by
list_agents so agents can address it).

### 5.10 Explicitly out of scope (and why)

- **An extra parentID field**: `(session_id, head_id)` plus caller/predecessor
  already forms the tree and the DAG already draws it, so no redundant field.
- **ID prefix classification** (fork_/msg_): existing id + name are enough for
  addressing, so no.
- **Retry / circuit-breaker policy**: failures are replied back to the model and
  the model decides; no fixed built-in policy (see §5.5).
- **Built-in aggregation functions** (voting, all-succeeded, etc.): synthesis
  means feeding sources to the model and letting it synthesize (§2.2). A model
  synthesizing is more flexible than a preset aggregation, so no fixed
  aggregation operators.

---

## 6. Backend and frontend checklist

**Backend (tools)**
- `openprogram/functions/tools/agent/` — `agent/` (spawn / fork) +
  `task_output/` + `task_stop/` (async form management)
- `openprogram/functions/tools/send_message/` — `send_message/` (deliver +
  trigger + auto reply-back + multi-source self-summarization) +
  `list_agents/` (reuse db.list_sessions + db.list_branches)
- Each tool calls `emit_safe(...)`; cross-session notifications use
  `emit_ws_frame`

**Backend (reused existing components)**
- `TaskRunner` (thread-pool concurrency, await, cancel, _dispatch_followup auto
  reply-back, attach edges)
- `SessionStore` (list/append/set_head/commit/get)
- `dispatcher.process_user_turn`
- `branch_summarization` (multi-source self-summarization)
- `openprogram.events` (emit_safe / emit_ws_frame)

**Frontend (`web/`)**
- Session / branch list panel (WS handlers already exist) + a "pick `to` →
  send message" interaction entry point
- On `branch.message_sent` / `branch.message_replied` (via ws.frame) → render
  communication nodes plus the return-flow soft link edge in that session's DAG /
  message stream (shown on hover; dag-live is already settled)
- Spawn progress reuses the existing `task_status` frame + the tasks panel

---

## 7. Verifiable behaviors

If the design holds, each behavior below is independently verifiable. Verify
through the webui (`cd web && npm run build` + `openprogram worker restart`) or
the event log (`~/.openprogram/sessions/<sid>/events.jsonl`, always on).

| Behavior | What you see |
|---|---|
| Spawn (the `agent` tool) | The agent calls once, a new branch runs a turn, and the result automatically follows up back to the caller; spawn events are visible in the event log |
| Listing | `list_agents` lists the real multiple sessions and each one's branches |
| Send to an existing branch in the same session | A sends to branch B of the same session, A does not block, B runs a turn, the reply returns to A automatically |
| Cross-session | A sending to another session takes the same path; both frontends update live via ws.frame |
| Multi-source synthesis | With 2 source branches, each summarizes itself first and the target model synthesizes a new answer |
| Robustness (§5) | A↔B back-and-forth stops automatically at MAX_DEPTH; spawning 30 queues without blowing up; cancelling the parent stops every child (`tests/unit/test_cascade_cancel.py`); messaging a running B queues to its inbox and is delivered when its turn ends (`tests/unit/test_send_message_inbox.py`); the parent receives is_error when a child fails; oversized results are truncated with a file path |
| Safety (§5.7-5.9) | Under deny it is held by tool.before; a nonexistent `to` raises an error; sub-branches have no more privilege than the parent and stay out of the UI picker |
| Frontend | Pick a branch in the webui and send a message; the DAG shows communication nodes + the soft link edge on hover |

---

## 8. Key file reference

| Thing | Location |
|---|---|
| Sub-agent spawn + auto reply-back | `openprogram/agent/sub_agent_run.py`, `agent/task/runner.py` (spawn_task / _dispatch_followup) |
| Tool template + registration | `openprogram/functions/tools/agent/agent/agent.py`, `functions/_runtime.py` (@function) |
| session/branch data layer | `openprogram/store/session/session_store.py` (list_sessions:658 / list_branches:832 / append_message:706 / set_head:814 / commit_turn:455) |
| Trigger a session to run a turn | `openprogram/agent/dispatcher/__init__.py` (process_user_turn:97) |
| Multi-source self-summarization | `openprogram/agent/compaction/branch_summarization.py` |
| List WS handlers | `webui/ws_actions/session.py:825`, `branch.py:221` |
| attach edge (drawing only) | `openprogram/agent/sub_agent_run.py` (write_attach_pointer_for_spawn) |
| Event bus | `openprogram/events/bus.py` (emit_safe / emit_ws_frame) |
| Busy-target inbox (§5.4) | `openprogram/agent/inbox.py` (enqueue / drain), busy check `run_control.is_turn_running`, drain hook `dispatcher._drain_send_message_inbox` |

> Note: "synthesize several branches" is provided by
> `send_message(sources=[...])`; there is no separate tool. The multi-parent
> ContextCommit lineage record in the underlying `_merge.py` is reused to record
> "which branches this synthesis came from".

---

## Appendix: implementation status

Everything in this document is implemented: the event layer (§3),
`TaskRunner`, `SessionStore`, `process_user_turn`, `branch_summarization`,
the `agent` tool family (`agent` / `task_output` / `task_stop`),
`send_message` and its discovery tool `list_agents`, spawned-branch naming
(§2.4 — the Stage 1 label at spawn plus the Stage 2 `finalize_turn`
rename), the serialized reply-back anchoring (§2.5 — `_dispatch_followup` +
`_followup_lock`), cascading cancel (§5.3 — `TaskRunner.cancel_task` over
`parent_task_id`, plus `inbox.clear` on session-level stop;
`tests/unit/test_cascade_cancel.py`), and the busy-target inbox (§5.4 —
`tests/unit/test_send_message_inbox.py`).
