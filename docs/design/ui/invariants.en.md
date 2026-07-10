# Cross-module UI invariants

> Per-module docs describe how one module works; this document states
> what the modules must jointly guarantee. Every rule here caused a
> real bug before it was written down (source commit cited). Walk this
> list before touching any related module; when a new feature must
> break a rule, change the list first, then the code. Each invariant
> should have a pinning test under tests/unit/ — the doc tells humans,
> the test blocks regressions.

## 1. The enabled set is the single gate on model availability

`list_enabled_models()` (the spec rows in the providers config) is the
single source of truth for "which models did the user enable". EVERY
surface that shows or uses a model must obey it:

- the chat/exec chips in the top bar (the gated `GET /api/agent_settings`);
- the model picker dropdowns (`/api/models/enabled`);
- default resolution on the send path (`_resolve_session_provider_model`
  — including the agent.json agent-model path, which historically did
  NOT gate);
- the exec runtime used for function execution.

Corollary: once a model is disabled it must not appear in any picker,
must not resolve as any default, and must not be shown on any chip.
"Picker empty ⇔ top bar shows no model ⇔ send raises enable-a-model"
must hold in lockstep.

Source: chip kept showing a disabled model (2026-07-10, 66cb7f73);
ungated agent.json path kept running a disabled model (cb78dde1).

## 2. Enabled-set changes broadcast, and every consumer reacts

The settings page may live in a DIFFERENT browser tab. Model/provider
toggles and custom-provider deletion must emit
`agent_settings_changed` through the event bus (`ws.frame`); the
frontend WS handler then:

- refetches agent settings (updates the chips);
- invalidates the `["models-enabled"]` react-query cache (updates
  every dropdown).

Refreshing only the tab that performed the action is not enough. Any
new entry point that mutates the enabled set (config import, bulk ops,
CLI writes, …) must hook the same broadcast.

Source: cross-tab chip/dropdown desync (66cb7f73).

## 3. Disabling the current default CLEARS the default, not hides it

The default model lives in three places: the chat global and exec
global in `_runtime_management`, and the default agent's `model` in
agent.json. When the current default falls out of the enabled set, all
three must be cleared (`_clear_stale_defaults`); the user picks the
next model explicitly. Hiding it at display level leaves a zombie
default: it resurrects on re-enable and keeps serving conversations
while disabled.

Source: cb78dde1.

## 4. The frontend store must be able to express "cleared"

`setAgentSettings` semantics: object = replace, `null` = clear,
`undefined` = keep. A keep-previous merge (`??`) makes "this setting
is now empty" permanently unable to overwrite the stale value — any
store field whose backend can legitimately report "gone now" needs a
setter that distinguishes "field not mentioned" from "field cleared".

Source: the chip could never clear; full page reloads masked it
(66cb7f73).

## 5. Backend→frontend frames go through the event bus

External sources AND webui routes emit frames via
`emit_ws_frame({"type": ..., "data": ...})`; the single subscriber in
server.py forwards to the sockets (event-layer.md). Never call
`_s._broadcast` directly — frames that bypass the bus are invisible to
event-layer consumers (proactive, logging) and unauditable.
(`_broadcast_envelope` / `_broadcast_chat_response` are server-side
helpers with session routing logic, not raw frames — exempt.)

Source: 6d93ce4a (routes migration, the routes part of event-layer
step 4).

## 6. The three spawn entry points share one semantics

A sub-agent branch spawns via three entry points: the sync `task()`
path (functions/tools/task/task.py), the async runner
(agent/task/runner.py), and `message_branch`
(functions/tools/agent_collab/). For clean mode all three must pass
`spawn_caller=<spawning node>`, so the branch root's `caller` points
at the turn that opened it (session-dag.md §2.3) instead of hanging
off ROOT. Change spawn semantics in all three together, test all
three together.

Spawns along one chain share a single depth counter
(`message_branch`'s `MAX_SPAWN_DEPTH`); task() and message_branch both
check and increment it — otherwise a spawned agent can re-delegate
forever (observed live: a 5-generation weather-query delegation chain,
each hop just re-wording the prompt).

Source: the sync path omitted it, so DAG branches forked from the
root (1d1fe016); the async task() path dropped the caller and never
counted depth (follow-up fix).

## 7. The chat sibling switcher appears only on REAL forks

`< N/M >` sibling sets group by fork point (`predecessor`, falling
back to `caller`; ROOT normalizes to none) and contain conversation
turns only:

- tool/code sub-call rows never join (they carry no predecessor and
  would pollute the root set);
- `source=agent_spawn` branch roots never mix with the user's own
  turns (an agent-opened branch is not a pageable alternative);
- `display=runtime` cards never join (fn-run cards have their own
  fn-run-scoped nav).

Source: a fresh session's first turn showed 1/6 (1d1fe016; an earlier
incarnation showed 1/12).

## 8. Instant interaction feedback (0ms optimistic state)

Every click renders an optimistic transition immediately (pending
card, button state, switched index); real data backfills; a timeout
reverts and toasts. Long operations (function runs pay ~1s subprocess
cold start) must never feel like a dead click. Entry point:
`optimisticAction` (web/lib/runtime-bridge/optimistic-action.ts).

Source: the retry / fn-form / checkout optimistic pass (0b3b9c2e).

## 9. Display order may be adjusted; data order may not

The attach pointer node must stay at the tail of the conversation
chain (head movement depends on it), yet the chat renders the Spawned
card BEFORE the reply it fed — requirements like this are always
solved by reordering at render time (conv-mapper), never by changing
persisted order or head semantics.

Source: 1d1fe016.

## 10. SSR boundary: no window at module scope

`web/lib/runtime-bridge/*` reads `window` at module scope; a static
import from an App Router page breaks prerendering. Page/settings
components that need them use dynamic `import()` (example:
`refreshAgentChip`). New runtime-bridge modules either keep this rule
in mind or defer window access into function bodies.

Source: settings-page prerender crash during f09ed1c2.
