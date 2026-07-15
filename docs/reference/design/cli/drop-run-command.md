# 统一 @agentic_function 执行路径

Status: implemented, with legacy comments still being cleaned up.

## 当前执行路径

UI 主动触发和 LLM 工具调用都应走同一条 runtime-block 路径：

```text
Web UI
  POST /api/function/{name}
    openprogram.webui.routes.chat.post_function
      dispatcher.dispatch_forced_tool_call(...)
        dispatcher._wrap_agentic_runtime_block(...)
          @agentic_function wrapper
            runtime.exec(...)
```

LLM 自己选择工具时不经过 REST endpoint，但同样进入 dispatcher 的工具执行和
runtime-block 包装逻辑。

## REST endpoint

`POST /api/function/{name}` accepts:

```json
{
  "session_id": "...",
  "kwargs": { "task": "..." },
  "work_dir": "/abs/path"
}
```

`session_id` is optional. If omitted, the server creates a session.

`work_dir` is optional. The server resolves it in this order:

1. explicit `work_dir`, `_workdir`, or `workdir`
2. the session's last workdir for this function
3. the repository root

For compatibility, older callers may still post flat function parameters at the
top level. The server converts those fields into `kwargs` and ignores control
keys such as `session_id` and `work_dir`.

The response is:

```json
{
  "session_id": "...",
  "msg_id": "..."
}
```

## Removed behavior

The typed `/run ...` chat command is no longer the function execution API.
Function invocation from React should call `POST /api/function/{name}` directly.

The backend parser keeps `/run ...` as plain user text rather than converting it
to `action="run"`. Retry UI should also use `POST /api/function/{name}`.

## Remaining cleanup

Some implementation comments and type names still say `/run` or `runtime block`
because the UI component name predates the unified endpoint. Those comments are
historical wording; they do not define a separate execution path.

Search targets when cleaning naming:

- `rg "/run|api/run|action=run|action=\"run\"" openprogram web`
- `rg "runtime block|RuntimeBlock" web openprogram`

Do not reintroduce `/api/run/{name}` or WebSocket `action="run"` for new code.
