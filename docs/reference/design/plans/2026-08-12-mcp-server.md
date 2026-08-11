# MCP Server Implementation Plan — Conflict Report

> **For agentic workers:** DO NOT execute implementation work from this document. Planning stopped because the approved design conflicts with MCP 2025-11-25 and the repository's locked MCP SDK behavior.

**Goal:** Determine whether the approved `openprogram mcp serve` design can be translated into a strict-TDD implementation plan without changing its protocol or security semantics.

**Architecture:** The intended implementation remains a stdio-only MCP server beside `openprogram/mcp/`, backed by the existing session store, dispatcher, tool registry, approval gate, cancellation token, and progress callback. No production architecture is approved by this report because cancellation semantics require a design decision first.

**Tech Stack:** Python 3.11+, `mcp==1.29.0` from `uv.lock`, MCP protocol revision `2025-11-25`, pytest, stdio JSON-RPC.

## Global Constraints

- Baseline is `origin/main@9fb70113fd31dc2acf80a341e5ac51d244e1c35a`.
- The only transport is stdio through `openprogram mcp serve`.
- The protocol baseline is `2025-11-25`; protocol negotiation remains SDK-owned.
- The six approved MCP tools remain `sessions_list`, `session_get`, `prompt_send`, `prompt_cancel`, `tools_list`, and `tool_call`.
- MCP uses an independent external-client identity, a default-empty exposure allowlist, and a fixed non-interactive `paired` authority tier.
- Startup authentication reads `<state>/mcp_server_token` with mode `0600` and compares it to `OPENPROGRAM_MCP_TOKEN` in constant time before reading stdin.
- `serve` never creates a token, `initialize.clientInfo` never authorizes a request, and the Web owner token is never reused.
- Tool visibility and execution are both limited to the intersection of the exposure allowlist and the fixed tier capabilities.
- Unknown, unexposed, and unauthorized tool names fail closed.
- HTTP, SSE, Streamable HTTP, OAuth, Web routes, ACP changes, and a client SDK are out of scope.

## Blocking conflict

The approved design requires a cancelled turn to return `CallToolResult(isError=true)` and states that `notifications/cancelled` stops an in-flight `prompt_send`. Those two requirements cannot both be implemented under the selected protocol and current SDK.

The MCP 2025-11-25 cancellation specification says that a receiver of `notifications/cancelled` should stop processing, free resources, and not send a response for the cancelled request. A `CallToolResult(isError=true)` is a response to that request. See [MCP 2025-11-25 cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation).

The locked `mcp==1.29.0` SDK consumes `CancelledNotification` inside `BaseSession._receive_loop()`, calls `RequestResponder.cancel()`, cancels the request handler, and emits its own JSON-RPC cancellation error. It does not dispatch this notification through `Server._handle_notification()`, so an application-level notification handler cannot convert it into the design's `CallToolResult(isError=true)`. The cancelled tool handler's later result is suppressed.

| Layer | Required/observed behavior | Conflict |
|---|---|---|
| Approved HTML | Cancelled turn returns `isError: true` content | Requires a normal `tools/call` result response |
| MCP 2025-11-25 | Receiver should stop and not respond to the cancelled request | Excludes that normal result response |
| `mcp==1.29.0` | SDK cancels the handler and sends a JSON-RPC cancellation error | Exposes neither the approved result shape nor an application cancellation callback |

This is not an authentication or authorization issue. Token preflight, client identity, the empty allowlist, fixed `paired` scope, approval refusal, and fail-closed tool exposure remain unchanged.

## Required design resolution

The smallest SDK-compatible revision is:

> `notifications/cancelled` cancels the matching in-flight request, trips the mapped OpenProgram turn token, releases pending questions, and does not produce an application `CallToolResult`; the transport-visible cancellation response is owned by `mcp==1.29.0`. `prompt_cancel` remains a separate ordinary tool call and reports its own execution outcome.

This wording removes the conflict without broadening authority. It still needs explicit design approval because it replaces the approved cancellation response contract. A stricter implementation that sends no response at all would require replacing or extending the SDK's session cancellation behavior and is outside the approved minimal SDK-based scope.

## Audited baseline facts not applied to the HTML

- `uv.lock` resolves `mcp` to `1.29.0`, while the HTML says `1.27.0`; both identify `2025-11-25` as the latest protocol revision.
- The repository already implements `openprogram acp` through `openprogram/acp/server.py`, while the HTML's current-state matrix says OpenProgram has no ACP server.
- `TurnRequest` already carries runtime-owned `principal_id`, `authority_tier`, and `interaction`; `paired` currently grants only `reply`, `memory.read`, and `memory.source.append`.
- `_hard_constraint_violation()` and `_NON_INTERACTIVE_SOURCES` do not yet include MCP, so an implementation plan must add MCP to both gates rather than relying on the exposure allowlist alone.
- `AgentToolResult` still stores error state under `details["is_error"]`; the planned first-class field remains necessary if the design proceeds.

These factual HTML corrections were not made because the instruction for a real protocol/API conflict is to write the report and stop. No implementation tasks, RED/GREEN steps, production files, status matrix updates, or implementation commits are authorized by this report.
