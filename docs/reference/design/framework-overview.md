# Framework Overview — One Conversation from Input to Output

> This document ties the whole framework together: how single-turn and
> multi-turn context, the event layer, the agent run, collaboration, and the DAG
> mesh along the timeline of one conversation. Every `file:line` reference points
> at the current code. Parts that are designed but not yet landed are collected
> in "Known Boundaries" at the end.

**One sentence runs through the whole document: the subsystems are decoupled and
connected by a single process-wide event bus.** The dispatcher, the agent loop,
tool execution, storage, and collaboration never call each other's UI or
broadcast logic. They `emit` an event, and whoever cares subscribes
(`events/bus.py:141` `emit` / `:159` `subscribe`). The event layer has two lanes:
**asynchronous observation** (`EventBus` — nobody can stop what is already
happening) and **synchronous consultation** (`tool_gate` — the one place in the
framework that can stop a tool from executing).

---

## Part One — The Lifecycle of One Conversation

The data flow starts at "the user types a sentence". Each step records where it
happens, what event it emits, how single-turn differs from multi-turn, and how a
branch shows up.

### Data flow (timeline)

```
user types a sentence
   │
   ▼  [entry] process_user_turn(req)              dispatcher/__init__.py:97 (sync; runs its own asyncio loop to completion)
   │
   ├─▶ 1. session created / loaded                 :175 get_session / :177 create_session
   │
   ├─▶ 2. history resolution (branches first appear here)   :186–198
   │       branch_from=INHERIT_PARENT → db.get_branch() walks the active branch's parent chain (= multi-turn)
   │       branch_from=None           → [] (root-level fork, starts empty)
   │       branch_from="<node_id>"    → db.get_branch(sid, node) (sibling fork, sees history up to the fork point)
   │
   ├─▶ 3. user message persisted (written before the run, so a crash still leaves a record)   :258–312
   │       written as a DAG Call node with caller=ROOT (:298)
   │       emit chat_ack(:314) + user_message(:326)  ← live UI display
   │       emit_safe("user.prompt_submitted")        :346  ← onto the event bus
   │
   ├─▶ 4. turn context bound (ContextVar)           :366–436
   │       _current_turn_id.set(assistant_msg_id) :379   ← every coroutine in the turn reads the same turn id
   │       _store.set(GraphStoreShim)            :435   ← deep runtime / tools / @agentic_function write the same DAG
   │       assistant_msg_id = user_msg_id+"_reply" :164
   │       assistant placeholder row written + set_head  :460; status="running" :464
   │
   ├─▶ 5. ★ before the model call: the context engine runs ★   _run_loop_blocking :754
   │       a. ContextEngine.prepare(...)           :885  ← DAG history rendered into LLM messages (TurnPrep)
   │       b. should_auto_compact(prep)?           :896
   │            yes → snip (free: drop the oldest turn); still over → compact (LLM summarization) → prepare again (:907/:941/:976)
   │       c. assemble the prompt (added once per loop, so the prompt cache prefix stays intact)
   │       d. agent_loop([prompt], context, ...)   :1074 → into the core loop (see below)
   │
   ├─▶ 6. assistant message persisted             persist_assistant_message :676 (persistence.py:31)
   │
   ├─▶ 7. finalize (three context-subsystem jobs happen here)   finalize_turn :711 / dispatcher/finalize.py:175
   │       · head / token update
   │       · ContextCommit backfill                finalize.py:283
   │       · ctx_engine.after_turn(...)            finalize.py:308 → engine.py:437
   │            ↑ this is where "context.compaction_recommended" is emitted (pct computed against the budget)
   │       · git commit_turn(:356) + project auto-commit (:369 commit_turn_changes)
   │       · auto-title, usage feedback, snapshot eviction
   │       db.update_session(status="idle"/"done")  :727/:729
   │
   └─▶ emit chat_response(:735) → return TurnResult(:739)
```

### Entry point: the dispatcher

`process_user_turn(req, *, on_event, cancel_event)` is the framework's **only**
conversation entry point (`dispatcher/__init__.py:97`). It is **synchronous** so a
channel worker thread can call it directly; internally it starts its own asyncio
loop and runs `agent_loop` to completion. It returns a `TurnResult`
(`dispatcher/types.py:102`).

### turn_id bound to a ContextVar — the other spine of the decoupling

`_current_turn_id.set(assistant_msg_id)` (`:379`) is what makes depth-independent
attribution work. A ContextVar propagates along asyncio tasks, so **any**
coroutine within the turn — tool execution, `@agentic_function`,
`send_message` — reads the same turn id, which routes file backups and
sub-branch parent anchors to the correct assistant message. `_store` is bound the
same way (`:435`) so deep runtime code writes into the same SQLite DAG without
threading a handle through every signature. The `finally` block resets both on
success, exception, and early return alike.

