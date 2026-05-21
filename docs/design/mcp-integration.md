# MCP 工具集成

Status: **implemented**(stdio transport only)。
关联代码:`openprogram/mcp/`。

## 一句话概括

把外部 MCP server 提供的工具注册成框架内的 `AgentTool`,LLM 调它跟调本地 `@function`/`@agentic_function` 完全一样。worker 启动时 spawn 配置文件里所有 enabled server 子进程,通过 stdio 跑 JSON-RPC,把它们的 `tools/list` 包装好挂进 `_registry`。

## 角色 — 我们做 client,不做 server

MCP 是协议,定义"工具的提供方"(server)和"工具的消费方"(client)。

| 角色 | 谁做 | 例子 |
|------|------|------|
| Server | 提供工具的进程 | `@drawio/mcp`、`@modelcontextprotocol/server-filesystem` |
| Client | LLM 框架,把 server 的工具暴露给 LLM | OpenProgram、Claude Desktop、Claude Code、Cursor、Cline、opencode |

我们是 client。本设计**不涉及**把 OpenProgram 自己的工具暴露给外部 client(如果以后要做,会是另一个独立模块,不复用 `openprogram/mcp/` 里的代码)。

## 目录位置

`openprogram/mcp/` 是 **top-level module**,跟 `openprogram/channels/` 平级。**不**放在 `openprogram/functions/` 下。

理由:
- MCP 是**外部协议适配器层**,跟"function 的实现"是两件事。我们的 function 体系(`@function` / `@agentic_function` / `agentics/` 下的代码)是本地实现的工具;MCP 是从外部进程取来的工具。两层概念正交。
- opencode (`src/mcp/`) 和 Claude Code (`src/services/mcp/`) 都把 MCP 当独立 module,跟本地 tool 实现完全分开。
- 我们项目里 `channels/`(消息渠道)就是已有的"外部协议适配器"先例 —— 它把外部消息源接进来。MCP 也是接外部东西,只是接的是工具源,**位置应该跟 channels 一致**。

## 参考实现

完整对照见 `references/opencode/packages/opencode/src/mcp/`。OpenProgram 的 MCP 层基本是 opencode 的 Python 翻译。

`references/hermes-agent/mcp_serve.py` 和 `references/openclaw/src/mcp/` 也提供 MCP,但**都是 server 端**(把自己的能力暴露给 Claude Desktop 等),跟我们要做的事方向相反。

## 配置

`<state_dir>/mcp_servers.json`(默认 `~/.agentic/mcp_servers.json`,profile 切换路径自动跟着走 `paths.get_state_dir`)。

```json
{
  "servers": {
    "drawio": {
      "type": "local",
      "command": ["npx", "-y", "@drawio/mcp"],
      "env": {},
      "enabled": true,
      "timeout_seconds": 60
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `type` | 目前只支持 `"local"`(stdio)。`"remote"` (HTTP/SSE + OAuth) 保留,opencode 有,我们暂未做 |
| `command` | 子进程命令 + 参数列表 |
| `env` | 注入子进程的环境变量(基础环境从父进程继承,这里 override / 追加) |
| `enabled` | false 临时禁用某个 server,无需删配置 |
| `timeout_seconds` | startup 等待 + 单次 call 的 read timeout |

文件缺失或解析失败都不致命——`load_configs()` 返回空 list,worker 正常启动,日志一行 warning。MCP 是 opt-in 功能。

## 代码结构

```
openprogram/mcp/
  __init__.py    public re-exports: load_mcp_servers / shutdown_mcp_servers / server_status
  config.py      MCPServerConfig + load_configs(读 mcp_servers.json,纯静态)
  client.py     MCPClient — 单个 server 的 stdio 子进程 + ClientSession + supervisor task
  adapter.py    MCP wire 类型 ↔ AgentTool 类型翻译(纯函数,无状态)
  registry.py    全局 manager: load_mcp_servers / shutdown_mcp_servers / server_status
```

四个文件每个一职责:配置、单连接、翻译、管理。

## 调用路径 — 只有 LLM 自动调用一条

```
你在 webui 输入 "画个流程图"
  ↓
worker → agent_loop → 调 LLM API,带上所有工具(本地 + MCP)
  ↓
LLM 决策,输出 tool_call(name="drawio__open_drawio_xml", args={...})
  ↓
agent_loop 在 _registry 查到 AgentTool,await execute(args)
  ↓ execute 是 adapter.register_remote_tool 里建的 closure
  ↓ closure 内部 capture 了对应 MCPClient,直接调 client.call_tool(原始工具名, args)
  ↓ MCPClient 通过 stdio JSON-RPC 发到对应 drawio 子进程
  ↓
drawio MCP 子进程执行(调 macOS open 命令开浏览器)
  ↓ 返回 CallToolResult
  ↓
adapter.convert_call_result 把 MCP content blocks 转成 AgentToolResult
  ↓
agent_loop 把结果塞进 chat history,再调一次 LLM
  ↓
LLM 看到工具结果,生成最终文本回复 "画好了"
  ↓
