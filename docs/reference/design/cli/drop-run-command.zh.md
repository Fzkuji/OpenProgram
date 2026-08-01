# 统一 `@agentic_function` 执行路径

`@agentic_function` 的调用只有一条执行路径。无论是用户从 UI 触发，还是模型
把它选为工具，执行都落在同一个 runtime-block 包装里。

## 执行路径

```text
Web UI
  POST /api/function/{name}
    openprogram.webui.routes.chat.post_function
      dispatcher.dispatch_forced_tool_call(...)
        dispatcher._wrap_agentic_runtime_block(...)
          @agentic_function wrapper
            runtime.exec(...)
```

模型自己选择工具时不经过 REST endpoint，但进入同一套 dispatcher 工具执行与
runtime-block 包装。

## REST endpoint

`POST /api/function/{name}` 接受：

```json
{
  "session_id": "...",
  "kwargs": { "task": "..." },
  "work_dir": "/abs/path"
}
```

`session_id` 是可选的。若省略，服务端会创建一个会话。

`work_dir` 是可选的。服务端按以下顺序解析它：

1. 显式的 `work_dir`、`_workdir` 或 `workdir`
2. 该会话上次为此函数使用的 workdir
3. 仓库根目录

为兼容起见，较旧的调用方可能在顶层直接提交扁平的函数参数。服务端会把这些字段
转换成 `kwargs`，并忽略 `session_id`、`work_dir` 等控制键。

响应为：

```json
{
  "session_id": "...",
  "msg_id": "..."
}
```

## `/run` 不是执行 API

输入式的 `/run ...` 聊天命令不是函数执行 API。React 发起的函数调用直接调用
`POST /api/function/{name}`，重试 UI 同样如此。

后端解析器把 `/run ...` 当作普通用户文本，而不将其转换为 `action="run"`。
`/api/run/{name}` 与 WebSocket 的 `action="run"` 都不存在。

部分实现注释和类型名仍写着 `/run` 或 `runtime block`，这是因为该 UI 组件的
命名早于统一 endpoint。这些属于历史措辞，并不定义一条独立的执行路径。
清理命名时的搜索目标：

- `rg "/run|api/run|action=run|action=\"run\"" openprogram web`
- `rg "runtime block|RuntimeBlock" web openprogram`
