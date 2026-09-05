# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:test-driven-development for every task and superpowers:verification-before-completion before reporting completion.

**Goal:** Add an authenticated, stdio-only `openprogram mcp serve` endpoint that exposes exactly six fixed MCP tools and maps them onto existing sessions, Runtime tools, approval, cancellation, progress, and audit mechanisms without granting owner authority.

**Architecture:** Add a sibling `openprogram/mcp/server/` package, separate from the existing MCP client package and Web owner control plane. The entrypoint authenticates before opening stdin, creates a credential-derived client identity with fixed `paired` authority and `non-interactive` interaction, and runs the locked MCP 1.29.0 SDK server at protocol baseline 2025-11-25. Six protocol-facing tools delegate to a service layer; underlying Runtime tool exposure is the intersection of the default-empty configuration allowlist, the live registry, and the fixed paired capability table. SDK cancellation owns the JSON-RPC cancellation error; OpenProgram only stops subsequent side effects, clears request/question state, and emits audit evidence.

**Tech Stack:** Python 3.11+, `mcp==1.29.0`, MCP 2025-11-25, AnyIO, Pydantic, jsonschema, pytest, stdio JSON-RPC, existing SessionDB/dispatcher/Runtime/event bus.

## Global constraints

- Implement from `origin/main@9fb70113fd31dc2acf80a341e5ac51d244e1c35a` in an isolated worktree. Do not modify a dirty main checkout.
- The only server transport is stdio through `openprogram mcp serve`. Do not add HTTP, SSE, Streamable HTTP, OAuth, Web routes, sockets, or deployment listeners.
- Lock behavior to the repository's `mcp==1.29.0` and protocol revision `2025-11-25`; do not add custom handshake fields or expand the dependency range.
- Expose exactly these MCP tool names: `sessions_list`, `session_get`, `prompt_send`, `prompt_cancel`, `tools_list`, `tool_call`.
- Use an independent credential-derived MCP client identity. `initialize.clientInfo` is diagnostic metadata only and must not affect authentication, identity, scope, allowlists, or approval.
- Every request uses `source="mcp"`, `permission_mode="ask"`, `authority_tier="paired"`, and `interaction="non-interactive"`; none is caller-configurable.
- `mcp_server.exposed_tools` defaults to `[]`. Both discovery and execution of underlying Runtime tools use `configured allowlist ∩ live registry ∩ paired capability`.
- Read `<state>/mcp_server_token` with POSIX mode `0600` and compare it to `OPENPROGRAM_MCP_TOKEN` with `hmac.compare_digest()` before entering the SDK stdio context. `serve` never generates a token.
- Do not import or reuse `openprogram.webui.owner_auth`, `backend_endpoint`, Web owner tokens, cookies, or owner bearer headers.
- Unknown and unexposed underlying tool names are indistinguishable and fail with a method-level MCP error. A configured tool lacking paired scope, a hard-constraint/approval refusal, or an executed tool failure returns `CallToolResult(isError=True)`.
- A cancelled original `prompt_send` never returns an application tool result. MCP SDK 1.29.0 returns its standard JSON-RPC cancellation error; the service stops subsequent side effects, clears active requests and questions, and records `mcp.request.cancelled`.
- `prompt_cancel` is a separate ordinary tool. It may cancel only a currently active turn started by the same authenticated server connection and returns its own normal execution result.
- General questions and approvals may not wait on a non-interactive MCP request. Approval gates deny immediately; other `question.asked` events for an active MCP session are resolved as declined immediately.
- Do not change or expand the existing ACP server. Run ACP regressions when shared Runtime types change.
- Follow strict RED → verify failure → GREEN → verify pass → commit for every task. Do not combine task commits.

## Fixed protocol contract

All six schemas set `additionalProperties: false`. Empty-input tools use `{ "type": "object", "properties": {} }`.