webui 显示
```

整条链路在 FastAPI 主 loop 上同步完成,**不涉及跨线程 / 跨 event loop 调度**。

`/api/run/{name}` / CLI `programs run` / `/functions` 页面**不**接 MCP 工具——它们是本地 function 的入口。MCP 工具用户不感知,LLM 隐性使用。

## 关键技术决策

### 1. 持久连接 + supervisor task

MCP 协议设计上是 long-lived 双向连接(server 可以推 progress notification、resource update 通知等)。Python 官方 SDK 把 session 暴露成嵌套 async-context-manager:

```python
async with stdio_client(params) as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        ...
```

跨函数调用持有 context manager 需要保持 `__aexit__` pending,Python 没有直接 API。最干净的写法是把整个嵌套块包进一个 long-lived coroutine,放进一个 `asyncio.Task` 当 supervisor:

```python
async def _supervisor(self):
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.list_tools()
            self._session = session
            self.tools = list(result.tools)
            self._ready.set()
            await self._shutdown.wait()      # 一直挂住,等关闭信号
```

`start()` 创建 supervisor task 并 `await self._ready.wait()`;`call_tool()` 直接复用 `self._session`;`stop()` `set self._shutdown`,supervisor 退出嵌套 `async with`,子进程自动关。

启动失败(spawn 不起来、initialize 报错)也走 `self._ready.set()`,把 `self.error` 设成原因字符串,`start()` 解除阻塞,manager 看到 error 跳过 tool 注册并 log 一行。

### 2. 工具命名空间 `{server}__{tool}`

opencode 用 `{server}:{tool}` 作为命名空间分隔符。我们换成双下划线 `{server}__{tool}`,因为 OpenAI 的 tool name regex 是 `^[a-zA-Z0-9_-]+$`,冒号会被拒。Anthropic 也是同样的字符集。

`adapter.namespace_tool_name(server, tool)` 同时:
- 替换 `[A-Za-z0-9_-]` 以外的字符为 `_`
- 截断到 64 字符(OpenAI 的硬上限),保留 tool 后缀

跨 server 同名工具会撞,后注册的覆盖前面的——跟框架其他地方的 last-wins 一致。

### 3. eager spawn vs. lazy spawn

opencode 是 lazy:服务实例化时不连,首次 `tools()` 调用才 spawn。我们选 **eager**:`load_mcp_servers()` 在 FastAPI startup hook 跑完才让 worker 进入服务状态。

理由:eager 实现简单,启动后状态稳定。代价是每个 enabled server 最多阻塞 `timeout_seconds` 秒(单 server 顺序启动)。drawio-mcp 首次 `npx -y @drawio/mcp` 还要去 npm 下载,可能慢,所以 timeout 默认给 30s,用户可以调高。

非致命:某个 server 启动失败,manager 记录 error,跳过它的工具注册,worker 继续启动并能用其他 server(以及所有本地工具)。

### 4. tolerant content 转换

`adapter.convert_call_result()` 处理 MCP 返回:

| MCP 类型 | 转成 |
|---|---|
| `TextContent` | `providers.types.TextContent` (chat 历史) |
| `ImageContent` | `providers.types.ImageContent` (multimodal LLM) |
| `EmbeddedResource` | 展开成 `[resource: <uri>]` 文本 — 不把 raw bytes 塞进 chat |
| 未知 / 未来类型 | `repr(block)` 文本 — 至少 LLM 能看到有东西返回了 |

`isError=True` 的返回会在 `details["is_error"]` 体现,LLM 看到的 chat 历史里就是工具结果文本,跟本地工具的 error path 一致。

### 5. 测试隔离 — 警惕 `from openprogram.paths import get_state_dir`

`tests/integration/test_attach_lazy_session.py` 用 `monkeypatch.setattr("openprogram.paths.get_state_dir", ...)` 重定向 state 目录。如果 `config.py` 用 `from openprogram.paths import get_state_dir` 引入,首次 import 发生在 attach 测试**期间**(因为 webui startup hook 触发 `_start_mcp_servers` → import `openprogram.mcp` → import `config`),lambda 会被永久 binding 到我们模块,attach 测试结束后泄漏。

修复:`from openprogram import paths as _paths`,每次调 `_paths.get_state_dir()` 现查 attribute。监控:test 套件如果加新的 paths monkeypatch,确认我们的 `config.py` 仍然走 module reference。

## 当前限制

1. **只支持 stdio**。HTTP/SSE/OAuth 待补(`MCPServerConfig.type` 字段已留位)。
2. **只支持 `tools/*`**。MCP 还有 `prompts/*` `resources/*` `sampling/*`,目前没接入——我们框架内没对应概念。
3. **配置改了必须重启 worker**。没有热重载 server 列表;`MCPClient.start()` 和 `stop()` 之间没有 `restart`。
4. **多 caller serialise 在 ClientSession lock**。`MCPClient._call_lock` 让同一 server 的并发调用排队;不是性能问题(MCP server 本来也是单飞行),但要注意一个慢工具会阻塞同 server 的其他工具调用。
5. **没做 tool 列表缓存**。supervisor 启动时拉 `list_tools` 一次,后续不再问;如果 MCP server 中途加/删工具,我们看不到(协议有 `tools/list_changed` notification,我们没监听)。
6. **`_loaded` 是模块全局**。测试要重置才能复测,见 `tests/integration/test_mcp_client.py::_reset_mcp_loader_flag` fixture。

## 后续可做

- 接 remote transport(HTTP / SSE)+ OAuth provider — 抄 opencode `mcp/auth.ts` + `mcp/oauth-provider.ts`
- 监听 `tools/list_changed`,运行期补注册
- webui 设置页面渲染 `server_status()`,可视化每个 server 的状态、错误信息、tool 数量、最近一次调用结果
- `restart_server(name)` API — 不用重启整个 worker
- 接 `sampling/createMessage` capability — server 可反向借 client 的 LLM 做推理(目前 client 不实现,会 reject 这种请求)
