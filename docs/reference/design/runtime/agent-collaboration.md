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

| Operation | Which use of communication it is |
|---|---|
| **Spawn a sub-agent** | **Create** a branch + deliver a message + auto-reply |
| **Message a branch** | Deliver a message to an **existing** branch + auto-reply |
| **Synthesize several branches** | Deliver content from **multiple source** branches so the target model synthesizes |

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

Wrap the primitive into tools an agent can call. **One core tool plus two
listing tools.**

### 2.1 `message_branch` — cross-branch communication (the core, the only collaboration primitive)

```
message_branch(
    message: str,                       # content/instruction delivered to the target
    target: str = "new",                # see target values below
    sources: list[str] = [],            # also carry content from these branches (used when synthesizing)
    agent_id: str = "main",             # which agent the target runs as
    wait: bool = false,                 # false=async (default, returns instantly); true=block for the reply
) -> str
```

**`target` values — creating a branch and sending a message are different values of the same parameter:**

| target | Meaning |
|---|---|
| `"new"` | Create a brand-new branch from ROOT (new session), deliver message, let it run |
| `"new:sid:msg_id"` | Fork a new branch from a node, deliver message, let it run |
| `"sid:head"` | Deliver message to an existing branch |

**Creating a branch is not a separate operation; it is just `target` set to
`new` / `new:…`.** Three uses:

- **Create and run (spawn / open a new session / fork)**: `target="new"` or
  `"new:sid:msg_id"` → new branch + deliver message; when it finishes, the
  result flows back automatically. (Want several? Call several times, each
  asynchronous and parallel.)
- **Message an existing branch/session**: `target="sid:head"` → deliver message
  to that branch, trigger one turn, and the answer is sent back automatically.
  Cross-session uses the same path (target can be any session).
- **Synthesize several branches**: `sources=["s1:h1","s2:h2",...]` → carry the
  content of those branches along with the delivery so the target model reads
  and synthesizes them. Count is arbitrary. Combines with any target.

**Unified execution flow** (whichever use it is):
1. Resolve `target`: `new` → new session with empty `branch_from`;
   `new:sid:msg_id` → fork inside sid with `branch_from=msg_id`; `sid:head` →
   `set_head` to that branch.
2. Assemble the delivered content: `message` plus (if `sources` is given) the
   content of each source branch.