| MCP tool | Input schema | Success payload |
|---|---|---|
| `sessions_list` | no fields | Up to 100 recent `{id,title,updated_at}` rows, in SessionDB order |
| `session_get` | required non-empty string `session_id` | Active-branch messages `{id,role,content,timestamp}` |
| `prompt_send` | required non-empty string `prompt`; optional non-empty string `session_id` | `{session_id,text,assistant_msg_id,failed}`; missing `session_id` creates an `mcp_<uuid>` session, supplied unknown id is invalid |
| `prompt_cancel` | required non-empty string `session_id` | `{session_id,cancelled}`; `cancelled` is true only for a same-connection active MCP turn |
| `tools_list` | no fields | Underlying tools in allowlist/registry/paired intersection as `{name,description,inputSchema}` |
| `tool_call` | required non-empty string `name`; optional object `arguments` defaulting to `{}` | Converted underlying `AgentToolResult` content and error state |

Successful structured application payloads are serialized as one UTF-8 JSON `TextContent`; do not add a second output representation in v1. Preserve Runtime text/image content for `tool_call`: text maps to MCP text content, image maps to MCP image content with its media type. Unsupported content fails closed as an execution error rather than being stringified.

MCP `tools/list` itself always returns the six fixed wrapper tools. The wrapper tool named `tools_list` returns the filtered underlying Runtime tools. These are distinct contracts and must have separate assertions.

---

### Task 1: Freeze the six JSON Schemas and protocol validation

**Files:**

- Create: `openprogram/mcp/server/__init__.py`
- Create: `openprogram/mcp/server/contracts.py`
- Create: `tests/unit/test_mcp_server_contracts.py`

**Interfaces:**

```python
MCP_TOOL_SCHEMAS: tuple[mcp.types.Tool, ...]
TOOL_BY_NAME: Mapping[str, mcp.types.Tool]
def validate_tool_call(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]
```

`validate_tool_call()` must raise `mcp.shared.exceptions.McpError(ErrorData(code=METHOD_NOT_FOUND, ...))` for a wrapper name outside the fixed six and `McpError(... INVALID_PARAMS ...)` for schema failures. It returns a copied dict and never mutates caller input.

**RED:** Add exact equality tests for the ordered six names, each complete input schema, `additionalProperties: false`, required fields, empty/whitespace prompt and ids, non-object arguments, unknown wrapper names, and input immutability. Also assert the lock resolves `mcp==1.29.0` and `LATEST_PROTOCOL_VERSION == "2025-11-25"`.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_contracts.py
```

Expected: FAIL because `openprogram.mcp.server.contracts` does not exist.

**GREEN:** Define the schemas as immutable module data, construct SDK `Tool` objects from them, and validate with `jsonschema.Draft202012Validator`. Normalize only `tool_call.arguments` from omitted/`None` to `{}`; reject every other undeclared or incorrectly typed field. Do not add server, transport, auth, or execution logic.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/__init__.py openprogram/mcp/server/contracts.py tests/unit/test_mcp_server_contracts.py
git commit -m "feat: define MCP server tool contracts"
```

### Task 2: Make Runtime tool errors typed end to end

**Files:**

- Modify: `openprogram/agent/types.py`
- Modify: `openprogram/programs/_runtime.py`
- Modify: `openprogram/mcp/adapter.py`
- Modify: `openprogram/agent/agent_loop.py`
- Modify: `openprogram/agent/permissions/approval.py`
- Modify: `openprogram/agentic_programming/function.py`
- Modify: `openprogram/programs/tools/files/bash/bash.py`
- Modify: `tests/unit/test_tools_runtime.py`
- Modify: `tests/agent/test_loop_options.py`
- Modify: `tests/agent/test_tool_gate.py`
- Modify: `tests/unit/test_acp_server.py`

**Interface:**

```python
class AgentToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    details: Any = None
    is_error: bool = False
```

