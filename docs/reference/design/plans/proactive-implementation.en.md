# Implementation Plan: Wiring the Proactive Layer into the Existing Code

> See [`../design/proactive/`](../proactive/README.md) for the design. This document only covers **how to wire that
> design into the existing OpenProgram code**: wiring points (file:line), which existing mechanisms to reuse, the phasing, and how to verify.
> The "what/why" of the design is not here; if there is a conflict, the design docs take precedence.

Code landing spots: upgrade the event layer in place at `openprogram/agent/event_bus.py` (Event + type-based subscription + process-level singleton),
with taps added in the individual source files; the `openprogram/proactive/` package is only created at the "new consumer enters" step (the rule layer).
Event model = the three core fields + an open metadata pocket (see design `event-layer.md` §1; turn/session are not fixed fields).

## Existing mechanisms to reuse

The "existing reusable parts" the design repeatedly mentions, with their real locations:

| Role in the design | Existing mechanism | Location |
|---|---|---|
| In-process event fan-out | `EventBus` (already implemented but idle; dispatcher/agent_loop bypass it with direct callbacks) | `openprogram/agent/event_bus.py:14-60` |
| The gate's `ask` path | `ApprovalRegistry` + `_wrap_with_approval` (request → block and wait → approve/deny; deny returns an is_error tool result) | `openprogram/agent/_approval.py:77-174` |
| The observer's `Prepare` background task | `TaskRunner.spawn_task` (ThreadPoolExecutor, state machine, task_status broadcast) | `openprogram/agent/task/runner.py` |
| The landing slot for `Inject` | memory prefetch injected into the system prompt + steering messages | `openprogram/agent/agent_loop.py:343-354` |
| Event causality / rewind / branching | session git DAG (nodes carry parent_id / caller) | `openprogram/contextgit/` |
| The gate's hard enforcement point | the single point through which all chat tool calls pass | `openprogram/agent/agent_loop.py` `_execute_tool_calls` |

## Event tap wiring points

Emit the design's Event from these locations in the existing code (most of these convert an existing callback/event into a CanonicalEvent):

| Event | Wiring point | Current state |
|---|---|---|
| `user.prompt_submitted` | `dispatcher/__init__.py` phase 2 (persist user message) | chat_ack/chat_response broadcast already exists; add a tap |
| `model.response_started` | `agent_loop.py:429` (AgentEventMessageStart) | event already exists; convert to CanonicalEvent |
| `model.response_completed` | `agent_loop.py:452` (AgentEventMessageEnd) | same as above |
| `tool.before` | `agent_loop.py:495` (existing no-op `dispatch_hook(TOOL_BEFORE_USE)`) | upgrade the observe-only hook into a PRL gate tap |
| `tool.after` | `agent_loop.py:564` (`dispatch_hook(TOOL_AFTER_USE)`) | same as above |
| `subagent.started/completed` | `task/runner.py:96-113` (task_status broadcast) | convert to CanonicalEvent |
| `permission.requested` | `_approval.py` (approval_request envelope) | add a tap |
| `artifact.file.changed` | `file_backup.backup_before_edit` + `project_commit` | add a new emit |

## gate integration points

- **chat path (hard)**: `_execute_tool_calls` in `agent_loop.py`; the gate is chained in before `tool.execute`.
  All chat tools pass through this point, so it is hard enforcement.
- **agentic nested path**: `_pre_invocation_hooks` in `function.py:50-89` (the cancel check is already mounted at this
  point). This is an optional mount point; declare its coverage truthfully and don't pretend it covers everything.
- The gate takes effect on subagent turns, **independently of `permission_mode`**, and is not turned off by the
  `permission_mode="bypass"` at `sub_agent_run.py:88` (this plugs an existing hole; see design `invariants.md` and `execution-model.md` §2).

## Prepare integration

