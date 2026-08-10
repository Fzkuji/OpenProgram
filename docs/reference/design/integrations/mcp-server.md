# MCP Server — Being Called From Outside

> This document is the design of the **server** direction: how an external
> program — Claude Desktop, another agent, an IDE — drives OpenProgram's tools
> over a stable protocol. The opposite direction, consuming tools from external
> MCP servers, is [`mcp-integration.md`](mcp-integration.md).
> Related code: `openprogram/webui/`, `openprogram/functions/_runtime.py`,
> `openprogram/agent/internals/_approval.py`.
> Rendered companion: [`mcp-server.html`](mcp-server.html).

## In one sentence

Expose a small, fixed set of OpenProgram tools over MCP stdio, authenticate
every caller, map the external caller onto a low-privilege authority tier
that routes tool calls through the same approval ladder a local turn uses, and
align cancellation, progress and errors with the MCP specification — so a
protocol client gets a contract it can build on, not a snapshot of an internal
route table.

## Layer 1 — How we work today

### There is no server, only a client

`openprogram/mcp/` is client-only: eight modules (`client.py`, `adapter.py`,
`config.py`, `registry.py`, `oauth_flow.py`, `sampling.py`, `token_storage.py`)
that spawn external MCP servers and mount their tools into the registry. No
`FastMCP`, no `mcp.server`, no `stdio_server`, no request handler exists in the
tree. Nothing in OpenProgram can currently be called by an external MCP client.

### What external control surface does exist

Three surfaces, all local HTTP, none versioned:

| Surface | Where | Shape |
|---|---|---|
| FastAPI routes | `create_app()` in `openprogram/webui/server.py`, route modules under `openprogram/webui/routes/` | 198 routes across ~25 modules |
| WebSocket `/ws` | `_websocket_handler` in `openprogram/webui/server.py`, actions from `openprogram/webui/ws_actions/` | ~100 client→server actions in the `WS_ACTIONS` dispatch dict, ~120 server→client event types |
| Tool registry | `_registry` in `openprogram/functions/_runtime.py` | `register()` / `get()` / `filter_for()`, keyed by tool name |

`AgentTool` (`openprogram/agent/types.py`) carries `name`, `description`,
`parameters`, `label` and:

```python
execute: Callable[
    [str, dict[str, Any], asyncio.Event | None, AgentToolUpdateCallback | None],
    Awaitable[AgentToolResult],
]
```

That signature already has the three things a protocol server needs: an
argument dict, a cancellation `Event`, and an incremental-update callback. The
missing piece is a transport that speaks them to a foreign process.

### Web authentication exists; MCP caller identity does not

`start_server()` binds `web.host` (default `127.0.0.1`) and installs
`OwnerAuthMiddleware` from `openprogram/webui/owner_auth.py` before route
dispatch. The active Web process owns a per-start token. Browsers exchange a
fragment token for a profile-specific HttpOnly cookie; native HTTP, SSE, and
WebSocket clients use `Authorization: Bearer`. Canonical Host and effective
Origin checks apply before authentication, a non-loopback bind without an
explicit origin fails startup, and WebSocket authentication occurs before
`accept()`. Successful requests receive the current profile's full owner
authority.

That boundary changes the security premise but does not implement this MCP
design:

1. The internal FastAPI and WebSocket surfaces are protected, but remain
   unversioned, full-owner application interfaces rather than an external
   integration contract.
2. An MCP client must not receive the Web owner token. It needs its own client
   identity, default-empty tool whitelist, and low-privilege authority mapping;
   none of those MCP-server components exists yet.

On that surface sits `POST /api/register`, which imports an arbitrary module
path taken from the request body. Stored provider credentials are no longer
retrievable in plaintext: both reveal forms are gone, so the account and
config-key routes return masks only, and the MCP management routes mask every
stored `env` value, header value, bearer token, and OAuth client secret the
same way. Web owner authentication limits who can
reach these internal routes, but it does not make their shapes suitable for
external callers or make a full-owner credential suitable for an MCP client.
MCP authentication and low-privilege authorization therefore remain
preconditions rather than later hardening.

### Approval already exists and is the right hook

`wrap_with_approval(agent_tool, req, on_event)` in
`openprogram/agent/internals/_approval.py` returns a copy of an `AgentTool`
whose `execute` is `_gated_execute`. The ladder runs in fixed order:

