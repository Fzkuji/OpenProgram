# Operate OpenProgram through backend tools

The `framework` tool discovers and calls OpenProgram's authenticated backend.
Use it for session, project, configuration, file, application and execution
management without asking an Agent to click OpenProgram's interface. The
`resource` tool operates persistent environments: Web pages, Terminals and
installed applications.

## Discover a business operation

```python
framework(action="describe", operation="settings")
framework(action="invoke", operation="GET /api/settings")
```

Use the exact operation ID returned by discovery. HTTP arguments use `path`
for named path parameters, `query` for query parameters, and `body` for JSON.
Binary endpoints accept `content_base64` and `content_type`. Results include
HTTP status; an unsuccessful response does not mean a mutation is safe to retry.
Discovery supports `arguments={"offset": 50}` and returns `next_offset`.

Existing WebSocket business commands also have request/response backend entries:

```python
framework(action="commands")
framework(action="command", operation="rename_session",
          arguments={"session_id": "EXACT_SESSION_ID", "title": "Research"})
```

Consult the returned fields and the command's canonical validation. Command
results contain response frames. Long-running work remains owned by the worker;
read its execution or session state for progress and completion. File operations
retain their existing request IDs, idempotency keys and revision checks.

## Operate a window without screen automation

```python
framework(action="interface")
framework(action="interface", operation="tabs.list",
          arguments={"window_id": "EXACT_WINDOW_ID", "arguments": []})
```

The returned catalogue declares supported tab, group, split, sidebar, resource
view, appearance, bookmark and native Desktop commands with argument schemas.
Commands target one authenticated connected window. Native history, downloads,
browser import and update controls use the existing Desktop functions. A browser
window reports native-only capabilities as unavailable. Missing or ambiguous
windows fail explicitly. A lost reply is not automatically retried.

Window presentation still needs a connected view; backend business operations
and installed application operations do not need a rendered frontend. Unsaved
file tabs must be saved or discarded before an automated close.

Web creation and closure use `resource(provider="web", action="open"/"close")`.
Native Page presentation commands require `arguments.web_session_id` from the
current `web` observation, in addition to the declared positional arguments.
They retain the existing Page lease, session access and enabled-tool policy.

## Authority

The tools retain normal tool approval and require owner authority. Framework
administration cannot run outside an active sandbox through an unrestricted
host fallback. Authentication bootstrap, transport receipts and Agent approval
answers are excluded from the operation catalogue. An Agent cannot use this
tool to approve its own pending operation or change approval modes or rules.
Installed Python applications still
require explicit trust, and only manifest operations with `agent: true` appear
in their resource action schemas.

See [application packages](applications.md) for automatic provider registration
and [Desktop resources](../interfaces/desktop.md) for Web and Terminal behavior.