**RED:** Change tests to assert `result.is_error` for normalized `ToolReturn` failures, timeouts, exceptions, remote MCP `isError`, approval refusals, and bash failures. Add an agent-loop assertion that `ToolResultMessage.is_error` equals `AgentToolResult.is_error`. Assert `details` retains diagnostic fields such as `denied`, `timeout`, and `reason_code` but no longer transports the canonical `is_error` bit.

Run:

```bash
uv run --locked pytest -q tests/unit/test_tools_runtime.py tests/agent/test_loop_options.py tests/agent/test_tool_gate.py tests/unit/test_acp_server.py
```

Expected: FAIL because `AgentToolResult` has no first-class error field and consumers still inspect `details`.

**GREEN:** Add the defaulted field; populate it in `_normalize_result()`, MCP client conversion, approval `_denied()`, timeout/exception paths, and explicit failing tools. Replace `details.get("is_error")` in caching and agent-loop conversion with `result.is_error`. Keep the default false so unrelated constructors remain source-compatible. Do not change ACP protocol shapes.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/agent/types.py openprogram/programs/_runtime.py openprogram/mcp/adapter.py openprogram/agent/agent_loop.py openprogram/agent/permissions/approval.py openprogram/agentic_programming/function.py openprogram/programs/tools/files/bash/bash.py tests/unit/test_tools_runtime.py tests/agent/test_loop_options.py tests/agent/test_tool_gate.py tests/unit/test_acp_server.py
git commit -m "refactor: type runtime tool error results"
```

### Task 3: Add default-empty exposure, paired MCP identity, and non-interactive gates

**Files:**

- Modify: `openprogram/config_schema.py`
- Modify: `openprogram/agent/authority.py`
- Modify: `openprogram/agent/permissions/approval.py`
- Modify: `tests/unit/test_config_schema.py`
- Modify: `tests/unit/test_authority_scope.py`
- Modify: `tests/unit/test_permission_rules.py`
- Modify: `tests/unit/test_spawn_hard_constraints.py`
- Create: `tests/unit/test_mcp_server_security.py`

**Interfaces:**

```python
def mcp_client_authority(client_id: str) -> dict[str, Any]

SettingSpec(
    key="mcp_server.exposed_tools",
    path=("mcp_server", "exposed_tools"),
    default=[],
    apply=APPLY_NEXT_START,
    ...,
)
```

The authority mapping is fixed to `speaker_kind="client"`, `speaker_id=f"mcp/{client_id}"`, `speaker_display="MCP client"`, `principal_id=owner_principal_id()`, `authority_tier="paired"`, `interaction="non-interactive"`. Reject empty or malformed client ids rather than synthesizing an identity.

**RED:** Assert a missing setting reads as `[]`; validation rejects non-list and non-string entries; normalized MCP authority is stable and has paired capabilities only. Add `source="mcp"` cases proving `_NON_INTERACTIVE_SOURCES` denies an approval immediately and `_hard_constraint_violation()` rejects `_RISKY_TOOLS`, worktree tools, and write/patch paths outside working directories before rules, allowlists, or bypass can authorize them. Assert no question is registered.

Run:

```bash
uv run --locked pytest -q tests/unit/test_config_schema.py tests/unit/test_authority_scope.py tests/unit/test_permission_rules.py tests/unit/test_spawn_hard_constraints.py tests/unit/test_mcp_server_security.py
```

Expected: FAIL because the setting, authority constructor, and MCP source gates are absent.

**GREEN:** Add the JSON-list setting validator, `mcp_client_authority()`, `"mcp"` to `_NON_INTERACTIVE_SOURCES`, and explicit MCP handling in `_hard_constraint_violation()`. Keep hard constraints before permission rules and every bypass. Do not expand `_PAIRED_CAPABILITIES`.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/config_schema.py openprogram/agent/authority.py openprogram/agent/permissions/approval.py tests/unit/test_config_schema.py tests/unit/test_authority_scope.py tests/unit/test_permission_rules.py tests/unit/test_spawn_hard_constraints.py tests/unit/test_mcp_server_security.py
git commit -m "feat: add MCP server security boundary"
```