1. `_hard_constraint_violation()` — evaluated before rules and before bypass,
   so nothing can remove it. Currently scoped to `req.source == "agent_spawn"`.
2. Rule-layer `deny` / `ask` from `req.permission_rules`, priority
   `deny > ask > allow`.
3. `_FORCE_APPROVAL_TOOLS` — always asks, even under bypass.
4. `mode == "bypass"` short-circuit.
5. Rule-layer `allow`.
6. `SAFE_AUTO_ALLOWLIST` — read-only tools pass in every mode.
7. `acceptEdits` + `_accept_edits_safe` + `_path_is_safe()`.
8. `mode == "auto"` → `RISKY_AUTO_DENYLIST`, else `auto_classify_tool()`.
9. Otherwise a blocking approval card.

`PermissionMode` is `ask` / `bypass` / `acceptEdits` / `auto` / `plan`, default
`ask`, but web chat defaults to `bypass` and spawned sub-agents are hardcoded
`permission_mode="bypass"` in `openprogram/agent/sub_agent_run.py`.
`await_user_approval()` opens a `QuestionRegistry` entry with `kind="approval"`
and emits `question.asked` carrying `tool`, `args` and
`risk_level ∈ {low, medium, high}`; it returns
`(approved, reason, scope ∈ {once, always})`, and `always` persists an allow
rule into `<project>/.openprogram/settings.json`.

Authority is a **two-tier enum**, `owner` / `paired`, carried on the request
boundary as `authority_tier` and distinct from `principal_id`. The gate maps the
tier onto a fixed capability set through the `TIER_CAPABILITIES` constant in
`openprogram/agent/authority.py`; a request never carries a capability list of
its own, so there is nothing a caller can mint. The execution rule is
`allow = hard_constraints ∧ TIER_CAPABILITIES[tier] ∋ capability ∧ permission_or_exact_owner_approval ∧ enforcement_boundary`,
and it is fail-closed: a missing or unrecognized tier denies every capability
rather than falling back to a reduced set. This design consumes that gate; see
[`authority-handoff.md`](../memory/authority-handoff.md) for the tier model.

### Cancellation

`openprogram/agent/run_control.py` models cancellation as a **per-turn
`CancelToken`, never per-session** — a `threading.Event` plus a `retired` flag,
so a stop arriving after the turn ended cannot leak into the next turn.
`begin_turn` / `end_turn` / `register_cancel_event` / `mark_cancelled` /
`kill_active_runtime` make up the surface, and `_cancel_hook` is installed
globally at import time via `add_pre_invocation_hook` and
`set_cancellation_check`, so every `@agentic_function` entry and every
`Runtime.exec` aborts.

The two existing entry points disagree. WS `stop` (`ws_actions/runtime.py`) is
two-stage: a graceful request, then a hard kill after `_GRACEFUL_GRACE_S = 4.0`,
and it cancels pending questions via `cancel_session`. HTTP `POST /api/stop`
(`routes/lifecycle.py`) is single-stage and does not touch the question
registry. Channel-worker turns register no token at all and are invisible to
both, a gap flagged in `run_control.py` itself.

### Error semantics

Two representations that share no field. `ToolResult`
(`openprogram/functions/_runtime.py`) has a first-class `is_error: bool`.
`AgentToolResult` (`openprogram/agent/types.py`) has only `content` and
`details`, and `_normalize_result()` moves the flag into `details["is_error"]`.
Every consumer therefore writes `details.get("is_error")` defensively, and
nothing checks it statically. Concrete payloads: denial is
`{"is_error": True, "denied": True}`, timeout is
`{"is_error": True, "timeout": True}`. At the event layer
`AgentEventToolEnd` does carry a typed `is_error`.

### Contract tests

`tests/unit/test_diagnostic_mcp_route_contracts.py` is the only file pinning
response shapes, and it covers two routes out of 198:

- `test_doctor_route_contract` — `GET /api/doctor` returns exactly
  `{"results": [...], "all_ok": bool}`.
- `test_mcp_list_route_contract` — `GET /api/mcp/servers` returns exactly
  `{"servers": [status]}` with a 15-key status dict.
- `test_mcp_detail_route_contract_and_missing_status` — the detail shape, and
  a missing server returning 404 `{"detail": "server '<name>' not loaded"}`.

