# Unified `@agentic_function` Execution Path

There is one execution path for `@agentic_function` calls. Whether the user
triggers a function from the UI or the model selects it as a tool, execution
lands in the same runtime-block wrapper.

## Execution path

```text
Web UI
  POST /api/function/{name}
    openprogram.webui.routes.chat.post_function
      dispatcher.dispatch_forced_tool_call(...)
        dispatcher._wrap_agentic_runtime_block(...)
          @agentic_function wrapper
            runtime.exec(...)
```

A model-selected tool call skips the REST endpoint but enters the same
dispatcher tool execution and runtime-block wrapping.

## REST endpoint

`POST /api/function/{name}` accepts:

```json
{
  "session_id": "...",
  "project_id": "...",
  "kwargs": { "task": "..." }
}
```

`session_id` is optional. If omitted, the server creates a session.
`project_id` is optional for an existing session and required when a pending
Project selection must be bound before the first function call. Execution uses
the selected Project directory, falling back to the configured default Project.

For compatibility, older callers may post flat function parameters at the top
level. The server converts those fields into `kwargs` and ignores control keys
such as `session_id` and `project_id`.

The response is:

```json
{
  "session_id": "...",
  "msg_id": "..."
}
```

## `/run` is not an execution API

The typed `/run ...` chat command is not a function execution API. Function
invocation from React calls `POST /api/function/{name}` directly, and so does
the retry UI.

The backend parser keeps `/run ...` as plain user text rather than converting
it to `action="run"`. Neither `/api/run/{name}` nor a WebSocket
`action="run"` exists.

Some implementation comments and type names still read `/run` or
`runtime block` because the UI component name predates the unified endpoint.
That wording is historical and does not define a separate execution path.
Search targets when cleaning naming:

- `rg "/run|api/run|action=run|action=\"run\"" openprogram web`
- `rg "runtime block|RuntimeBlock" web openprogram`