### Task 4: Implement the independent 0600 token lifecycle

**Files:**

- Create: `openprogram/mcp/server/auth.py`
- Modify: `openprogram/_cli_cmds/mcp.py`
- Modify: `openprogram/cli.py`
- Create: `tests/unit/test_mcp_server_auth.py`
- Create: `tests/unit/test_mcp_server_cli.py`

**Interfaces:**

```python
MCP_TOKEN_ENV = "OPENPROGRAM_MCP_TOKEN"
def token_path() -> Path
def create_token(path: Path | None = None) -> str
def authenticate_from_environment(
    environ: Mapping[str, str] = os.environ,
    path: Path | None = None,
) -> str  # returns credential fingerprint/client id
def _cmd_mcp_token_create() -> int
```

The fingerprint is `sha256(stored_token.encode()).hexdigest()[:16]`; it is the stable client id and must never include token text. `create_token()` uses `secrets.token_urlsafe(32)`, refuses an existing target, writes an exclusive temporary file at `0600`, flushes and `fsync()`s, then installs it with a no-clobber operation (`os.link(temp, target)` followed by unlinking the temp on POSIX; an equivalently tested no-replace primitive on other platforms). It re-applies user-only permissions, verifies them, and returns the token for the CLI to print once. Do not use `os.replace()` after a pre-check because that can overwrite a token created by a concurrent process.

**RED:** Cover creation, length/entropy shape, existing-file refusal without overwrite, parent creation, POSIX `0600`, unreadable/wrong-mode token rejection, missing environment, mismatch, `hmac.compare_digest` use, stable fingerprint, no token in exception/log text, and `mcp token create` parser/exit behavior. Monkeypatch `_require_backend_endpoint` to fail if called and prove token creation is local.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_auth.py tests/unit/test_mcp_server_cli.py
```

Expected: FAIL because token auth and the nested `mcp token create` verb do not exist.

**GREEN:** Implement the standalone writer in `mcp/server/auth.py`; do not import Web auth helpers. Extend the existing `mcp` parser with `token create` while preserving all management verbs. Dispatch token creation directly before any backend HTTP helper.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/auth.py openprogram/_cli_cmds/mcp.py openprogram/cli.py tests/unit/test_mcp_server_auth.py tests/unit/test_mcp_server_cli.py
git commit -m "feat: add MCP server token lifecycle"
```

### Task 5: Implement session reads and filtered Runtime discovery

**Files:**

- Create: `openprogram/mcp/server/service.py`
- Create: `openprogram/mcp/server/tools.py`
- Create: `tests/unit/test_mcp_server_tools.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class MCPClientContext:
    client_id: str
    authority: Mapping[str, Any]

class MCPService:
    def sessions_list(self) -> AgentToolResult: ...
    def session_get(self, session_id: str) -> AgentToolResult: ...
    def tools_list(self) -> AgentToolResult: ...
    def exposed_runtime_tools(self) -> tuple[AgentTool, ...]: ...

def json_result(payload: Any, *, is_error: bool = False) -> AgentToolResult: ...
```

**RED:** With fake SessionDB and registry, assert `sessions_list` calls `list_sessions(limit=100)` and emits only `id/title/updated_at`; `session_get` rejects unknown ids, reads the current active branch with `get_branch(session_id)`, and emits only `id/role/content/timestamp`. Assert `tools_list` is empty by default, ignores configured-but-unregistered names, excludes registered names lacking paired capability, preserves configured order after de-duplication, and returns exact `name/description/inputSchema` keys. Assert it never consults `clientInfo` or Web auth.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_tools.py -k "sessions or session_get or tools_list"
```

Expected: FAIL because the service and conversion modules do not exist.

**GREEN:** Inject SessionDB/config/registry accessors into `MCPService` for deterministic tests. Use `functions._runtime.get()`/registry exposure and `decide_tool_authority()` for the intersection. Return deterministic JSON text. Do not implement `tool_call`, prompt execution, transport, or owner fallbacks in this task.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/service.py openprogram/mcp/server/tools.py tests/unit/test_mcp_server_tools.py
git commit -m "feat: add MCP session and tool discovery"
```