### Context assembly: single-turn / multi-turn / branch

History resolution lives at `:186–198`, and this is where branching **first
shows up in the data flow**:

| Case | branch_from | History taken | Meaning |
|---|---|---|---|
| Normal append | `INHERIT_PARENT` | `db.get_branch(sid)` walks the active branch's parent chain | **multi-turn**: the whole history of the current branch |
| Root-level fork | `None` | `[]` | the LLM starts from nothing |
| Sibling fork | `"<node_id>"` | `db.get_branch(sid, node_id)` | history up to the fork point, uncontaminated by the active branch |

**The only difference between single-turn and multi-turn is the length of the
chain `get_branch` walks back**: single-turn holds just the user node written a
moment ago (hanging under ROOT), multi-turn holds a full parent chain.

> The read side is the three `get_branch` cases above. The write side of a fork
> is either pointing a user node's caller at something other than the active tail
> (`set_head` in the storage layer moves the UI pointer), or starting a new root
> through `send_message` (see §⑤). Read = three `get_branch` variants; write =
> move the head pointer or create a new root.

### ★ Before the model call: the context engine runs (the per-turn auto-compaction path) ★

This layer is easy to overlook and happens on every turn. Step 5 is
`_run_loop_blocking` (`dispatcher/__init__.py:754`), which runs **before**
entering `agent_loop`:

1. `ContextEngine.prepare(agent, session, history, model, tools)` (`:885` →
   `engine.py:194`) renders the DAG history **into LLM input messages** (DAG
   rendering `_build_messages_from_dag` `engine.py:558` by default; setting
   `context.render="legacy"` falls back to the commit chain). It returns a
   `TurnPrep` (`context/types.py:102`).
2. When `should_auto_compact(prep)` (`:896`) is true — **this is the path that
   actually fires when context exceeds the budget** — `snip` runs first (free:
   drop the oldest turn); if that is not enough, `_ctx_engine.compact(...)` (LLM
   summarization) runs, then **prepare runs again** (a three-stage retry at
   `:907`/`:941`/`:976`).
3. The prompt is assembled (added once per loop, so the cached prefix is not
   broken by repetition).
4. `agent_loop([prompt], context, config, ...)` (`:1074`) enters the core loop.

> "Compact once at the start of the turn" and the "idle-gap microcompact" at the
> end are two different compaction paths (see §③).

### Core loop: call the model → tools → feed results back → repeat

`agent_loop` (`agent_loop.py:114`) builds an `EventStream`; the inner loop of
`_run_loop` (`:205`, loop at `:236`) does:

1. push `AgentEventTurnStart` (pushed on every inner turn, `:246`).
2. `_stream_assistant_response` calls the model (`:260`), emitting
   `model.response_started` (`:442`) and `model.response_completed` (`:466`).
3. extract `ToolCall`s (`:273`). If there are tools, `_execute_tool_calls`
   (`:278`) runs and the results are appended back into the context (`:288–290`).
4. push `AgentEventTurnEnd`.
5. break when there are no more tools and no steering / follow-up.

`MAX_INNER_ITERATIONS = 50` (`:226`) is the hard cap against an endless "just one
more tool".

### Tool execution: the tool.before interception

`_execute_tool_calls` (`agent_loop.py:654`) does this for each tool call:

1. push `AgentEventToolStart` (`:675`) + the plugin hook `TOOL_BEFORE_USE`
   (`:686`, best-effort).
2. **event-layer tool.before**: `make_event("tool.before",...)` + `emit` (`:695`)
   — one event serving both the asynchronous observers and the synchronous
   consultation.
3. **synchronous gate**: `decide_tool_gate(before_ev)` (`:701`), **the one place
   in the framework that can stop a tool** (`events/tool_gate.py:53`). Any gate
   returning deny blocks the call (reasons merged); a gate that raises counts as
   allow (fail-open). A blocked call raises `ToolGateDenied` (`:708`) and the deny
   reason goes back to the model as an error tool result. **It applies to
   subagents too** — the gate sits outside the `permission_mode` approval
   wrapper, so `bypass` cannot switch it off (`events/tool_gate.py:14–15`).
4. `tool.execute(...)` (`:731`), with a cwd snapshot + file checkpoint taken
   around it.
5. push `AgentEventToolEnd` (`:758/:766`) + emit `tool.after` (`:772`).
6. assemble a `ToolResultMessage` and feed it back (`:792`). Steering is checked
   after each tool (`:806`); when it hits, the remaining tools are skipped and the
   steering message is fed back instead.