Each test builds a bare `FastAPI()` and calls the module's `register(app)`, so
`OwnerAuthMiddleware` is not in the path. The assertions use exact equality, which
makes them brittle to additive change — an accurate description of the current
state, since these are contracts by construction rather than by policy.

## Layer 2 — How the reference frameworks do it

Surveyed: every directory under `references/`.

| Framework | MCP server | ACP (agent side) | Inbound HTTP / webhook | Client SDK |
|---|---|---|---|---|
| claude-code | — (snapshot is `BashTool/` only) | — | — | — |
| claude-code-leaked | `claude mcp serve`, stdio, all built-in tools | no | `claude server`, bearer token | Agent SDK over the control protocol |
| codex-cli | `codex mcp-server`, stdio, two tools | no (own app-server instead) | app-server over stdio / WS / unix socket | `@openai/codex-sdk` (TS) + `openai-codex` (Python) |
| openclaw | three stdio servers plus MCP-over-HTTP | `openclaw acp`, Gateway-backed | Gateway HTTP APIs + HMAC webhooks plugin | `@openclaw/sdk` (private) |
| opencode | no — client only | `opencode acp` | `opencode serve`, HTTP Basic | `@opencode-ai/sdk` |
| hermes-agent | `hermes mcp serve`, FastMCP stdio, ten tools | `hermes acp` + ACP registry entry | HMAC webhook adapter + bearer OpenAI-compatible API | no |
| pi-ai | no | no | no | not applicable — it is a provider client library |
| pi-mono | no, explicitly ("**No MCP.**") | no | no | embed-only packages |
| weclaw | no | ACP **client** only, auto-allows every permission | `POST /api/send`, no auth | no |

Five points carry over into our design.

**Tool surface is deliberately small, and the small ones age better.** codex
exposes exactly two tools, `codex` and `codex-reply` — start a turn, continue a
thread. hermes exposes ten, all read-or-message operations over its own session
store. openclaw's channel bridge exposes nine of the same shape. Only
claude-code-leaked exposes the whole built-in tool set, and it does so with
`getEmptyToolPermissionContext()` for both list and call — no user permission
rules loaded, `isNonInteractiveSession: true`, so there is no interactive
prompt path at all. That is the configuration to avoid, not to copy.

**Approval over the protocol is a solved shape, twice.** codex pushes exec and
patch approvals back to the MCP client with `elicitation/create`, carrying its
own correlation fields (`threadId`, `codex_elicitation`,
`codex_mcp_tool_call_id`, `codex_event_id`) and receiving a `ReviewDecision`;
its source notes the payload does not yet conform to `ElicitResult`. openclaw
takes the polling route instead: `permissions_list_open` and
`permissions_respond` with `allow-once | allow-always | deny`, so a generic MCP
client with no elicitation support still works. hermes mirrors that pair.
Polling degrades gracefully; elicitation is lower-latency and depends on client
support.

**Cancellation is handled where it is handled at all.** codex implements
`notifications/cancelled` end to end — it looks the turn up in an active-turn
registry and stops routing events to it, and answers `tasks/cancel` as
explicitly unsupported. openclaw's plugin-tools server forwards the SDK's
`extra.signal` into `handlers.callTool(params, signal)`. claude-code-leaked
creates a fresh `AbortController` per call and never wires it to the
notification. hermes has no cancellation.

**Progress notifications are largely unused.** codex receives
`notifications/progress` and only logs it; its own streaming is a custom
notification shape carrying Codex event payloads. Nobody surveyed emits
standard `notifications/progress` outward.

**Errors converge on one shape.** Every implementation returns
`{isError: true, content: [{type: "text", ...}]}` for a tool that ran and
failed, and reserves JSON-RPC errors for protocol-level faults — unknown tool
name, malformed arguments. That split is the one we adopt.

Two cautionary data points from the same survey: weclaw's `/api/send` has no
authentication of any kind and its ACP client auto-allows every
`session/request_permission`; opencode's `serve` runs unauthenticated when
`OPENCODE_SERVER_PASSWORD` is unset and only prints a warning. Both are the
default-off posture that the acceptance bar for this work rules out.

## Layer 3 — The design

### Transport and entry point

`openprogram mcp serve` runs an MCP server over **stdio only**. Stdio is the
transport every surveyed implementation ships first, it inherits the trust
boundary of whoever spawned the process, and it does not expose the internal
full-owner Web API as an integration surface. HTTP transport waits until the
stdio contract is stable and a separate deployment design exists.