### Task 6: Implement fail-closed `tool_call` and progress mapping

**Files:**

- Modify: `openprogram/mcp/server/service.py`
- Modify: `openprogram/mcp/server/tools.py`
- Modify: `tests/unit/test_mcp_server_tools.py`

**Interfaces:**

```python
async def MCPService.tool_call(
    self,
    name: str,
    arguments: Mapping[str, Any],
    *,
    call_id: str,
    cancel_event: asyncio.Event,
    on_progress: Callable[[str], None] | None,
) -> AgentToolResult: ...

def to_mcp_content(result: AgentToolResult) -> list[mcp.types.ContentBlock]: ...
```

**RED:** Add table-driven tests for: unknown name and known-but-unexposed name returning identical `METHOD_NOT_FOUND` errors; exposed name missing paired capability returning `is_error=True` with the missing capability; hard-constraint, policy, and unavailable-approval refusals returning typed errors; invalid underlying arguments returning `INVALID_PARAMS`; success text/image conversion; underlying exception/failure conversion; no invocation on every denial. Verify execution uses a `TurnRequest` with fixed `source="mcp"`, `permission_mode="ask"`, paired authority, and no request-controlled override. Verify update callbacks preserve order and are ignored when `on_progress` is absent.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_tools.py -k tool_call
```

Expected: FAIL because `tool_call()` is absent.

**GREEN:** Resolve exposure first without leaking registry membership, validate the selected Runtime tool schema, check `decide_tool_authority()`, wrap it through `wrap_with_approval()`, and call `AgentTool.execute(call_id, arguments, cancel_event, on_update)`. Convert only supported content. Preserve `AgentToolResult.is_error`; never infer failure from free-form text.

Progress contract: the service reports ordered update messages only. The wire layer in Task 8 converts them into monotonically increasing MCP progress values and emits them only when request metadata contains `progressToken`.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/service.py openprogram/mcp/server/tools.py tests/unit/test_mcp_server_tools.py
git commit -m "feat: add scoped MCP runtime tool calls"
```

### Task 7: Implement prompt ownership, non-interactive questions, and cancellation cleanup

**Files:**

- Modify: `openprogram/mcp/server/service.py`
- Modify: `openprogram/mcp/server/tools.py`
- Create: `tests/unit/test_mcp_server_turns.py`

**Interfaces:**

```python
@dataclass
class ActiveMCPRequest:
    request_id: str
    session_id: str
    client_id: str
    thread_cancel: threading.Event
    tool_cancel: asyncio.Event

async def MCPService.prompt_send(..., request_id: str, ...) -> AgentToolResult: ...
def MCPService.prompt_cancel(session_id: str) -> AgentToolResult: ...
def MCPService.cancel_request(request_id: str, *, reason: str) -> None: ...
def MCPService.close() -> None: ...
```

**RED:** Assert: omitted session id creates `mcp_<uuid>` using agent `main` and source `mcp`; supplied existing session retains its stored agent id; supplied unknown id is `INVALID_PARAMS`; `process_user_turn()` receives the fixed request security fields and registered thread event. Assert concurrent requests have request-scoped records. `prompt_cancel` succeeds only for a same-client, same-connection active turn; unknown, completed, or foreign turns fail closed without setting their events.

