# Agent collaboration: one cross-branch communication primitive

All of agent collaboration collapses into **a single primitive: cross-branch
communication**. An agent can spawn other agents and send messages to other
branches or other sessions. These look like different operations, but
**underneath they are the same thing**: deliver content to a branch, trigger
that branch to run a turn,
and send the result back to the caller automatically. Everything is a tool call,
and everything is built on the existing event layer.

> §1 is the vocabulary the whole tool surface is built on; read it first.

---

## 0. Core: there is only one primitive

The whole collaboration story has exactly one primitive:

> **Cross-branch communication** = **deliver content** to a branch (another
> branch of the same session, a different session, one created on the spot, or
> one that already exists) → **trigger** that branch to run a turn (the model
> reads the delivered content) → the result is **sent back automatically** to
> the caller (append a new message + trigger the caller to run a turn, and
> the caller reads it and continues).

Every collaboration operation is a **parameterization** of that primitive:

| Operation | Which use of communication it is | Tool |
|---|---|---|
| **Spawn a sub-agent** | **Create** a branch + deliver a message + auto-reply | `agent` |
| **Dispatch a task to an existing agent** | Deliver a **tracked task** to an existing branch + auto-reply | `agent(to=…)` |
| **Message an agent** | Deliver a message to an **existing** branch + auto-reply | `send_message` |

Delivered content is always read and used by the target model. Count is
arbitrary (spawn can create N, a message can go to many), so it is not a
distinguishing dimension. Both uses share one deliver → trigger → reply-back
path.

`attach` is not an operation. It is how a communication result is drawn as a
"return edge" on the DAG (marking which branch the result came back from).

---

## 1. Four domains, one word each

Collaboration is four domains. Each owns one noun and one set of tools, and
the words never overlap — a term means the same thing everywhere it appears.

| Domain | Noun | Tools | What it is |
|---|---|---|---|
| Planning | **todo** | `todo_create` / `todo_update` / `todo_list` | A hand-written checklist: entries, status, owner, dependencies. Written intent, and nothing runs because an entry exists |
| Execution | **task** | `job_output` / `job_stop` / `list_jobs` | Work handed out and now running: a task id, a status, a result |
| Entity | **agent** | `agent` / `list_agents` / `archive_agent` | What does the work: create a new one, hand work to an existing one (`to=`), list the agents, archive one that is finished |
| Communication | **message** | `send_message` / `read_conversation` | Messaging and reading: deliver a message, read any branch in full |

Writing "benchmark the parser" on the todo list starts nothing. `agent(…)`
starts something, and what comes back is a task id. The list says what was
intended; `list_jobs` says what is running.

An agent's conversation is a **branch**: a `(session_id, head_id)` pair
inside a session. Two heads in one session are two branches of one
conversation; two sessions are two conversations. Every agent address is a
branch — `"SID:HEAD"`, or the branch's name.

### Only the dispatcher operates on a task

Three things can be done to dispatched work, and only the dispatcher can
do them:

| What the dispatcher can do | What it means |
|---|---|
| The result comes back | When the task ends, its reply lands in the dispatcher's conversation automatically, whether or not the dispatcher is still waiting |
| It can be stopped | `job_stop` cancels the task; one still queued is withdrawn before it ever runs |
| Cancellation cascades | Stopping a task stops everything that task dispatched, all the way down |

`read_conversation` makes every task id readable, so ownership is checked
rather than assumed: `job_output` and `job_stop` refuse a task another
session dispatched (§5.10). Calls with no session context (the user, the
UI) are not gated.

`send_message` carries none of the three, which is why anyone may write to
anyone. It delivers a message and the receiver answers or does not. It
creates no task id, cannot be cancelled, and does not cascade, so a message
never interrupts work that is already running.

### `agent` has two modes

| Call | What happens |
|---|---|
| `agent(prompt=…)` | Creates a new agent and runs it. Blocks for the reply, or returns a task id with `run_in_background=true` |
| `agent(prompt=…, to="reviewer")` | Creates nothing. The prompt goes to the existing agent named `reviewer` as a tracked task and runs as its next turn, queued behind whatever it is doing now — one turn at a time. Always returns a task id |

Both produce a task; only the first produces an agent. `to` and `start_from`
are mutually exclusive: the target already has a history, so there is no
fork point left to choose.

A whole delegation reads in the four words:

```
todo_create("benchmark the parser")            → todo #1 on the board
todo_update("1", status="in_progress")
agent("benchmark the parser", "bench",
      run_in_background=true)                  → job_id=t_7f2
list_jobs()                                   → t_7f2 running — bench
send_message("how far along?", to="bench")     → the agent answers, no task created
job_output("t_7f2")                           → the result, when it lands
todo_update("1", status="completed")
archive_agent(to="bench")                      → archived out of the agent list
```