The server lives in `openprogram/mcp_server/`, a top-level module beside
`openprogram/mcp/`. It does not reuse the client modules — `client.py` and
`adapter.py` translate in the opposite direction — but it does share
`openprogram/functions/_runtime.py` and the approval ladder, because sharing
those is the entire point.

### The minimum tool set

Six tools, chosen so that each maps onto one capability and none is a generic
escape hatch:

| Tool | Does | Capability |
|---|---|---|
| `sessions_list` | List sessions with id, title, updated-at | `reply` |
| `session_get` | Fetch one session's messages | `reply` |
| `prompt_send` | Start a turn in a session and return its result | `reply` |
| `prompt_cancel` | Cancel an in-flight turn started by this caller | `reply` |
| `tools_list` | List the tools the caller's tier permits, with schemas | `reply` |
| `tool_call` | Invoke one named tool with arguments | per-tool |

`prompt_send` is the codex `codex`/`codex-reply` shape collapsed into one call
with an optional session id. `tool_call` is the one that needs the whitelist
below; the other five are read-or-converse operations that produce no host side
effect beyond appending to a session.

There is no `register`-equivalent, no config mutation, no credential access, and
no filesystem tool exposed directly — a caller that wants to read a file asks
for `tool_call` with a file tool name, which goes through the whitelist and the
approval ladder like any other.

### Tool exposure whitelist

`tool_call` resolves against an explicit allowlist, not against `_registry`
keys. The config key `mcp_server.exposed_tools` holds a list of tool names; the
default is empty, so a fresh install exposes no tools through `tool_call` until
the owner names them. `tools_list` returns the intersection of the allowlist
with what the caller's tier permits, so a client never sees a tool it cannot
call.

The whitelist is a filter placed **before** the approval ladder, not a
replacement for it. Naming a tool in `mcp_server.exposed_tools` makes it
reachable; whether a given call runs is still the ladder's decision.

### Authority tier for external callers

An MCP client is a request source in exactly the sense
[`sandbox-architecture.html`](../runtime/sandbox-architecture.html) defines, and
it takes its place in that table as the lowest-privilege entry:

| Request source | principal | `authority_tier` | Missing-field handling |
|---|---|---|---|
| Authenticated local Web / CLI / TUI | `principal_id=owner`, `interaction=interactive` | `owner`, which holds `approval.request` | The entry point must construct it; invalid auth denies |
| Paired channel account | Instance owner, speaker stored separately | `paired`, which holds `reply` and `memory.source.append` | A message with no tier is denied, never downgraded |
| **External MCP client** | **Instance owner, client id stored separately** | **`paired`, intersected with the exposed-tool whitelist** | **Absent or unverified client identity denies the connection** |
| continuation / subagent | Explicitly inherited owner | The caller's tier, inherited unchanged and never widened | Missing field is a state error, deny |
| cron trigger | Owner approved at creation | The job capability frozen at approval | Never recomputed as interactive |

`runtime_authority()` implements the subagent row: it copies the parent's
normalized authority, rewrites only the speaker fields and `interaction`, and
leaves `authority_tier` untouched. A spawned child therefore holds exactly its
parent's tier — a `paired` turn cannot spawn an `owner` subagent — and a parent
with no valid authority yields `{}`, which the gate denies.

Three consequences follow from the MCP row.

`interaction` is **never** `interactive` for an MCP caller. It therefore never
holds `approval.request`, so it cannot request a one-shot capability escalation.
A tool call needing approval that has no local owner watching resolves as a
denial, the same way a cron trigger does — it does not silently pass, and it
does not block forever.

`permission_mode` for an MCP-sourced turn is fixed at `ask` and is not
configurable per request. The `bypass` default that web chat uses is a local
interactive affordance and does not extend across the protocol boundary.

Hard constraints run first, as always, and this design adds the external source
to `_hard_constraint_violation()` alongside `agent_spawn`: writes and patches
outside the session's working directories, and the `_RISKY_TOOLS` set, are
refused regardless of whitelist, rule or approval.

### Authentication, on by default

The server reads a token from `<state_dir>/mcp_server_token`, generated on
first `openprogram mcp serve` with `0600` permissions and printed once. A
client presents it in the `initialize` request's `clientInfo`; a mismatch or an
absent token fails `initialize` before any tool is listed. Comparison is
constant-time.