Subscribe a test event bus and emit `question.asked` for active and unrelated sessions. The active MCP question must resolve `declined` immediately; unrelated questions must remain untouched. On cancellation, assert `thread_cancel.set()`, `tool_cancel.set()`, `mark_cancelled(session_id)`, `kill_active_subprocess(session_id)`, `kill_active_runtime(session_id)`, `QuestionRegistry.cancel_session(session_id)`, `unregister_cancel_event(session_id, exact_event)`, active-map removal, and `mcp.request.cancelled` audit with request/session/client fingerprint but no prompt/token. Repeat cancellation and cleanup to prove idempotence.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_turns.py
```

Expected: FAIL because prompt lifecycle and active request ownership are absent.

**GREEN:** Run synchronous `process_user_turn()` with `anyio.to_thread.run_sync(..., abandon_on_cancel=True)` and always unregister the exact cancel event in `finally`. Scope the question-bus subscription to the service lifetime and unsubscribe in `close()`. Catch AnyIO cancellation at the request boundary, call `cancel_request()`, then re-raise so SDK 1.29.0—not application code—produces the standard JSON-RPC cancellation error. Never construct `AgentToolResult` in that cancellation path. Guard completion so an abandoned worker cannot publish a later result or mutate the active request record.

Run the same command; expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/service.py openprogram/mcp/server/tools.py tests/unit/test_mcp_server_turns.py
git commit -m "feat: add MCP prompt lifecycle and cancellation"
```

### Task 8: Wire MCP 1.29.0 stdio and add end-to-end protocol tests

**Files:**

- Create: `openprogram/mcp/server/server.py`
- Modify: `openprogram/mcp/server/__init__.py`
- Modify: `openprogram/_cli_cmds/mcp.py`
- Modify: `openprogram/cli.py`
- Modify: `tests/unit/test_mcp_server_cli.py`
- Create: `tests/integration/test_mcp_server.py`

**Interfaces:**

```python
def build_server(context: MCPClientContext) -> mcp.server.Server: ...
async def serve_stdio(context: MCPClientContext) -> None: ...
def serve() -> int: ...
def _cmd_mcp_serve() -> int: ...
```

`serve()` calls `authenticate_from_environment()` and constructs `MCPClientContext` before `mcp.server.stdio.stdio_server()` is entered. Authentication errors print sanitized text to stderr, return nonzero, leave stdout empty, and never read stdin.

Use `Server.run(read, write, server.create_initialization_options())`. Register an explicit `CallToolRequest` handler in `server.request_handlers` rather than relying solely on the decorator that turns all exceptions into `isError`; wrapper-name and invalid-parameter faults must remain MCP errors. Obtain request id, `progressToken`, and session from `server.request_context`. Read `client_params.clientInfo` only for sanitized diagnostics.

**RED:** First extend CLI unit tests: `mcp serve` dispatches locally and never calls `_require_backend_endpoint`; auth happens before the stdio context. Then add SDK-client subprocess tests covering:

1. missing token file, wrong mode, missing env, and mismatched env all exit before initialize with nonzero status, empty stdout, sanitized stderr;
2. valid token completes initialize at 2025-11-25, and changing `clientInfo.name/version` does not change identity, tool list, or results;
3. MCP `tools/list` returns exactly the ordered six fixed tools and the `tools_list` wrapper returns only allowlist/registry/paired intersection;
4. all six mappings return their fixed shapes; unknown wrapper names and invalid params are MCP errors;
5. unknown/unexposed underlying names are indistinguishable, scope/hard-constraint/approval failures are `isError`, and no denied tool executes;
6. an approval or general question never blocks an MCP call;
7. progress notifications arrive in monotonic order iff the request supplies `progressToken`;
8. cancelling an in-flight `prompt_send` yields SDK 1.29.0's standard JSON-RPC cancellation error for the original request, never a tool result/`isError`; later side effects stop, active request/question state clears, and the audit event exists;
9. `prompt_cancel` remains an ordinary tool call and cannot cancel a foreign/completed turn;
10. stdout contains JSON-RPC only; diagnostics and audit text never appear there.