### Persistence + finalize

- Error path (`:605–639`): the error is folded into the placeholder row or a
  standalone error node, and `head_id` moves to the failed turn
  (`:612/:618/:622`).
- Success path: `persist_assistant_message` (`:676`,
  `dispatcher/persistence.py:31`) writes the assistant message, then
  `finalize_turn` (`:711`, `dispatcher/finalize.py:175`) runs. **The context
  subsystem does three things inside finalize**: (1) head/token update;
  (2) **ContextCommit backfill** (`finalize.py:283`, freezing this turn's
  compaction decision into an immutable per-turn commit); (3) **`after_turn`**
  (`finalize.py:308` → `engine.py:437`, **where `context.compaction_recommended`
  is emitted**: usage feedback + pct against the budget). Then git `commit_turn`
  (`finalize.py:356`) + the project auto-commit (`:369`).

### DAG updates run throughout, not as a separate step

The user node (`:298`), the assistant placeholder, every tool result, and the
nodes inside an `@agentic_function` all land in the same `GraphStoreShim` through
the `_store` ContextVar. At the end of the turn `commit_turn`
(`session_store.py:504`) commits the whole working tree as one turn — append-only,
with no mutable "current state" mirror file, so two agents writing concurrently
never collide on the same file.

---

## Part Two — Layer Reference

### Event table

The events actually in flight. Two kinds: typed events on the bus (asynchronous
observation + synchronous consultation), and `ws.frame` envelopes passed through
to the front end.