### Names shared with Claude Code

`agent`, `list_agents`, `send_message`, `job_output` and `job_stop` carry
the same meaning here as in Claude Code, deliberately — a model that knows
those names already knows these tools.

One name deliberately differs. Claude Code's `TaskList` is a todo planning
board, not a view of running work. The planning board here takes the
`todo_*` prefix instead, so the collision cannot happen and `list_jobs`
keeps its literal reading: the tasks that are running.

Three tools have no Claude Code counterpart — `list_jobs` (there, a model
cannot enumerate its background tasks), `archive_agent` (archiving an agent
out of the agent list, §2.6), and `read_conversation` (another agent's
history as a readable transcript, rather than the raw session files).

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
    start_from: str = "clean",          # "clean" / "inherit" / "SID:MSG_ID"
    run_in_background: bool = false,    # false=block for the reply; true=job_id
    to: str = "",                       # dispatch to an EXISTING agent instead
    archive_when_done: bool = false,    # archive the spawned agent at terminal state (§2.6)
) -> str
```

`start_from` picks where the new branch starts: `"clean"` (default) is a new
root seeing only the prompt; `"inherit"` forks off the calling turn with the
full chain; `"SID:MSG_ID"` forks off that exact node (any session),
inheriting the chain up to it. `run_in_background=true` returns a `job_id`; its
companions `job_output(job_id)` (block for the result) and
`job_stop(job_id)` (cancel) manage the background form.

`"SID:MSG_ID"` is an exact fork address. Both the session and the message must
exist before the spawn is admitted; the message is not snapped to a branch's
current tip. An archived branch may still be used because this operation reads
recorded history and creates a new branch rather than delivering work to the
archived branch.

When the address names another session, keep the two session roles separate.
If session S at node A starts from `"T:M"`, the new branch and the canonical
Job run in target session T, with M as the exact predecessor. The Job records
`parent_session_id=T`, `parent_msg_id=M`, `caller_session_id=S`, and
`caller_msg_id=A`. Its attach card is stored beside A in source session S, but
the card's `attach.session_id` is T and its terminal `head_id` is the new target
branch tip. Writing or finalising that card does not move S's HEAD, and the
spawned turn uses `advance_head=false`, so it does not replace T's selected
HEAD either. An asynchronous completion may subsequently advance S's HEAD by
writing the ordinary reply-back turn described in §2.5.

**`to=` — dispatch a tracked task to an EXISTING agent.** With `to` set the
tool creates no branch: the prompt is handed to the named existing branch
as a formal task. Addressing is send_message's, verbatim (`"SID:HEAD"`
snaps onto the branch's current tip; a branch name resolves exact-first,
then unique prefix; ambiguity lists candidates). What distinguishes a
dispatch from a message is task tracking:

- A **Task entity** is created (the runner's task record): the dispatcher gets
  a `job_id` back immediately, `job_output` waits on it, `job_stop`
  withdraws or cancels it, and `list_jobs` shows it.
- Delivery reuses the message machinery: an idle target runs the task as
  the next turn on its branch; a busy target queues it in its inbox
  (§5.4) — the Task entity is pre-created in `pending` so the id exists
  while the work waits, and the drain runs the SAME task. The delivered
  turn is prefixed with a task receipt header (`[task from SID:HEAD] This
  is a tracked task …`) so the target knows the reply is the task's
  result, returned to the dispatcher automatically.
- At terminal state the result flows back as a followup notification into
  the dispatcher's session, with the reply text carried inline in the
  notification. A dispatch creates no branch, so there is no attach
  pointer: attach records that a call created the branch it points at,
  which is untrue of work handed to an agent that already exists.
- `to` and `start_from` are mutually exclusive (the target branch keeps its
  own history; a fork-point choice contradicts that — the call errors).
  `to` is always asynchronous, so `run_in_background` is ignored.
  Dispatching to the caller's own current branch is refused (do the work
  directly). A dispatch spends the message budget, not the spawn budget
  (§5.1) — it creates no agent.

**`send_message` — talk to an EXISTING agent:**

```
send_message(
    message: str,                       # content/instruction delivered to the target
    to: str,                            # see to values below
    agent_id: str = "main",             # which agent the target runs as
) -> str
```

**`to` values — every value names a branch that already exists:**

| to | Meaning |
|---|---|
| `"sid:head"` | Deliver message to an existing branch. The node names the branch, not a fork point: delivery always lands on the branch's current tip, so a stale head (the branch ran more turns since) is still a valid address and never forks off history. A node that is a shared ancestor of several branches is ambiguous — the error lists the candidates (name + `sid:current-tip`). Snapping applies to live branches only: the head of a branch a merge absorbed resolves to itself (§2.6). To fork off a specific node, use `agent(start_from="sid:msg_id")`. |
| `"<branch name>"` | Deliver to a named branch. Tried when the value is not `SID:HEAD` syntax: exact name match wins, a unique prefix is accepted next; several matches return an error listing the candidates (name + `sid:head`), zero matches point to `list_agents`. `list_agents` marks each branch's name so the model can address by name directly. |

The removed spawn addressing (`to="new"` / `"new:sid:msg_id"`) is rejected
with an error that points to the `agent` tool.

Every delivery (direct or queued, see §5.4) is prefixed with a
sender-receipt header —
`[message from SID:HEAD] To reply, use send_message(to="SID:HEAD"). Replying is
optional …` — so the receiver knows who sent it, how to answer, and that not
answering is legitimate. Agent-tool spawns carry the bare prompt: a spawned
agent has no earlier sender to reply to.

One use:

- **Message an existing branch/session**: `to="sid:head"` → deliver message
  to that branch, trigger one turn, and the answer is sent back automatically.
  Cross-session uses the same path (`to` can be any session).

Both tools drive the same primitive; a spawn is the same
deliver → trigger → reply-back flow with a freshly created branch as the
target.

**One flow, whichever tool starts it:**
1. The target branch is resolved — `agent` creates it (`start_from` picks
   where it starts); `send_message` resolves `to` onto an existing branch's
   current tip.
2. The receipt header plus the message is delivered there.
3. The target runs one turn and the model reads everything delivered.
4. The reply comes back on its own. The send returned instantly, so the
   caller never blocked; when the target finishes, its answer is appended to
   the caller's conversation and the caller runs a turn to read it.

### 2.2 Referencing other branches

A message is plain text, exactly like a user message. When the target should
consider other branches, the sender writes that into `message`: quote the
conclusion directly (each branch's reply already flowed back to the sender via
reply-back), or name the branch (`SID:HEAD` or its name) and the target reads
it itself with `read_conversation`. The target model decides how much of the
named branch to read, so context stays bounded without a dedicated aggregation
parameter.

### 2.3 `list_agents` — seeing each other (a precondition for communication)

```
list_agents(scope="session", limit=20, agent_id?, source?) -> str   # db.list_sessions + db.list_branches
```

`scope` picks the view: `"session"` (default) lists the current session's
branches — the agents spawned here; `"all"` widens to every session, most
recently active first, without previews; `"archived"` lists the current
session's archived branches (§2.6), which the other two scopes hide.

An agent's conversation is stored as a branch in the session DAG, so "which
agents can I talk to" = "which sessions exist, and which branches does each
one have". One call lists them all, grouped by session: each session line
carries its id, title, agent, and busy/idle status
(`run_control.is_turn_running`; omitted when the probe fails), and each
branch line carries its name (if any), a ready-to-use `to="SID:HEAD"`
address, its turn count and approximate size (`— 3 turns, ~2k chars`; sizes
under 1000 characters show as `<1k chars`), and a preview of its tip. The
size lets the model pick a sensible `max_chars` before reading the branch
with `read_conversation`. This is the entry point for "two agents seeing
each other".

### 2.4 New branches must have names

Every time the `agent` tool creates a branch,
**the branch must be given a name** — otherwise the web UI can only show an
8-digit hex short id and a pile of branches becomes indistinguishable.

- **Named immediately (Stage 1)**: at creation, pass a short label to
  `run_agent_turn(... label=…)` → `store.set_branch_name`. The label is taken
  from the delivered prompt (truncated to ~24 characters), or the model
  supplies a name explicitly in the call (`description`). This way a branch has a readable name
  from the moment it is created, with no wait on an LLM.
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

For the asynchronous reply-back, `_dispatch_followup` feeds the
target branch's reply into the delivery session as a **synthetic user-role
turn**. **Key rule: the reply-back `TurnRequest` leaves `branch_from` unset
(INHERIT_PARENT) — the dispatcher resolves it to the delivery session's
current HEAD and advances it.** A per-delivery-session follow-up lock
(`JobRunner._followup_lock`) serialises concurrent completions, so N
sub-tasks finishing produce one serial chain
`… → notice₁ → answer₁ → notice₂ → answer₂` — each follow-up reads a HEAD
that already contains the previous answer.

Why the reply is not pinned to the spawn node (`caller_msg_id`): with N
parallel sub-tasks forked from one turn, every reply-back would land as a
sibling hanging off that same node, and the single user message that
triggered the spawns would be answered N times on N parallel branches.
Anchoring at HEAD keeps all N completions on a single conversation path.

The return-flow provenance is not lost by this anchoring: the **attach
pointer** written at spawn time does hang off
`predecessor = caller_msg_id`, so the DAG still shows which turn each
sub-branch forked from and which branch each result flowed back from.
The sub-branch itself stays a parallel independent branch and **does not
merge back into the mainline**.

For a cross-session spawn, the pointer remains in the initiator's session but
references the target `(session_id, head_id)`. Terminal finalisation reads the
target branch and its ContextCommit in the target session, then patches the
card in the initiator's session. The source-side initiating node is marked
`spawn_out`; the target-side first `agent_spawn` user node records
`caller=<source node>` plus `metadata.spawned_from_session=<source session>`
and is projected as `spawn_remote`. Because the source session has a real
attach pointer, its asynchronous follow-up consumes the result through attach
expansion rather than duplicating the reply inline. `send_message` and
`agent(to=...)` create no branch and no attach pointer, so their replies remain
inline and receive neither spawn marker.

### 2.6 Archiving: removing an agent from the agent list

Branches live forever in the session DAG — fork, replay, and
`read_conversation` all depend on that — so without an archive flag
`list_agents` accumulates every agent ever spawned, and the model keeps
addressing workers whose job finished long ago. Archiving is that flag:
`archived: true` on the branch's meta entry, the same `branches` entry that
carries the name, written with `set_branch_meta` and read with
`get_branch_meta`. Sharing an entry with the name is safe because every
writer merges field by field under the index lock: Stage-2 auto-naming
(branch-naming.md) sets `name` and its own counters and cannot drop the
archive flag, and it skips archived branches anyway — an agent whose work
is finished needs no new name.

**Archiving stops new deliveries to a branch and keeps its history.**

| Operation on an archived branch | Behavior |
|---|---|
| `list_agents` (`scope="session"` / `"all"`) | Hidden |
| `list_agents(scope="archived")` | Listed: every archived branch, including one a merge absorbed |
| `send_message(to=…)` | Refused: `agent SID:HEAD is archived` |
| `agent(to=…)` | Refused, same message |
| `read_conversation` | Reads it as usual |
| `agent(start_from="SID:MSG_ID")` | Forks it as usual |

The refusal lives in exactly one place: `resolve_existing_target` (the
addressing both delivery paths share, §2.1) checks the flag right after it
snaps an address onto the branch's current tip, so every delivery inherits
the guard and no caller can route around it. `archive_agent` reaches
archived branches through that same resolver with `allow_archived=True`.

**Archiving is orthogonal to merging.** A merge absorbs a branch into
another one, and the absorbed head leaves `list_branches` because its
content is now reachable from the branch that absorbed it. That is a fact
about where content lives, and it happens on its own: the task runner
absorbs a background spawn's branch the moment the spawn completes
successfully. Archiving is a fact about the agent that worked on the
branch, and it is always an explicit act. Neither implies the other, so the
two are stored apart (`merged_heads` in the session meta,
`archived` on the branch entry) and read apart:

- `list_agents(scope="archived")` reads the archive flag off the branch
  entries (`store.list_archived_branches`) rather than filtering the live
  tip list, so every archived branch is listed whether or not a merge
  absorbed it. This is what makes `archive_when_done` observable on a spawn
  that succeeded, which is exactly the case where the merge comes first.
- The default scope and `scope="all"` list live branch tips, so a merged
  branch stays out of both, archived or not. That is the merge's own
  behavior and archiving does not change it.
- A merged head keeps addressing its own branch. `resolve_existing_target`
  snaps onto the current tip of a live branch; the head of a branch a merge
  retired is snapped nowhere and resolves to itself
  (`store.merged_heads`). Without that rule
  `archive_agent(to="SID:MERGED_HEAD")` resolves to whichever live branch
  absorbed the node, archives that branch instead, and reports success for
  it.

Two ways to archive:

- **`agent(archive_when_done=true)`** — the spawn declares up front that
  the agent it creates is a one-shot worker. The branch is marked at
  terminal state (`completed` / `errored` / `cancelled`), after the result
  has flowed back to the caller; the synchronous spawn form marks it once
  the result is in hand. The write is best-effort: a failed meta write is
  logged and the result still returns. Spawn-only — combined with `to=` the
  call errors, because a dispatch targets an agent it did not create.
- **`archive_agent(to, reason="")`** — archive an agent after the fact. `to`
  takes the same addresses as `send_message` (`"SID:HEAD"` or a branch
  name). Archiving an already-archived branch is an idempotent notice, not
  an error.

**Any session may archive any agent.** Archiving is not gated the way
`job_stop` is (§5.10), because it does not do what `job_stop` does: it
interrupts no running work and deletes nothing. A task already running on
the branch runs to its end, `read_conversation` still reads the branch and
`agent(start_from="SID:MSG_ID")` still forks it. All that changes is that
the branch leaves `list_agents` and stops accepting `send_message` and
`agent(to=)`. Any session can see that an agent is finished, so any session
can say so.

**Archiving is one-way; there is no unarchive.** The flag means "this
conversation is finished", and a finished conversation whose memory is worth
reusing is forked with `agent(start_from="SID:MSG_ID")` — a fresh branch
with its own name and its own lifecycle, which is what reusing it actually
requires. An unarchive tool would only be a second way to do the same thing.

---

## 3. What collaboration looks like while it happens

Collaboration runs on the framework's shared event layer, so it is visible
live rather than only in hindsight. Three effects follow from that, and they
are the whole of what a user or an agent needs to know about it:

- **Both sides update in real time.** A message delivered to another
  session appears in that session's UI as it lands, and the reply appears in
  the sender's UI when it comes back. Neither side has to reload.
- **Everything is on the record.** Deliveries, branch state changes and
  listings are written to the session's event log
  (`~/.openprogram/sessions/<sid>/events.jsonl`, always on), so a
  collaboration can be replayed and audited after the fact.
- **A delivery can be held for confirmation.** Under an unattended policy
  that denies side effects, `send_message` is stopped before it delivers and
  waits for approval. Sub-agents are held by the same gate, and
  `permission_mode=bypass` does not turn it off.

The event layer itself — the bus, the event model, the registry, the veto
protocol — is documented in
[proactive/event-layer](../proactive/event-layer.md).

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
   appends the reply to the end of A (△) + triggers A to run a turn; A reads
   it on that turn and can continue.
5. **Repeatable**: A can `send_message` B again — neither branch blocks and
   nothing is serialized.

Spawn (the `agent` tool) is another parameterization of the same flow and is
not listed separately.

---

## 5. Robustness and safety

Communication creates branches, triggers other branches to run, and writes
across sessions. Those side effects need boundaries.

### 5.1 Three budgets bound every chain

Recursive collaboration is allowed — a spawned agent can message further
agents for multi-level decomposition — and three budgets keep it finite. A
**chain** is everything that grows out of one user turn. Two of the
budgets travel with the chain, each on its own counter; the third counts
siblings inside one turn.

| Budget | Setting | Default | Counter | What spends it |
|---|---|---|---|---|
| **Spawn depth** | `agent.max_spawn_depth` | 1 | `depth._chain_generations` | Creating an agent, and nothing else: `agent` without `to=`. The new agent runs one generation in |
| **Messages** | `agent.max_messages` | 8 | `depth._chain_messages` | Every hop: a spawn, a `send_message` delivery, an `agent(to=…)` dispatch, and a result flowing back |
| **Fan-out** | `agent.max_spawn_fanout` | 8 | `agent._fanout_used`, per (session, turn) | Creating an agent, counted per turn instead of per chain |

**Setting any of them to 0 removes that limit entirely** — nothing
accumulates against it and nothing is refused because of it.

**Reading a result spends a message and no generation.** The turn that
carries a finished agent's reply back is the *dispatcher's* turn, so it
runs at the dispatcher's generation count (`Task.caller_chain_generations`,
re-bound by `JobRunner._dispatch_followup`) and one message further
along. That keeps the most common multi-agent shape open: send a batch of
work out, read what comes back, send the next batch. One counter for both
budgets closes it — the coordinator's follow-up turn inherits the worker's
count of 1, and every later `agent` call in that chain is refused. The
message counter is what still ends such a chain: each wave costs messages,
and the eighth stops it.

```bash
openprogram config set agent.max_spawn_depth 2   # workers may open one more generation
openprogram config set agent.max_messages 0      # unlimited conversation between agents
openprogram config set agent.max_spawn_fanout 16 # wider parallel fan-out per turn
```

**What the budgets do when they run out.** A call that would overrun is
refused with a reason the model can act on, and it keeps every other
tool. Once the **message** budget is spent, `agent`, `job_output` and
`job_stop` leave the tool list altogether: every form of delegation
hands a message over, so a chain out of messages can do nothing with
them, and a tool sitting in the listing makes the model try to call it.
The generation budget never removes a tool, because a chain out of
generations still dispatches work to agents that already exist. Neither
does the fan-out budget: it is spent inside a turn, and the tool list is
frozen at the turn boundary, so it can only refuse.

Typical behavior at the defaults (spawn depth 1, messages 8, fan-out 8):

- The main agent spawns workers. A worker asked to spawn again is told
  to do the work itself with its own tools.
- That same worker keeps `agent(to=…)` and `send_message`: it can hand
  work to agents that **already exist** and answer whoever wrote to it.
  Only creating a new generation is closed to it.
- The main agent spawns a wave of workers, reads their results as they
  come back, and spawns the next wave. Reading costs messages, never
  generations, so the workers stay one generation deep however many
  waves there are.
- A and B messaging back and forth stop after the 8th message of the
  chain, whichever of them is holding the turn. The reply hop re-binds
  the finished task's count instead of adding to it, so one round trip
  costs 1 and 8 buys eight round trips.
- A turn that calls `agent` a ninth time is refused and pointed at the
  eight agents it already has. The next turn starts a fresh fan-out
  budget, so this stops a runaway turn without becoming a quota on the
  session.

At `agent.max_spawn_depth: 2` a worker may open one more generation and
the third refuses. At `0`, `0` and `0` nothing is ever refused, and
runaway protection falls to the concurrency cap and the per-turn
iteration cap (§5.2) plus the user's Stop.

**Self-send refusal** is unconditional and independent of all three
budgets: a `to` pointing at the issuing branch itself is a direct cycle
and is refused immediately.

**Where the numbers come from.** Each default is calibrated against the
eight reference implementations surveyed in
`agent-collab-comparison.html` §05, and the reasoning is kept next to
each constant in the code (`agent.MAX_SPAWN_DEPTH`,
`agent.MAX_SPAWN_FANOUT`, `depth.MAX_MESSAGES`).

- **Spawn depth 1** is what openclaw, codex-cli V1, hermes-agent and
  opencode all settle on. Claude Code's 3 does not transfer: its leaked
  tree has no depth counter and strips the `Agent` tool from every
  subagent unless `USER_TYPE=ant`, so an external user's effective depth
  there is 1, and its async tool allowlist omits `Agent` outright, so a
  background subagent never spawns whatever the counter says. Depth 3
  applies only to synchronous nesting, where the parent's tool call
  blocks for the whole child run. Our unattended path is
  `run_in_background=True`, and 1 is the value Claude Code enforces
  there.
- **Messages 8** is anchored on openclaw, the only reference that counts
  the same thing: its agent-to-agent ping-pong stops after 5 alternating
  replies by default and 20 at most. 8 sits between them, which is where
  a counter that also pays for spawns and dispatches belongs.
- **Fan-out 8** covers the one runaway nothing else counted. A spawn
  hands its count to the child and leaves the parent's own untouched, so
  before this budget a single turn could call `agent` until the
  50-iteration cap stopped it. openclaw is the only reference with a
  true fan-out cap (5 live children per parent, range 1 to 20); hermes'
  3 and pi-mono's 8 validate the length of a batch argument, which does
  not transfer because `agent` creates one child per call. 8 is two
  widths of our four-worker pool, so a turn can fill the pool and keep
  one wave queued behind it.

**Two guards we looked at and did not take.** openclaw rate-limits
parent-to-child messages to one every 2 seconds, and hermes gives each
delegated subtask a 600 second timeout.

- The 2 second limit guards openclaw's *steer* path, which aborts the
  child's in-flight run, drains its queues and restarts it, so two
  steers close together abort each other mid-abort. Its non-interrupting
  sibling send has no rate limit at all. `send_message` is the
  non-interrupting kind: a busy target queues (§5.4) and the message is
  delivered as its own turn, so there is nothing to thrash.
- hermes' 600 seconds is a caller-side `Future.result(timeout=…)`, not a
  kill. On expiry it sets a cooperative interrupt flag and abandons the
  worker thread, and a child wedged in blocking I/O keeps running. We
  already have both halves of that and stronger: `job_output(timeout=)`
  is the same caller-side wait (default 30s, ceiling 600s), and
  `job_stop` cancels cooperatively, kills the active runtime and forces
  the entity terminal after 30s. What neither we nor hermes have is a
  deadline that fires with nobody watching. Adding one means scheduling
  `cancel_job` at submit time in `JobRunner`, and the bound that makes
  it rarely necessary is the 50-iteration per-turn cap below.

**How the counts travel.** Both counters live in ContextVars
(`send_message…depth._chain_messages`, `._chain_generations`). A chain
crosses three thread boundaries and each one has to hand them over
explicitly, because a Python thread starts with its ContextVars at their
defaults:

| Hop | How the counts arrive |
|---|---|
| Dispatcher → tool body | `copy_context()` in `functions/_runtime.py` carries both into the executor thread |
| Sender → task worker | Both are persisted on the Task (`chain_messages`, always sender + 1; `chain_generations`, sender + 1 for a spawn and unchanged for a dispatch) and re-bound by `JobRunner._run_one` |
| Task → reply follow-up | `JobRunner._dispatch_followup` re-binds the finished task's `chain_messages` and its `caller_chain_generations` in its own thread |

The reply hop is where the two budgets part company, and each direction
matters. Messages carry over from the child: the follow-up turn is where
A reads B's answer and writes the next message, so a follow-up that
started at 0 would give A a fresh budget on every round and the
8-message cap would never be reached. Generations go back to the
dispatcher's count: the follow-up creates nobody, and inheriting the
child's count left an agent that had read one worker's reply unable to
create any further agent in that chain.

The same thread also re-binds `_current_job_id` to the finished task's
`parent_job_id`, so a task A spawns while reading the reply belongs to
the same lineage cascading cancel walks (§5.3).

The session id those tools read (`run_control._current_session_id`) is
bound by `TurnBindings` for the length of the turn, alongside the turn
id, so it is present on every path into `process_user_turn` and not only
the ones whose caller bound it first. Binding fills in only when nothing
is bound: an entry point that owns the id for a scope wider than one turn
(the webui exec thread, a task runner worker, a channel adapter) keeps
it, so a nested turn for another session cannot repoint the cancel hook
or `runtime.ask` at a session that registered no turn token.

### 5.2 Concurrency limit + queueing

- Spawning runs on the `JobRunner` thread pool, capped by
  `OPENPROGRAM_JOB_WORKERS` (default 4). Spawn eight at once and anything over
  the cap **queues**, running as slots free up, without overloading anything.
  This is a global pool, so it bounds what runs at once and not how much
  work one turn can create. That is the fan-out budget's job (§5.1).
- Every turn, spawned ones included, stops after 50 inner tool-call
  iterations (`agent_loop.MAX_INNER_ITERATIONS`). A caller-supplied
  `max_iterations` can tighten it and never raise it. The stream ends
  cleanly and the turn counts as finished, so a model that keeps asking
  for one more tool call still returns. hermes-agent caps a delegated
  subagent at the same 50.

### 5.3 Cancellation propagation (cascading)

- Cancelling a task **also cancels every task it spawned**. Each spawn made
  from inside a running task records the chain on the Task entity
  (`parent_job_id`, defaulted from the runner's current-task ContextVar).
  `JobRunner.cancel_job` walks the persisted entities breadth-first over
  that chain (visited-set guard, so even a malformed cycle terminates):
  pending/queued descendants flip straight to cancelled without ever running;
  running ones go through the same per-task cancel path as the root —
  session cancel event + `kill_active_runtime` + the 30s force-cancel
  watchdog. No zombie threads or subprocesses remain.
- **Descendants are cancelled before the root.** Cancelling the root makes
  its worker drop out, and the freed pool slot immediately starts the next
  queued future, which is a descendant the cascade had not reached yet. It
  then ran a full turn for work the user had already stopped. Walking the
  chain first means the worker that picks the descendant up finds an
  entity already at `cancelled` and returns without calling
  `run_agent_turn`. Ordering only; `cancel_job` still returns the root's
  post-update entity, and `None` for a task id that resolves to no session.
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
  placement pattern as `jobs.json`), recording the delivery body, sender
  `SID:HEAD`, sender agent, the chain's message count at send time, and
  enqueue time. The sender immediately gets back "target busy, message
  queued, processed when its current turn ends".
- **Draining**: the dispatcher drains the inbox at turn end
  (`_process_turn_once` → `_drain_send_message_inbox`, on both the success and
  the error return), delivering each entry as one async turn through the
  normal path (`run_agent_turn_async` → auto-followup back to the sender),
  continued from the target's current head. Delivery-then-delete: an entry is
  removed only after its delivery turn was submitted — a crash between the two
  may re-deliver (acceptable); the reverse order could lose a message (not
  acceptable). A queued hop spends the message budget exactly like a direct
  one (§5.1).
- **Limits**: at most 50 pending entries per target — a full inbox drops
  the oldest and leaves a system notice in the dropped message's sender
  session; an identical message from the same sender within 60s of a
  still-queued copy is rejected as a duplicate, and the sender is told
  so. 50 is Claude Code's number for the same structure, a 50-entry ring
  that drops the oldest, and it is the only reference implementation
  with a mailbox to compare against. The 60s window has no equivalent
  anywhere: Claude Code dedups by message uuid and weclaw by inbound
  message id, both of which only catch a byte-identical retransmission
  of one message object and never a model that composed the same text
  twice. The check fires only against entries that are still queued, so
  the window bounds one thing, how long a sender waits before the same
  text counts as a deliberate resend instead of a retry loop.

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

- Under an unattended policy that denies side effects, `send_message` is held
  for confirmation before it delivers (§3). Sub-branches are held by the same
  gate, which `permission_mode=bypass` does not turn off.
- A `to` that names nothing is an error, never a silent creation. The regular
  permission gating applies on top.

### 5.9 Branch visibility

Branches are marked **internal (sub-spawned) vs. user-visible**: an internal
branch can only be triggered by `send_message` and does not appear in the UI's
session picker (but it is still drawn in the DAG and can be listed by
list_agents so agents can address it).

### 5.10 Task ownership (job_output / job_stop)

`read_conversation` can read any branch, so any agent can learn any
job_id — without a gate, any agent could wait on or kill work it never
dispatched. `job_output` and `job_stop` therefore verify ownership
before acting: the current session must be the task's dispatcher
(`caller_session_id`, or `parent_session_id` for a same-session spawn),
or an ancestor on the task chain (the current task is an ancestor via
`parent_job_id`, or the current session dispatched one of the task's
ancestors — the same lineage cascading cancel walks). Anything else is
refused: `[job_stop error] task {id} was not dispatched by this
session`. Calls with no session context (the user, the UI) are not
gated.

`job_stop` on a `to=`-dispatched task is state-dependent:

- **queued** (target was busy, task waiting in its inbox) → the entry is
  withdrawn from the inbox and the entity flips to `cancelled`. No
  session-level cancel is sent: the target is busy running someone
  else's turn, which a withdrawal must not kill.
- **running** → cancels that one turn on the target branch (the task's
  cancel event + session cancel bridge + runtime kill + 30s watchdog),
  not the target agent or its session.
- **terminal** → idempotent no-op.

### 5.11 Explicitly out of scope (and why)

- **An extra parentID field**: `(session_id, head_id)` plus caller/predecessor
  already forms the tree and the DAG already draws it, so no redundant field.
- **ID prefix classification** (fork_/msg_): existing id + name are enough for
  addressing, so no.
- **Retry / circuit-breaker policy**: failures are replied back to the model and
  the model decides; no fixed built-in policy (see §5.5).
- **Built-in aggregation functions** (voting, all-succeeded, etc.): synthesis
  means naming the branches in `message` and letting the target model read and
  synthesize them (§2.2). A model synthesizing is more flexible than a preset
  aggregation, so no fixed aggregation operators.

---

## 6. Behavior you can check

Each line below is independently observable — in the web UI, or in the
session event log.

| Behavior | What you see |
|---|---|
| Spawn (the `agent` tool) | The agent calls once, a new branch runs a turn, and the result automatically follows up back to the caller; spawn events are visible in the event log |
| Listing | `list_agents` lists the real multiple sessions and each one's branches |
| Archiving (§2.6) | An archived agent leaves `list_agents` and shows up under `scope="archived"`; `send_message` and `agent(to=)` refuse it while `read_conversation` and `agent(start_from=…)` still work; any session may archive any agent, and the flag is one-way; a spawn that completed and was merged is still listed under `scope="archived"`, and its head still addresses its own branch |
| Send to an existing branch in the same session | A sends to branch B of the same session, A does not block, B runs a turn, the reply returns to A automatically |
| Cross-session | A delivery to another session updates both sides live. `send_message` / `agent(to=...)` remain message-only. `agent(start_from="T:M")` creates the branch and canonical Job in T, keeps its card in the initiating session, and marks the source and target DAG nodes `spawn_out` / `spawn_remote` without moving either selected HEAD |
| Robustness (§5) | A↔B back-and-forth stops when the chain's message budget runs out, and a budget of 0 never stops it; spawning 30 at once queues instead of overloading; cancelling the parent stops every child; messaging a busy B queues and is delivered when its turn ends; the parent is told when a child fails; oversized results are truncated with a file path |
| Safety (§5.7-5.9) | Under a deny policy a delivery is held for confirmation; a nonexistent `to` raises an error; sub-branches have no more privilege than the parent and stay out of the UI picker |
| Frontend | Pick a branch in the web UI and send a message; the DAG shows the communication node plus the return-flow edge on hover |

---