Run:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_cli.py tests/integration/test_mcp_server.py
```

Expected: FAIL because stdio serving and `mcp serve` are absent.

**GREEN:** Implement only the stdio server and local CLI dispatch. Bridge progress from worker-thread callbacks to the AnyIO loop with a memory object stream or `loop.call_soon_threadsafe`; call `ServerSession.send_progress_notification()` only when a token exists. In the request handler, catch ordinary execution failures and convert them per the fixed matrix, but re-raise cancellation. Ensure `MCPService.close()` executes on shutdown.

Run the same command; expected: PASS.

Also run shared regressions:

```bash
uv run --locked pytest -q tests/unit/test_authority_scope.py tests/unit/test_permission_rules.py tests/unit/test_spawn_hard_constraints.py tests/agent/test_questions.py tests/unit/test_acp_server.py tests/unit/test_tools_runtime.py
```

Expected: PASS.

**Commit:**

```bash
git add openprogram/mcp/server/server.py openprogram/mcp/server/__init__.py openprogram/_cli_cmds/mcp.py openprogram/cli.py tests/unit/test_mcp_server_cli.py tests/integration/test_mcp_server.py
git commit -m "feat: serve authenticated MCP over stdio"
```

### Task 9: Record implementation evidence and run the complete release gate

**Files:**

- Modify: `docs/reference/design/integrations/mcp-server.html`
- Modify: `docs/reference/design/plans/2026-08-12-mcp-server.md` only if executed paths or commands differ materially
- Update: `.superpowers/sdd/2026-08-12-mcp-server/ledger.md` (gitignored; do not commit)

**RED:** Before editing status, run the complete gate and record exact command/output/commit evidence in the ledger:

```bash
uv run --locked pytest -q tests/unit/test_mcp_server_contracts.py tests/unit/test_mcp_server_auth.py tests/unit/test_mcp_server_cli.py tests/unit/test_mcp_server_security.py tests/unit/test_mcp_server_tools.py tests/unit/test_mcp_server_turns.py tests/integration/test_mcp_server.py
uv run --locked pytest -q tests/unit/test_authority_scope.py tests/unit/test_permission_rules.py tests/unit/test_spawn_hard_constraints.py tests/agent/test_questions.py tests/unit/test_acp_server.py tests/unit/test_tools_runtime.py
uv run --locked --with mdit-py-plugins python -m scripts.docs_site.build
uv run --locked --with mdit-py-plugins python -m scripts.docs_site.checklinks
git diff --check
```

Expected: every test and docs command passes. If any command fails, leave HTML implementation statuses unchanged, return to the task that owns the failure, add a new RED test if coverage was missing, and fix there.

**GREEN:** Only after the complete gate passes, update the HTML implementation-status table and evidence cells with actual files, tests, SDK/protocol versions, and observed counts. Preserve its document order: current state → competitor design → future plan → implementation evidence. Do not claim HTTP/OAuth/Web routes, ACP changes, SDK expansion, or any result not established by the gate. Keep cancellation wording explicit: SDK JSON-RPC cancellation error, no application tool result, OpenProgram cleanup/audit only.

Re-run the five commands above after the documentation edit. Expected: PASS and clean diff check.

**Commit:**

```bash
git add docs/reference/design/integrations/mcp-server.html docs/reference/design/plans/2026-08-12-mcp-server.md
git commit -m "docs: record MCP server implementation evidence"
```

## Review checkpoints

- Review Task 1 before implementation proceeds: schemas are the public v1 surface and later tasks must not alter them incidentally.
- Review Tasks 3–4 together for security: no owner credential reuse, no clientInfo authorization, no empty-list fallback to all tools, and no request-controlled authority fields.
- Review Tasks 6–7 together for fail-closed behavior: every rejection precedes invocation, questions do not wait, cancellation leaves no later result or side effect.
- Review Task 8 with the SDK client, not hand-written JSON-RPC frames. The SDK's observed cancellation behavior is part of the acceptance baseline.
- Task 9 is the only task authorized to mark Layer 3 as implemented or add implementation evidence to the HTML.