Reuse `TaskRunner.spawn_task`, but inject a restricted tool allowlist (no bash/write/network). A separate small pool
with concurrency 1-2, preemptible by user tasks, yielding on 429 (see design `execution-model.md` §3).

## Holes to fix along the way

`@function` tool execution currently **does not write a DAG node** (only `@agentic_function` does), so the DAG tree is incomplete.
If auditing needs to rely on the DAG for causal traceback, this must be filled first; this design instead records the full set independently in `events.jsonl`, listing the DAG hole as a known item that
does not block the proactive rollout.

## Phasing (corresponds to the five-step migration in `framework-evolution.md` §4)

| Step | Content | Nature | Status |
|---|---|---|---|
| **1** | Enable the bus + integrate class-A sources | pure addition | ✅ Landed 2026-06-13 (commits e06b2db6 / 2915849b / 9b15ccac / dd8cb843); a real turn validated the full sequence |
| **2** | `file.changed` + the `tool.before` synchronous query point | pure addition | ✅ Landed 2026-06-13 (commit 89e16a10); file.changed live-verified, gate end-to-end tested |
| **3** | Class-B source bridging: real auth bridge + context/channels/memory/webui source taps | pure addition | ✅ Landed 2026-06-13 (commit 5cc967df); skills.changed live-verified, 5 unit tests for the auth bridge. Note: worker cwd=home, so the project skills directory is ~/skills |
| **4** | Switch webui to a subscriber: external sources emit `ws.frame`, webui subscribes and broadcasts as-is | reroute existing path | ✅ Landed 2026-06-13 (commits 99678165 + 4c5a5ede); WS probe verified that task_status's four states reach the frontend through the new chain |
| **5** | New consumer enters: the `openprogram/proactive/` rule layer (Policy / blocking / observing) | pure addition | ⏳ Acceptance: proactive does not touch subsystem internals and works purely via subscription |

## Implementation already landed (steps 1–2 as-built)

| Part | Location |
|---|---|
| Event / make_event / emit_safe / subscribe(types=) / get_event_bus / event-log subscriber | `openprogram/agent/event_bus.py` |
| Synchronous query point (register_tool_gate / decide_tool_gate / ToolGateDenied) | `openprogram/agent/tool_gate.py` |
| tool.before observe+query, tool.after, model.\*, turn.ended taps | `openprogram/agent/agent_loop.py` |
| user.prompt_submitted (emitted on both paths, outside the persistence branch) | `openprogram/agent/dispatcher/__init__.py` |
| subagent.started/ended (status funnel) | `openprogram/agent/task/runner.py` `_broadcast_task_status` |
| file.changed (after a successful write, lazy import) | five spots across the write / edit / apply_patch tools |
| Class-B bridge (auth subscribe-and-translate, idempotently installed at worker startup) | `openprogram/agent/event_bridges.py` + `worker/runner.py` |
| Class-B source taps | `context/engine.py` (compaction ×2), `channels/_conversation.py`, `memory/session_watcher.py` (×2), `webui/server.py` (skills/plugins) |
| Step-4 passthrough-envelope emit_ws_frame + webui `_subscribe_event_bus` subscribe-and-forward | `agent/event_bus.py`, `webui/server.py` |
| Step-4 external-source decoupling (no longer imports webui) | `task/runner.py`, `sub_agent_run.py`, `worktree/manager.py`, `functions/watcher.py`, `channels/_broadcast.py` |
| Unit tests (30) | `tests/agent/test_event_bus.py`, `test_tool_gate.py`, `test_event_bridges.py` |

## Verification

Common to every step: `py_compile` + the relevant unit tests + `openprogram worker restart` + `/healthz` healthy +
send a real message through webui (frontend changes need `cd web && npm run build`).
Step 1 specific: restart the worker with `OPENPROGRAM_EVENT_LOG=1`, run a turn with a tool call, and confirm that the log
shows, in order, `user.prompt_submitted → model.response_started → tool.before → tool.after →
model.response_completed → turn.ended`, with metadata carrying session/turn.