| Event type | Emitted by (file:line) | Consumed by | Notes |
|---|---|---|---|
| `user.prompt_submitted` | dispatcher `:346` | proactive observer (`proactive/state.py:61`) | user message committed |
| `tool.before` | agent_loop `:695` | **tool_gate (synchronous)** + observers | the only interception point |
| `tool.after` | agent_loop `:772` | observers | carries `is_error` |
| `model.response_started` | agent_loop `:442` | observers | model stream begins |
| `model.response_completed` | agent_loop `:466` | proactive wrap-up policies | wrap-up timing check |
| `subagent.started` / `.ended` | task/runner `:115` (origin=`system`, session passed explicitly because a worker thread's ContextVar is unreliable) | observers | subagent state funnel |
| `branch.message_sent` | send_message `:266` | observers + `ws.frame` | from/to/is_new/sources |
| `branch.message_replied` | send_message `:344` | observers + `ws.frame` | carries is_error |
| `question.asked` | questions `:164` (also `emit_ws_frame` `:161` for the front-end card) | channels question bridge (`_question_bridge.py:43`) | both on the bus and as a ws frame |
| `question.replied` | questions `:275` (`resolve_question_and_broadcast:262`) | front end | **ws frame only** |
| `question.rejected` | questions `:173/:276` | front end (handled as "withdrawn") | **ws frame only** |
| `context.compaction_recommended` | **engine `after_turn` `:437`** (called from finalize) | UI / proactive | pct against the budget |
| `context.compacted` | context engine | UI / observers | compaction happened |
| `file.changed` | functions watcher and others | observers | file change |
| `channel.message_inbound` | channels | observers | inbound message |
| `memory.ingest_started` / `.ended` | memory | observers | memory ingestion |
| `skills.changed` / `plugins.update_available` / `sessions.listed` / `branches.listed` | various subsystems | UI / observers | lists and available updates |
| `ws.frame` (`events/bus.py:115`) | external sources via `emit_ws_frame` (`:118`) | `webui/server.py:1192` (broadcast verbatim) | passthrough envelope: external sources never touch webui `_broadcast` directly |

**Subscriber sites**: the proactive engine subscribes to **all** events and
filters by `on` (`proactive/engine.py:145`); the webui subscribes only to
`ws.frame` (`server.py:1192`); the channels question bridge subscribes only to
`question.asked` (`_question_bridge.py:43`).

**Event contract**: `emit` is fire-and-forget, and a handler that raises never
propagates back to the emitter (`events/bus.py:141–157` + `_call:182–198` prints to
stderr); an async handler with no running loop is skipped; `emit_safe` (`:96`)
wraps the whole thing in try/swallow. The event layer never breaks the caller's
code path.

---

### ① Storage — SessionStore, branch = (session, head)

**Responsibility**: one git repo plus an in-memory index per session;
append-only, with no mutable current-state mirror.
**Key files**: `store/session/session_store.py`, `store/session/memory_index.py`.
**Mechanisms**:
- `_open(sid)` (`:413`) returns `(GitSession, SessionMemoryIndex)`, LRU-cached and
  evicted by capacity; the index can be rebuilt losslessly from git.
- **branch = (session, head pointer)**: `get_branch(sid[, head])` (`:817` /
  `memory_index.py:101`) walks the `predecessor`/`caller` parent chain; `set_head`
  (`:880`) moves a single-valued UI pointer stored in meta. A fork is simply
  another chain walked from some node, and the two never contaminate each other.
- `commit_turn(sid, msg)` (`:504`) commits the working tree as one turn.
**Events**: `sessions.listed`, `branches.listed`.

### ② Event layer — bus + tool.before interception

**Responsibility**: process-wide fan-out that decouples every subsystem.
**Key files**: `openprogram/events/` (bus.py / tool_gate.py), `agent/questions.py`.
**Mechanisms**:
- `EventBus` (`:129`): typed `subscribe(handler, types=...)` (`:159`) + legacy
  channel `on` (`:208`); process singleton `get_event_bus()` (`:241`,
  double-checked locking).
- **synchronous tool.before interception**: `register_tool_gate`
  (`events/tool_gate.py:38`) / `decide_tool_gate` (`:53`), taking the strictest verdict
  and failing open. A gate must be fast — no LLM calls, no slow IO.
- Question subsystem: `QuestionRegistry` (`questions.py:61`) — a process-wide
  pending table, claim-once, thread-safe.
**Events**: see the table above.

### ③ Context engine — assembly / compaction / ContextCommit

**Responsibility**: render DAG history into LLM input and make compaction
decisions.
**Key files**: `context/engine.py`, `context/microcompact.py`,
`context/references.py`, `context/render.py`, `context/commit/`,
`context/rules/`, `context/tool_aging/`; finalize lives in
`agent/dispatcher/finalize.py`, not under `context/`.
**Mechanisms**:
- `ContextEngine.prepare(...)` (`engine.py:194`) returns a `TurnPrep`
  (`context/types.py:102`). It renders from the DAG by default
  (`_build_messages_from_dag:558`) and falls back to legacy on failure
  (`:218–220`), so one bad commit does not take down the whole turn.
- **Two compaction paths** (distinct):
  - **turn-start auto-compact (primary)**: `should_auto_compact` true → `snip`
    (free: drop the oldest turn) → still over, `compact` (LLM summarization) →
    re-prepare. Lives in `_run_loop_blocking`, before each model call (Part One,
    step 5).
  - **idle-gap microcompact (secondary)**: `microcompact.py:76`, triggered
    **only after more than 3600s idle** (`GAP_THRESHOLD_SECONDS:45` — cleanup at
    zero extra cost once the prompt cache has expired). It keeps the 5 most recent
    tool_results and replaces older large ones with placeholders. It is
    **non-destructive**: it returns a copy and does not touch DAG nodes.
- **Reference scan**: `ReferenceTracker.build` (`references.py:114`) is a cheap
  substring scan marking tool_results referenced by later text so they escape
  compaction. Its results currently feed logging only; the ContextCommit rules do
  not consume them yet (see Known Boundaries).
- **ContextCommit** (`commit/types.py:104`): each turn's compaction decision
  frozen into an immutable commit, backfilled by `finalize_turn`
  (`dispatcher/finalize.py:283`).
- **`after_turn`** (`engine.py:437`, called from `dispatcher/finalize.py:308`):
  usage feedback + emitting `context.compaction_recommended`.
**Events**: `context.compaction_recommended`, `context.compacted`.

### ④ Agent run — loop + tool registration + subagents

**Responsibility**: drive the model↔tool loop, execute tools, spawn subagents.
**Key files**: `agent/agent_loop.py`, `agent/agent.py`, `agent/sub_agent_run.py`,
`providers/utils/event_stream.py`, `agent/management/gating.py`.
**Mechanisms**:
- `EventStream` (`event_stream.py:15`): async-iterable plus a terminal value;
  provider dict events are normalized into typed ones.
- `agent_loop` / `_run_loop` (`:114/:205`, hard cap 50 at `:226`) +
  `_execute_tool_calls` (`:654`).
- `Agent` supports `steer` (interject mid-run), `follow_up` (append at wrap-up),
  and `prompt`.
- **Static admission for tools / skills / MCP**: `gate(name, disabled, allowed,
  categories)` (`management/gating.py:38`, fnmatch, resolved in the order
  disabled→allowed→categories). This is *static* admission — agent.json declaring
  who may use what — and is a separate mechanism from ②'s runtime `tool_gate`
  interception.
- **Subagents**: `run_agent_turn(sid, prompt, agent_id, branch_from, label)`
  (`sub_agent_run.py:41`) is another call into `process_user_turn` (`:96`) with
  `source="agent_spawn"` and `permission_mode="bypass"` (`:89`). It returns an
  `AgentTurnResult` (`:32`, `head_id` = the new branch tip); `label` becomes a
  named branch via `set_branch_name` (`:109`).
**Events**: `model.response_started/completed`,
`subagent.started/ended` (the last pair emitted by task/runner).

### ⑤ Collaboration — send_message + cross-session + guards

**Responsibility**: deliver messages between branches and sessions, run the
target branch, and bring the reply back.
**Key files**: `functions/tools/send_message/send_message/send_message.py`,
`functions/tools/send_message/list_branches/list_branches.py`.
**Mechanisms**:
- `send_message(message, to, sources, agent_id, wait)` (`:393` →
  `_send_message_impl:186`).
- `to` semantics (`_parse_to:167`): `new` (a new root in the current
  session) / `new:SID:MSG_ID` (fork a node and inherit its chain) / `SID:HEAD`
  (deliver to an existing branch = run one more turn from its head).
- The parent anchor `_resolve_parent` (`:74`) reads the dispatcher's session/turn
  ContextVars and **falls back to the session head when the turn id is missing**.
- **Source aggregation**: `_gather_sources` (`:128`) wraps each source branch's
  tip text in a `<branch source=...>` block and prepends it for the target model
  to synthesize.
- Asynchronous delivery (the default) is handed to the task runner
  (`run_agent_turn_async`); when the run completes it writes an attach pointer and
  dispatches a follow-up back to the **initiating** session, so replies flow back
  automatically.
- **Guards**: the depth guard `MAX_SPAWN_DEPTH=8` (`:35`, checked at `:209`;
  children inherit depth+1, and an A↔B round trip counts too); a self-reference
  guard (messaging your own current turn is rejected outright); the target session
  must already exist and is never silently created; oversized replies go through
  `_clip_result` (`:365`, >30000 chars are written to a file and the path is
  returned); and tool.before interception applies (an attended gate can block it).
**Events**: `branch.message_sent` (`:266`), `branch.message_replied` (`:344`);
plus `emit_ws_frame("branch_message",...)` (`_emit_branch_ui:107`), which renders
a "sent / replied" line in the initiator's chat stream.

---

## Known Boundaries

- **tool.before observes and denies but cannot mutate or veto by plugin**: the
  plugin hook `TOOL_BEFORE_USE` is explicitly marked future work
  (`agent_loop.py:682–684`).
- **The gate's three-state "ask" is not wired to ApprovalRegistry yet**:
  `Gate.ask` is noted as pending that connection (`proactive/actions.py:29`), and
  the "critical fail-closed" tier awaits the rules layer (`events/tool_gate.py:13`).
- **Reference-scan results are not consumed by ContextCommit rules**: they feed
  logging only (`engine.py:204–212`).
- **The DAG render keeps a fallback path alongside the normal one**: a bad commit
  falls back to legacy (`engine.py:218–220`).
- **The core entry points lack covering tests**: several paths through
  `process_user_turn` / `agent_loop` are marked "no covering tests found".
- **A worker thread's ContextVar is unreliable**: `subagent.started/ended`
  therefore pass the session explicitly (`agent/task/runner.py:111–115`), and
  `send_message._resolve_parent` carries a head fallback for the same reason.
- **`ContextEngine.after_turn` exists at two levels**: the abstract base stub
  (`engine.py:124`) is `pass`; the concrete engine implementation
  (`engine.py:437`) is the one doing the work (usage feedback + emitting
  compaction_recommended), called from `dispatcher/finalize.py:308`.

---

## Anchor Quick Reference

Dispatcher entry `dispatcher/__init__.py:97`; turn_id binding `:379`;
history/branch resolution `:186–198`; user node write `:298`; **prepare /
auto-compact before the model call** `_run_loop_blocking :885/:896/:1074`;
finalize `:711` / `dispatcher/finalize.py:175` (ContextCommit backfill `:283`,
after_turn `:308` → `engine.py:437`). Event bus `events/bus.py:141/159/241`;
tool.before interception `agent_loop.py:695/:701` + `events/tool_gate.py:53`. Context
`engine.py:194` plus the two compaction paths (auto-compact
`_run_loop_blocking:896` / microcompact `microcompact.py:76`). Agent loop
`agent_loop.py:114/205/654`; subagents `sub_agent_run.py:41`. Collaboration
`send_message.py:186/393`, depth cap `:35`.