3. Deliver and trigger: `process_user_turn(TurnRequest(session_id=target,
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

### 2.3 `list_sessions` / `list_branches` — seeing each other (a precondition for communication)

```
list_sessions(limit=50, agent_id?, source?) -> str      # db.list_sessions
list_branches(session_id?) -> str                        # db.list_branches
```

Before communicating you need to name a target/sources, so you first have to
list which sessions exist and which branches each one has (`(session_id,
head_id)` + name). This is the entry point for "two agents seeing each other".
The data layer and WS handlers already exist (`handle_list_sessions` /
`handle_list_branches`); only the tool wrappers are missing.

### 2.4 New branches must have names

Every time `message_branch` creates a branch with `target="new"` / `"new:…"`,
**the branch must be given a name** — otherwise the web UI can only show an
8-digit hex short id and a pile of branches becomes indistinguishable.

- **Named immediately (Stage 1)**: at creation, pass a short label to
  `run_agent_turn(... label=…)` → `store.set_branch_name`. The label is taken
  from the delivered `message` (truncated to ~24 characters), or the model
  supplies a name explicitly in the call. This way a branch has a readable name
  from birth, with no wait on an LLM.
- **Renamed automatically in the background (Stage 2)**: once the branch is
  actually in conversation, `finalize_turn` fires when `turns` hits the
  thresholds `{1,6,16,40}`, and a background thread uses an LLM to generate a
  more fitting title from the branch content, overwriting the Stage 1 temporary
  name. The rules are in [branch-naming](operations/branch-naming.md), which
  defines the naming tiers, locks, and trigger points. This section only
  stresses one thing: **branches spawned by message_branch and branches the user
  forks by hand use the same naming path (both get a Stage 1 placeholder name
  plus Stage 2 automatic renaming); neither may be skipped.**

### 2.5 Where the reply-back node lands: continue the caller's branch, not the mainline

For asynchronous reply-back (`wait=false`), `_dispatch_followup` feeds the
target branch's reply into the caller session as a **synthetic user-role turn**.
**Key rule: the `predecessor` of that reply-back node must point at "the node
that issued message_branch" (the caller), not at the caller session's current
`head_id`.**

Why:
- Using `head_id` as predecessor → the reply is **stitched onto the tail of the
  mainline**. If the caller chatted about something else while waiting, the
  reply lands inexplicably after that, and the DAG gives no hint that this is
  "the return flow of a particular spawn".
- Using the caller as predecessor → the reply **continues from the point where
  it was issued**, a natural extension of the caller's branch in the DAG
  (matching where the attach pointer lands: attach also hangs off
  `predecessor = caller_msg_id`).

DAG shape: `caller node ──caller──> sub-branch (dash-dot spawn edge)`; once the
sub-branch finishes, the reply-back user node goes
`──predecessor──> caller node`, continuing after the caller node. The caller
reads the reply, responds, and the chain continues. The sub-branch itself is a
parallel independent branch (forked off the caller node) and **does not merge
back into the mainline**.

In implementation, the reply-back `TurnRequest` must carry the caller point
(caller_msg_id) explicitly as `branch_from`; when it is omitted,
`process_user_turn` takes the session `head_id` as predecessor, which stitches
the reply onto the tail of the mainline.

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
| `branch.message_sent` | message_branch delivers | agent | from, to, sources, delivery_id, is_new, chain |
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
  **The communication tool `message_branch` uses it for unattended
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

1. **See**: A calls `list_sessions` → sees B; `list_branches` → sees B's active
   branch `(B_session, B_head)`.
2. **Send**: A calls `message_branch("...", target="B_session:B_head")` →
   returns instantly and A carries on.
3. **B receives**: the message lands in B's branch (a △ "message from A" on B's
   side), and B runs a turn to answer it (△). Both frontends see it live via
   ws.frame.
4. **Reply back to A**: when B finishes, `_dispatch_followup` automatically
   appends the reply to the end of A (△) + triggers A to run a turn; A wakes up,
   reads it, and can continue.
5. **Repeatable**: A can `message_branch` B again — neither branch blocks and
   nothing is serialized.

Spawn (target="new") and synthesis (with sources) are two more parameterizations
of the same flow and are not listed separately.

---

## 5. Robustness and safety

Communication creates branches, triggers other branches to run, and writes
across sessions. Those side effects need boundaries.

### 5.1 Recursive spawning + infinite-loop protection

**Recursive spawning is allowed** (a sub-branch can `message_branch` further
sub-branches for multi-level task decomposition), kept in check by:

- **Depth limit**: every delivery carries a **spawn chain** in Event metadata
  (`chain: [issuing branch, …, current]`). When `message_branch` runs and the
  chain length is ≥ `MAX_DEPTH` (default 8), it refuses and returns the reason to
  the model. Reply-back (the automatic followup) inherits the same chain and does
  not reset it, so back-and-forth between A and B counts toward the depth and
  stops automatically at the ceiling.
- **Self-send refusal**: a target pointing at **the issuing branch itself** (a
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

- Cancelling a branch **also interrupts every sub-branch it spawned**: maintain
  an "active sub-branches" list (protected by a thread lock) and on cancellation
  walk it calling `child.interrupt`, reusing TaskRunner's existing cancel plus
  `kill_active_runtime`.
- Sub-branches shut down gracefully (cleanup), leaving no zombie threads or
  subprocesses.

### 5.4 Sending to a branch that is "already running" (race)

When A messages B, B may be mid-turn. **Do not interrupt, do not drop — queue**:
append the message to B's branch and trigger processing once B's current turn
ends (the pendingWake idea from OpenCode). If B is idle, trigger immediately.

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

- `message_branch` goes through the event layer's synchronous `tool.before`
  consultation point: under an unattended + deny policy it is held for
  confirmation (this also applies to sub-branches, sitting outside the approval
  wrapper).
- Before delivering, validate that `target` (when not "new") actually exists
  (`db.get_session` is not None); if it does not, raise an error rather than
  silently creating it. The existing three-layer gating (check_fn / can_use /
  requires_approval) still applies.

### 5.9 Branch visibility

Branches are marked **internal (sub-spawned) vs. user-visible**: an internal
branch can only be triggered by `message_branch` and does not appear in the UI's
session picker (but it is still drawn in the DAG and can be listed by
list_branches so agents can address it).

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

**Backend (tools, `openprogram/functions/tools/agent_collab/`)**
- `message_branch.py` — the one core: deliver + trigger + auto reply-back +
  multi-source self-summarization
- `list_sessions.py` / `list_branches.py` — reuse db.list_*
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
- Session / branch list panel (WS handlers already exist) + a "pick target →
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
| Spawn (`target="new"`) | The agent calls once, a new branch runs a turn, and the result automatically follows up back to the caller; message_sent/replied events are visible in the event log |
| Listing | `list_sessions` / `list_branches` list the real multiple sessions / multiple branches |
| Send to an existing branch in the same session | A sends to branch B of the same session, A does not block, B runs a turn, the reply returns to A automatically |
| Cross-session | A sending to another session takes the same path; both frontends update live via ws.frame |
| Multi-source synthesis | With 2 source branches, each summarizes itself first and the target model synthesizes a new answer |
| Robustness (§5) | A↔B back-and-forth stops automatically at MAX_DEPTH; spawning 30 queues without blowing up; cancelling the parent stops every child; messaging a running B queues until its turn ends; the parent receives is_error when a child fails; oversized results are truncated with a file path |
| Safety (§5.7-5.9) | Under deny it is held by tool.before; a nonexistent target raises an error; sub-branches have no more privilege than the parent and stay out of the UI picker |
| Frontend | Pick a branch in the webui and send a message; the DAG shows communication nodes + the soft link edge on hover |

---

## 8. Key file reference

| Thing | Location |
|---|---|
| Sub-agent spawn + auto reply-back | `openprogram/agent/sub_agent_run.py`, `agent/task/runner.py` (spawn_task / _dispatch_followup) |
| Tool template + registration | `openprogram/functions/tools/task/task.py`, `functions/_runtime.py` (@function) |
| session/branch data layer | `openprogram/store/session/session_store.py` (list_sessions:658 / list_branches:832 / append_message:706 / set_head:814 / commit_turn:455) |
| Trigger a session to run a turn | `openprogram/agent/dispatcher/__init__.py` (process_user_turn:97) |
| Multi-source self-summarization | `openprogram/agent/compaction/branch_summarization.py` |
| List WS handlers | `webui/ws_actions/session.py:825`, `branch.py:221` |
| attach edge (drawing only) | `openprogram/agent/sub_agent_run.py` (write_attach_pointer_for_spawn) |
| Event bus | `openprogram/events/bus.py` (emit_safe / emit_ws_frame) |

> Note: "synthesize several branches" is provided by
> `message_branch(sources=[...])`; there is no separate tool. The multi-parent
> ContextCommit lineage record in the underlying `_merge.py` is reused to record
> "which branches this synthesis came from".

---

## Appendix: implementation status

The event layer (§3), `TaskRunner`, `SessionStore`, `process_user_turn`,
`branch_summarization`, and the automatic reply-back in `_dispatch_followup` all
already exist; `message_branch` and the two listing tools are built on top of
them.

Two points still diverge from this design and need to be closed during
implementation:

- **Naming spawned branches (§2.4)**: when `message_branch` calls
  `run_agent_turn`, it must pass a label to get a Stage 1 placeholder name, and
  it must hook into the Stage 2 automatic rename triggered by `finalize_turn`.
  Miss either one and the spawned branch shows only an 8-digit hex short id in
  the web UI.
- **Where the reply-back node lands (§2.5)**: when `_dispatch_followup` builds
  the `TurnRequest`, it must set `branch_from` explicitly to the caller point
  (caller_msg_id); otherwise `process_user_turn` takes the session `head_id` as
  predecessor and the reply is stitched onto the tail of the mainline instead of
  the caller's branch.
