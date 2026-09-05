# Wiring the Proactive Layer into the Code — Design

> The proactive design itself lives in [`../proactive/`](../proactive/README.md).
> This document covers only where that design meets the existing OpenProgram
> code: which mechanisms it reuses, where events are emitted, where the gate
> mounts, and what its enforcement actually covers. On any conflict the design
> documents win.

## 1. Where the code lives

The event layer is an in-place upgrade of `openprogram/events/bus.py`:
`Event`, subscription by event type, and a process-level singleton. Taps live in
the individual source files that already know when something happened, not in a
central collector. The `openprogram/proactive/` package holds the rule layer and
is created only when the first rule consumer arrives; the event layer is useful
on its own before then.

The event model is the three core fields plus an open `metadata` pocket (design
`event-layer.md` §1). `turn` and `session` travel in metadata rather than as
fixed fields, so a source that has no notion of a turn is not forced to invent
one.

## 2. Mechanisms reused rather than rebuilt

Each role the design describes already has a working implementation in the
codebase:

| Role in the design | Existing mechanism | Location |
|---|---|---|
| In-process event fan-out | `EventBus` — implemented but idle; dispatcher and agent_loop bypassed it with direct callbacks | `openprogram/events/bus.py` |
| The gate's `ask` path | `ApprovalRegistry` + `_wrap_with_approval`: request, block and wait, approve or deny; a denial returns an is_error tool result | `openprogram/agent/permissions/approval.py` |
| The observer's `Prepare` background task | `JobRunner.spawn_job` — ThreadPoolExecutor, state machine, job_status broadcast | `openprogram/agent/job/runner.py` |
| Landing slot for `Inject` | memory prefetch into the system prompt plus steering messages | `openprogram/agent/agent_loop.py` |
| Event causality, rewind, branching | the session git DAG, whose nodes carry parent_id / caller | `openprogram/context/git/` |
| The gate's hard enforcement point | the single point every chat tool call passes through | `agent_loop.py` `_execute_tool_calls` |

## 3. Event taps

Events are emitted from the places that already detect the underlying fact.
Most taps convert an existing callback or internal event into a canonical event
rather than adding new detection:

| Event | Source | Nature of the tap |
|---|---|---|
| `user.prompt_submitted` | `dispatcher/__init__.py`, at user-message persistence | added alongside the existing chat_ack / chat_response broadcast, outside the persistence branch so both paths emit |
| `model.response_started` | `agent_loop.py`, AgentEventMessageStart | conversion of an existing event |
| `model.response_completed` | `agent_loop.py`, AgentEventMessageEnd | conversion of an existing event |
| `tool.before` | `agent_loop.py`, before every `tool.execute()` | one event feeds both the notify emit and the gate query |
| `tool.after` | `agent_loop.py`, after every tool call finishes | notify emit with the result text channel |
| `subagent.started` / `completed` | `task/runner.py`, the job_status broadcast | conversion, funnelled through `_broadcast_job_status` |
| `permission.requested` | `_approval.py`, the approval_request envelope | added tap |
| `artifact.file.changed` | `file_backup.backup_before_edit` and `project_commit` | new emission after a successful write |

## 4. Gate mounting and honest coverage

The gate mounts at two places with genuinely different guarantees, and the
difference is stated rather than papered over.

**Chat path — hard enforcement.** The gate is chained into
`_execute_tool_calls` in `agent_loop.py`, ahead of `tool.execute`. Every chat
tool call passes through this one point, so nothing routes around the gate.

**Agentic nested path — optional mount.** `_pre_invocation_hooks` in
`function.py`, where the cancel check already hangs. This is a mount point, not
a chokepoint; its coverage is declared for what it is and no claim of total
coverage is made for nested calls.

The gate takes effect on subagent turns **independently of `permission_mode`**.
In particular it is not disabled by the `permission_mode="bypass"` set in
`sub_agent_run.py` — that bypass is an existing hole this design closes (design
`invariants.md` and `execution-model.md` §2).

## 5. Prepare execution

`Prepare` reuses `JobRunner.spawn_job` with a restricted tool allowlist that
excludes bash, write, and network tools. It runs in a separate small pool at
concurrency 1–2, is preemptible by user tasks, and yields on 429 (design
`execution-model.md` §3).

## 6. A known gap, deliberately not blocking

`@function` tool execution does not write a DAG node; only `@agentic_function`
does. The DAG tree is therefore incomplete as a causal record. If auditing had
to rely on the DAG for causal traceback, this would have to be filled first.
Instead the design records the full event set independently in `events.jsonl`,
which makes the DAG gap a known item rather than a prerequisite.

## 7. Verification approach

Every wiring change is checked the same way: `py_compile`, the relevant unit
tests, `openprogram worker restart`, a healthy `/healthz`, and a real message
sent through the web UI (frontend changes need `npm --prefix apps/web run build` first).

Event ordering is checked by running a turn that calls a tool and reading
the session's `events.jsonl` (the event log is always on). The log must
show `user.prompt_submitted → model.response_started → tool.before →
tool.after → model.response_completed` in that order, each entry
carrying session and turn in its metadata.

## Appendix: Implementation Status

The migration runs in five steps, four of them landed. The bus was enabled and
the in-agent sources connected; `file.changed` and the synchronous `tool.before`
query point followed; then the external-source bridge (a real auth bridge plus
taps in context, channels, memory, and the web UI); then the web UI was
converted from an emitter into a subscriber, with external sources emitting
`ws.frame` envelopes that the web UI forwards unchanged. Steps one through four
are pure additions except the fourth, which reroutes an existing path. The fifth
step — the `openprogram/proactive/` rule layer with its policy, blocking, and
observing behaviour — has not landed; its acceptance criterion is that proactive
touches no subsystem internals and works purely by subscription.

As built, the pieces sit here:

| Part | Location |
|---|---|
| `Event` / `make_event` / `emit_safe` / `subscribe(types=)` / `get_event_bus` / the event-log subscriber | `openprogram/events/bus.py` |
| Synchronous query point: `register_tool_gate` / `decide_tool_gate` / `ToolGateDenied` | `openprogram/events/tool_gate.py` |
| `tool.before` observe and query, `tool.after`, `model.*` taps | `openprogram/agent/agent_loop.py` |
| `user.prompt_submitted` | `openprogram/agent/dispatcher/__init__.py` |
| `subagent.started` / `ended` | `openprogram/agent/job/runner.py` `_broadcast_job_status` |
| `file.changed`, emitted after a successful write via lazy import | five sites across the write / edit / apply_patch tools |
| External-source bridge, installed idempotently at worker startup | `openprogram/events/bridges.py` + `worker/runner.py` |
| External source taps | `context/engine.py` (compaction, ×2), `channels/_conversation.py`, `memory/session_watcher.py` (×2), `webui/server.py` (skills / plugins) |
| `emit_ws_frame` passthrough envelope + `_subscribe_event_bus` forwarding | `openprogram/events/bus.py`, `webui/server.py` |
| External sources decoupled from the web UI import | `task/runner.py`, `sub_agent_run.py`, `worktree/manager.py`, `functions/watcher.py`, `channels/_broadcast.py` |
| Unit tests (30) | `tests/agent/test_event_bus.py`, `test_tool_gate.py`, `test_event_bridges.py` |

Live validation covered the full event sequence on a real turn, `file.changed`,
an end-to-end gate test, `skills.changed`, and a WebSocket probe confirming that
all four job_status states reach the frontend through the new chain. One
environment note carried forward: the worker's working directory is the home
directory, so the project skills directory resolves to `~/skills`.