There is no flag that disables authentication. A caller that cannot present the
token gets no tool list and no tool call. This mirrors codex's token-file mode
and claude-code-leaked's auto-generated `sk-ant-cc-*` — the token exists whether
or not the operator thought about it — and it deliberately does not mirror
opencode's warn-and-continue or weclaw's no-auth endpoint.

The token identifies a client, and the client id is recorded on every request
for audit. It does not identify the *owner*: presenting it grants the `paired`
tier above, not owner authority.

### Cancellation

`notifications/cancelled` maps onto the existing per-turn `CancelToken`. The
server keeps a map from MCP request id to the `session_id` and token of the
turn it started; on the notification it calls the same two-stage path WS `stop`
uses — graceful request, hard kill after the grace period — and it cancels
pending questions through `cancel_session`, which is the behaviour HTTP
`/api/stop` currently lacks. A notification whose request id is unknown, or
whose turn already retired, is a no-op, which the `retired` flag already gives
for free.

`tool_call` passes the token's `asyncio.Event` straight into the `AgentTool`
`execute` signature's cancel parameter. That parameter already exists and is
already honoured; the server only has to supply it.

### Progress

The `on_update` callback in the `execute` signature becomes
`notifications/progress` against the originating request's `progressToken`, sent
only when the client supplied one. `prompt_send` reports turn-level progress —
tool started, tool finished — rather than token-level streaming, which keeps the
notification volume proportional to work done and does not require the client to
reassemble a stream.

Where a client sends no `progressToken`, the callback is dropped and the call
returns its result normally. Progress is an optimization, never a correctness
requirement.

### Errors

The split the reference implementations converge on:

| Condition | Response |
|---|---|
| Unknown tool name | JSON-RPC error, method-level |
| Arguments fail schema validation | JSON-RPC error, invalid params |
| Token missing or wrong | `initialize` fails, no session established |
| Tool not in `mcp_server.exposed_tools` | JSON-RPC error, treated as unknown tool — the caller learns nothing about tools it may not use |
| Tier lacks the capability | `isError: true`, text names the missing capability |
| Approval denied or unavailable | `isError: true`, text carries the denial reason |
| Tool ran and failed | `isError: true`, text is the tool's error content |
| Turn cancelled | `isError: true`, text states cancellation |

Mapping `AgentToolResult` onto that table needs `details.get("is_error")`, which
is why this design promotes `is_error` to a typed field on `AgentToolResult`
rather than reading an untyped dict key across a protocol boundary. The field
already exists on `ToolResult` and on `AgentEventToolEnd`; making it three for
three removes the defensive `.get` at every call site.

### Contract tests

The acceptance bar is an end-to-end test from a real external client, not a
route-shape assertion. `tests/integration/test_mcp_server.py` spawns
`openprogram mcp serve` as a subprocess, connects with the MCP SDK's own client,
and asserts: `initialize` fails without the token and succeeds with it;
`tools/list` returns exactly the whitelisted intersection; a whitelisted
read-only `tool_call` returns content; a non-whitelisted `tool_call` returns a
method error; a call needing approval returns `isError` rather than hanging;
`notifications/cancelled` stops an in-flight `prompt_send`; and progress
notifications arrive when and only when a `progressToken` was supplied.

Using the SDK client rather than hand-built JSON-RPC frames is what makes this a
protocol test — it fails if we drift from the specification, which a
self-consistent frame comparison would not catch.

## Layer 4 — The ideal state, and the distance to it

### ACP

ACP would let Zed and other editors drive OpenProgram as their agent. Five of
the surveyed frameworks ship it — openclaw, opencode, hermes on the agent side,
weclaw on the client side, and it is what `codex --experimental-acp`-style flags
address elsewhere. The permission mapping is uniform across all of them:
`allow_once` / `allow_always` / `reject_once` option ids returned from
`session/request_permission`.

The gap is not the protocol, it is the session model. ACP expects
`newSession` / `loadSession` / `prompt` / `cancel` / `setSessionMode` over one
stdio connection with the editor owning the filesystem — `fs/read_text_file`
and `fs/write_text_file` are client-side capabilities. Our sessions are DAG-
structured with branches, worktrees and per-session working directories, and our
file tools read the host directly. openclaw's own documentation lists
`loadSession` as partial, and per-session `mcpServers`, `fs/*` and `terminal/*`
as unsupported — a full implementation is a substantial mapping exercise.

We do not do it now because the same session-mapping work benefits MCP first,
and because MCP settles the authority-tier and approval questions that ACP
would otherwise have to answer independently. ACP becomes worth evaluating once
`prompt_send` and `prompt_cancel` have a proven session mapping.

### Inbound webhooks

hermes and openclaw both ship them, and hermes' version shows what correctness
costs: per-route HMAC secrets validated at startup, per-route rate limiting, an
idempotency cache against retry duplicates, and body-size limits checked before
reading. That is the floor, not a nice-to-have — a webhook is an unauthenticated
inbound trigger by default.

The gap is that a webhook is a *push* trigger arriving with no owner present,
which puts it in the same category as a cron trigger: it needs a frozen job
capability approved at registration time, not a tier computed at delivery.
Batch I of the sandbox plan and step 05B (cron creation and management) build
exactly that machinery. Doing webhooks before it means either inventing a
parallel mechanism or accepting an unattended trigger with an interactive-shaped
scope.

We do not do it now because the prerequisite is scheduled work on another track,
and because MCP covers the pull-shaped integrations — an external program that
wants to call us — which is the demand we can actually name.

### Client SDK

codex ships TS and Python, opencode ships TS generated from a checked-in OpenAPI
spec, openclaw ships a private TS package, claude-code-leaked ships the Agent
SDK over its control protocol. hermes ships none and tells integrators to use
MCP, ACP or the HTTP API directly.

The gap is that an SDK is a compatibility promise, and we have nothing stable to
promise. 198 FastAPI routes and ~100 WebSocket actions are internal surfaces
that change with the frontend; two of them have pinned shapes. Publishing an
SDK over that surface converts every internal refactor into a breaking change
for external callers.

We do not do it now, and the ordering is deliberate: MCP first, because the MCP
SDK *is* the client library for every language that has one, which is why hermes
can ship zero SDKs and still be integrable. An OpenProgram-specific SDK only
earns its place if the MCP tool surface proves too coarse for a real integrator,
and that is evidence we do not have yet.

### Where the surface stabilizes

The through-line across all three deferrals is that a protocol is only worth
publishing over a surface whose shape we are willing to keep. The MCP tool set
above is six tools chosen to be small enough to keep. The FastAPI and WebSocket
surfaces are not, and this design does not propose making them so — it proposes
routing external callers away from them.

## Appendix: Implementation Status

Nothing in Layer 3 is implemented. The current state is Layer 1: an MCP client
with no server counterpart, an owner-authenticated but internal and unversioned
Web surface, and an approval ladder that works for local turns.

| Item | Status | Blocking condition |
|---|---|---|
| `openprogram mcp serve` over stdio | Not implemented | — |
| Six-tool minimum set | Not implemented | — |
| `mcp_server.exposed_tools` whitelist, default empty | Not implemented | — |
| Token authentication, no disable flag | Not implemented | — |
| External-caller `authority_tier` row | Not implemented | The tier gate itself exists (`openprogram/agent/authority.py`); what is missing is an MCP entry point that constructs a request carrying it |
| External source in `_hard_constraint_violation()` | Not implemented | Independent of batch I; can land with the server |
| `notifications/cancelled` → `CancelToken` | Not implemented | Uses `run_control.py` as-is |
| `notifications/progress` from `on_update` | Not implemented | Uses the existing `execute` signature as-is |
| Typed `is_error` on `AgentToolResult` | Not implemented | Touches every `details.get("is_error")` call site |
| `tests/integration/test_mcp_server.py` end-to-end | Not implemented | — |
| ACP | Not planned this round | Session mapping proven by `prompt_send` / `prompt_cancel` first |
| Inbound webhooks | Not planned this round | Frozen job capability from sandbox batch I and step 05B |
| Client SDK | Not planned this round | A tool surface stable enough to promise |

The current Web boundary is a premise of this design rather than an MCP-server
item: `OwnerAuthMiddleware` authenticates the singleton owner and protects
HTTP, SSE, and WebSocket, as specified in
[`remote-web-access.md`](../ui/remote-web-access.md). It does not authenticate
an external MCP client, restrict that client to a tool whitelist, or assign the
low-privilege scope defined here. Adding an MCP server must implement those
separate controls and must not reuse the Web owner token.
