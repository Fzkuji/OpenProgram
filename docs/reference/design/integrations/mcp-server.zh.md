# MCP 服务端 —— 被外部调用

> 本文是**服务端**方向的设计：外部程序（Claude Desktop、另一个 agent、IDE）
> 如何通过稳定协议驱动 OpenProgram 的工具。相反方向，即消费外部 MCP server
> 的工具，见 [`mcp-integration.md`](mcp-integration.md)。
> 关联代码：`openprogram/webui/`、`openprogram/functions/_runtime.py`、
> `openprogram/agent/internals/_approval.py`。
> 配套可视化：[`mcp-server.html`](mcp-server.html)。

## 一句话概括

用 MCP stdio 暴露一组小而固定的 OpenProgram 工具，对每个调用方做认证，把外部调用方
映射成一个低权权限档位，让工具调用走本地轮次同一条审批阶梯，并把取消、进度
和错误语义对齐 MCP 规范。协议客户端拿到的是一份能依赖的契约，不是内部路由表的快照。

## 层一 —— 我们现在怎么做

### 只有客户端，没有服务端

`openprogram/mcp/` 是纯客户端：八个模块（`client.py`、`adapter.py`、`config.py`、
`registry.py`、`oauth_flow.py`、`sampling.py`、`token_storage.py`），负责拉起外部
MCP server 并把它们的工具挂进注册表。代码里没有 `FastMCP`、没有 `mcp.server`、
没有 `stdio_server`、没有请求处理器。外部 MCP 客户端目前调不到 OpenProgram 的任何东西。

### 现有的外部控制面是什么

三个面，全是本地 HTTP，都没有版本化：

| 控制面 | 位置 | 形状 |
|---|---|---|
| FastAPI 路由 | `openprogram/webui/server.py` 的 `create_app()`，路由模块在 `openprogram/webui/routes/` | 约 25 个模块共 198 条路由 |
| WebSocket `/ws` | `openprogram/webui/server.py` 的 `_websocket_handler`，动作来自 `openprogram/webui/ws_actions/` | `WS_ACTIONS` 分发字典里约 100 个客户端→服务端动作，约 120 种服务端→客户端事件 |
| 工具注册表 | `openprogram/functions/_runtime.py` 的 `_registry` | `register()` / `get()` / `filter_for()`，按工具名索引 |

`AgentTool`（`openprogram/agent/types.py`）带 `name`、`description`、`parameters`、
`label`，以及：

```python
execute: Callable[
    [str, dict[str, Any], asyncio.Event | None, AgentToolUpdateCallback | None],
    Awaitable[AgentToolResult],
]
```

这个签名里已经有协议服务端需要的三样东西：参数字典、取消用的 `Event`、增量更新回调。
缺的只是一层把它们说给外部进程听的传输。

### 认证：没有，而且 origin guard 不算认证

`start_server()` 绑定 `web.host`（默认 `127.0.0.1`），并且**全程没有任何认证** ——
没有 token、没有 API key、没有会话 cookie、没有认证中间件。唯一的闸是
`BrowserOriginGuard`（`openprogram/webui/origin_guard.py`），它自己的调用点注释就写着
"nothing here authenticates a caller"。它拒绝 `Sec-Fetch-Site: cross-site` 和不在
白名单里的 `Origin`，而**不带 `Origin` 头的请求无条件放行** —— 这是刻意的，为了让
curl、TUI 和 Python 客户端能用。

两个后果构成本设计的安全前提：

1. 任何能连到端口的非浏览器客户端都能访问每一条路由。
2. 把 `web.host` 设成 `"0.0.0.0"` 会让 `is_loopback_hostname` 为假，从而
   `enforce_loopback_host=False`，**整条 Host 校验规则被关掉** —— 最需要它的那种
   部署方式恰好是把它关掉的那种。

这个面上还有 `POST /api/register`，它按请求体里给的模块路径做任意 import；以及
`GET /api/providers/{provider}/accounts/{name}/reveal`，它返回明文 provider 密钥。
在没有认证的前提下加一个协议服务端只会把一个已经敞开的面扩大，所以认证是本设计的
前置条件，不是后续加固项。

### 审批机制已经存在，而且就是正确的挂载点

`openprogram/agent/internals/_approval.py` 的
`wrap_with_approval(agent_tool, req, on_event)` 返回一个 `AgentTool` 副本，其
`execute` 是 `_gated_execute`。阶梯按固定顺序执行：

1. `_hard_constraint_violation()` —— 在规则和 bypass 之前判定，所以任何东西都取消不了它。
   当前只覆盖 `req.source == "agent_spawn"`。
2. 规则层 `deny` / `ask`，来自 `req.permission_rules`，优先级 `deny > ask > allow`。
3. `_FORCE_APPROVAL_TOOLS` —— 始终询问，bypass 下也询问。
4. `mode == "bypass"` 短路。
5. 规则层 `allow`。
6. `SAFE_AUTO_ALLOWLIST` —— 只读工具在任何模式下放行。
7. `acceptEdits` + `_accept_edits_safe` + `_path_is_safe()`。
8. `mode == "auto"` → `RISKY_AUTO_DENYLIST`，否则 `auto_classify_tool()`。
9. 其余走阻塞式审批卡片。

`PermissionMode` 是 `ask` / `bypass` / `acceptEdits` / `auto` / `plan`，默认 `ask`，
但 Web 聊天默认 `bypass`，spawn 出来的 sub-agent 在
`openprogram/agent/sub_agent_run.py` 里硬编码 `permission_mode="bypass"`。
`await_user_approval()` 在 `QuestionRegistry` 里开一条 `kind="approval"` 的记录，
发出的 `question.asked` 带 `tool`、`args` 和 `risk_level ∈ {low, medium, high}`；
返回 `(approved, reason, scope ∈ {once, always})`，`always` 会把 allow 规则写进
`<project>/.openprogram/settings.json`。

权限是一个**两档枚举**，`owner` / `paired`，以 `authority_tier` 字段挂在请求边界上，
与 `principal_id` 相区别。门口通过 `openprogram/agent/authority.py` 里的
`TIER_CAPABILITIES` 常量表把档位映射成固定能力集合；请求自己不携带能力列表，
调用方也就没有可构造的东西。执行判定是
`allow = hard_constraints ∧ TIER_CAPABILITIES[tier] ∋ capability ∧ permission_or_exact_owner_approval ∧ enforcement_boundary`，
并且fail-closed：档位缺失或无法识别时拒绝全部能力，不回落到某个缩小的集合。
本设计消费这个门口，档位模型见[`authority-handoff.md`](../memory/authority-handoff.md)。

### 取消

`openprogram/agent/run_control.py` 把取消建模为**按轮次的 `CancelToken`，从不按会话** ——
一个 `threading.Event` 加一个 `retired` 标志，所以轮次结束后才到的 stop 不会漏进下一轮。
`begin_turn` / `end_turn` / `register_cancel_event` / `mark_cancelled` /
`kill_active_runtime` 组成这层接口，`_cancel_hook` 在 import 时通过
`add_pre_invocation_hook` 和 `set_cancellation_check` 全局装上，所以每个
`@agentic_function` 入口和每次 `Runtime.exec` 都会中止。

现有两个入口语义不一致。WS `stop`（`ws_actions/runtime.py`）是两段式：先发优雅停止，
`_GRACEFUL_GRACE_S = 4.0` 之后硬杀，并通过 `cancel_session` 取消待答问题。HTTP
`POST /api/stop`（`routes/lifecycle.py`）是单段式，不碰问题注册表。渠道 worker 的轮次
根本不注册 token，对两者都不可见，这个缺口在 `run_control.py` 里已经标出。

### 错误语义

两套表示，没有共同字段。`ToolResult`（`openprogram/functions/_runtime.py`）有一等的
`is_error: bool`。`AgentToolResult`（`openprogram/agent/types.py`）只有 `content` 和
`details`，`_normalize_result()` 把标志挪进 `details["is_error"]`。于是每个消费方都写
防御性的 `details.get("is_error")`，也没有任何静态检查。具体载荷：拒绝是
`{"is_error": True, "denied": True}`，超时是 `{"is_error": True, "timeout": True}`。
事件层的 `AgentEventToolEnd` 倒是带一等的 `is_error`。

### 契约测试

`tests/unit/test_diagnostic_mcp_route_contracts.py` 是唯一钉响应形状的文件，198 条路由里
覆盖两条：

- `test_doctor_route_contract` —— `GET /api/doctor` 精确返回
  `{"results": [...], "all_ok": bool}`。
- `test_mcp_list_route_contract` —— `GET /api/mcp/servers` 精确返回
  `{"servers": [status]}`，status 字典 15 个键。
- `test_mcp_detail_route_contract_and_missing_status` —— 详情形状，以及不存在的 server
  返回 404 `{"detail": "server '<name>' not loaded"}`。

每个测试都新建一个裸 `FastAPI()` 再调模块的 `register(app)`，所以 origin guard 不在
路径上。断言用精确相等，加字段就会挂。这是对当前状态的准确描述：这两条是构造出来的契约，
不是政策上的契约。

## 层二 —— 参考框架怎么做

调查范围：`references/` 下每一个目录。

| 框架 | MCP 服务端 | ACP（agent 侧） | 入站 HTTP / webhook | 客户端 SDK |
|---|---|---|---|---|
| claude-code | 无（快照只有 `BashTool/`） | 无 | 无 | 无 |
| claude-code-leaked | `claude mcp serve`，stdio，全部内置工具 | 无 | `claude server`，bearer token | Agent SDK 走 control protocol |
| codex-cli | `codex mcp-server`，stdio，两个工具 | 无（用自己的 app-server） | app-server 走 stdio / WS / unix socket | `@openai/codex-sdk`（TS）+ `openai-codex`（Python） |
| openclaw | 三个 stdio server 加 MCP-over-HTTP | `openclaw acp`，走 Gateway | Gateway HTTP API + HMAC webhooks 插件 | `@openclaw/sdk`（private） |
| opencode | 无 —— 只做客户端 | `opencode acp` | `opencode serve`，HTTP Basic | `@opencode-ai/sdk` |
| hermes-agent | `hermes mcp serve`，FastMCP stdio，十个工具 | `hermes acp` 加 ACP 注册表条目 | HMAC webhook 适配器 + bearer 的 OpenAI 兼容 API | 无 |
| pi-ai | 无 | 无 | 无 | 不适用 —— 它本身是 provider 客户端库 |
| pi-mono | 无，且明确写着 "**No MCP.**" | 无 | 无 | 只有内嵌用的包 |
| weclaw | 无 | 只有 ACP **客户端**，自动放行所有权限请求 | `POST /api/send`，无认证 | 无 |

五点带进我们的设计。

**工具面刻意做小，做小的那些活得更久。** codex 只暴露两个工具，`codex` 和
`codex-reply` —— 开一轮、续一个 thread。hermes 暴露十个，全是自己会话库上的读取或消息
操作。openclaw 的渠道桥暴露九个，形状相同。只有 claude-code-leaked 暴露整个内置工具集，
而且 list 和 call 两处都用 `getEmptyToolPermissionContext()` —— 不加载任何用户权限规则，
`isNonInteractiveSession: true`，所以根本没有交互式询问路径。这是要避开的配置，不是要抄的。

**协议内审批有两种成型写法。** codex 用 `elicitation/create` 把 exec 和 patch 审批推回
MCP 客户端，带自己的关联字段（`threadId`、`codex_elicitation`、
`codex_mcp_tool_call_id`、`codex_event_id`），收回 `ReviewDecision`；它源码里注明这个
载荷还不符合 `ElicitResult`。openclaw 走轮询：`permissions_list_open` 和
`permissions_respond`，决策是 `allow-once | allow-always | deny`，所以不支持 elicitation
的通用 MCP 客户端也能用。hermes 照搬这一对。轮询降级平滑，elicitation 延迟更低但依赖
客户端支持。

**取消只在做了的地方才做。** codex 端到端实现了 `notifications/cancelled` —— 在活跃轮次
注册表里查到该轮并停止向它路由事件，同时明确回答 `tasks/cancel` 不支持。openclaw 的
plugin-tools server 把 SDK 的 `extra.signal` 转进 `handlers.callTool(params, signal)`。
claude-code-leaked 每次调用新建一个 `AbortController`，但从不接到通知上。hermes 没有取消。

**进度通知基本没人用。** codex 收到 `notifications/progress` 只是记日志；它自己的流式
输出是带 Codex 事件载荷的自定义通知形状。被调查的框架里没有一个对外发标准
`notifications/progress`。

**错误语义收敛到同一形状。** 每个实现对"工具跑了但失败"都返回
`{isError: true, content: [{type: "text", ...}]}`，把 JSON-RPC 错误留给协议级故障 ——
工具名不存在、参数格式错。这个划分我们采纳。

同一次调查里还有两个反面数据点：weclaw 的 `/api/send` 没有任何认证，它的 ACP 客户端
自动放行每个 `session/request_permission`；opencode 的 `serve` 在
`OPENCODE_SERVER_PASSWORD` 未设时以无认证运行，只打印一行警告。这两种"默认关"的姿态
都被本轮的验收标准排除。

## 层三 —— 设计

### 传输与入口

`openprogram mcp serve` 用 **stdio 单一传输** 跑 MCP 服务端。stdio 是被调查的每个实现
最先支持的传输，它继承拉起进程那方的信任边界，而且不会在一个已经无认证的监听面上再加一个
端口。HTTP 传输等 webui 自己有了认证再说。

服务端放在 `openprogram/mcp_server/`，与 `openprogram/mcp/` 平级的顶层模块。它不复用
客户端模块 —— `client.py` 和 `adapter.py` 翻译的是相反方向 —— 但它共用
`openprogram/functions/_runtime.py` 和审批阶梯，因为共用这两样正是全部意义所在。

### 最小工具集

六个工具，每个映射到一项能力，没有一个是通用逃生口：

| 工具 | 作用 | 能力 |
|---|---|---|
| `sessions_list` | 列出会话的 id、标题、更新时间 | `reply` |
| `session_get` | 取一个会话的消息 | `reply` |
| `prompt_send` | 在会话里开一轮并返回结果 | `reply` |
| `prompt_cancel` | 取消本调用方开的在途轮次 | `reply` |
| `tools_list` | 列出调用方档位允许的工具及其 schema | `reply` |
| `tool_call` | 按名调用一个工具 | 按工具而定 |

`prompt_send` 是把 codex 的 `codex`/`codex-reply` 收成一次调用，会话 id 可选。
`tool_call` 是需要下面那份白名单的那个；另外五个是读取或对话操作，除了往会话里追加以外
不产生宿主副作用。

没有 `register` 的等价物，没有配置改写，没有凭证访问，也不直接暴露文件工具 —— 想读文件的
调用方用文件工具名去调 `tool_call`，一样过白名单和审批阶梯。

### 工具暴露白名单

`tool_call` 按显式白名单解析，不按 `_registry` 的键解析。配置键
`mcp_server.exposed_tools` 存一个工具名列表，默认为空，所以新装的实例在 owner 点名之前
`tool_call` 一个工具都不暴露。`tools_list` 返回白名单与调用方档位允许集合的交集，
所以客户端永远看不到自己调不了的工具。

白名单是放在审批阶梯**之前**的过滤器，不是它的替代品。把工具写进
`mcp_server.exposed_tools` 只让它可达；某次调用跑不跑仍由阶梯判定。

### 外部调用方的权限档位

MCP 客户端正好是 [`sandbox-architecture.html`](../runtime/sandbox-architecture.html)
定义意义上的一种请求来源，它在那张表里占最低权的一行：

| 请求来源 | principal | `authority_tier` | 缺失字段处理 |
|---|---|---|---|
| 认证本地 Web / CLI / TUI | `principal_id=owner`，`interaction=interactive` | `owner`，持有 `approval.request` | 入口必须主动构造；认证无效即拒绝 |
| 已配对渠道账号 | 实例 owner，speaker 另存 | `paired`，持有 `reply` 与 `memory.source.append` | 不带档位的消息直接拒绝，不做降档 |
| **外部 MCP 客户端** | **实例 owner，客户端 id 另存** | **`paired`，再与暴露工具白名单取交集** | **客户端身份缺失或未通过校验即拒绝连接** |
| continuation / subagent | 显式继承 owner | 原样继承调用方档位，永不扩权 | 缺字段即状态错误，deny |
| cron 触发 | 创建时批准的 owner | 批准时固化的 job capability | 触发时不重算为 interactive |

subagent 那一行由 `runtime_authority()` 实现：它复制父轮次规范化后的权限，只改写
speaker 字段和 `interaction`，`authority_tier` 原封不动。子 agent 因此恰好持有父轮次的
档位，`paired` 轮次不可能派生出 `owner` 子 agent；父轮次没有有效权限时返回 `{}`，门口拒绝。

MCP 那一行推出三个结论。

MCP 调用方的 `interaction` **永不**为 `interactive`。因此它永远不持有
`approval.request`，也就无法申请一次性能力升级。一次需要审批而现场没有本地 owner 的工具
调用判为拒绝，跟 cron 触发一样 —— 不会静默放行，也不会永久阻塞。

MCP 来源轮次的 `permission_mode` 固定为 `ask`，不接受按请求配置。Web 聊天用的 `bypass`
默认值是本地交互场景的便利，不跨协议边界延伸。

hard constraints 一如既往最先跑，本设计把外部来源加进 `_hard_constraint_violation()`，
与 `agent_spawn` 并列：写入或 patch 到会话工作目录之外，以及 `_RISKY_TOOLS` 集合，
无论白名单、规则还是审批一律拒绝。

### 认证，默认开启

服务端从 `<state_dir>/mcp_server_token` 读 token，首次 `openprogram mcp serve` 时以
`0600` 权限生成并打印一次。客户端在 `initialize` 请求的 `clientInfo` 里出示它；不匹配
或缺失会在列出任何工具之前让 `initialize` 失败。比较用常量时间。

没有关闭认证的开关。出示不了 token 的调用方拿不到工具列表也调不了工具。这一点对齐 codex
的 token-file 模式和 claude-code-leaked 自动生成的 `sk-ant-cc-*` —— 不管运维有没有想到，
token 都存在；也刻意不对齐 opencode 的"警告后继续"和 weclaw 的无认证端点。

token 标识的是一个客户端，客户端 id 记进每条请求供审计。它不标识 *owner*：出示它拿到的是
上表那个 `paired` 档位，不是 owner 权限。

### 取消

`notifications/cancelled` 映射到已有的按轮次 `CancelToken`。服务端维护一张从 MCP 请求 id
到它开的那轮 `session_id` 与 token 的映射；收到通知时走 WS `stop` 用的同一条两段式路径 ——
先优雅请求，宽限期后硬杀 —— 并通过 `cancel_session` 取消待答问题，这正是 HTTP
`/api/stop` 目前缺的一步。请求 id 未知、或对应轮次已退休的通知是空操作，`retired` 标志
已经免费提供了这个行为。

`tool_call` 把 token 的 `asyncio.Event` 直接传进 `AgentTool` `execute` 签名的取消参数。
该参数已经存在也已经被遵守，服务端只需要把它填上。

### 进度

`execute` 签名里的 `on_update` 回调变成对着发起请求的 `progressToken` 发
`notifications/progress`，只在客户端提供了 token 时发送。`prompt_send` 报的是轮次级进度 ——
工具开始、工具结束 —— 而不是 token 级流式，这样通知量与实际工作量成正比，也不要求客户端
重组一条流。

客户端没给 `progressToken` 时回调被丢弃，调用照常返回结果。进度是优化，从来不是正确性要求。

### 错误

采用参考实现收敛出的那个划分：

| 情况 | 响应 |
|---|---|
| 工具名不存在 | JSON-RPC 错误，方法级 |
| 参数不过 schema 校验 | JSON-RPC 错误，invalid params |
| token 缺失或错误 | `initialize` 失败，不建立会话 |
| 工具不在 `mcp_server.exposed_tools` 里 | JSON-RPC 错误，按工具名不存在处理 —— 调用方得不到关于自己无权使用的工具的任何信息 |
| 档位不含该能力 | `isError: true`，正文点名缺失的能力 |
| 审批被拒或无法审批 | `isError: true`，正文带拒绝原因 |
| 工具跑了并失败 | `isError: true`，正文是工具的错误内容 |
| 轮次被取消 | `isError: true`，正文说明已取消 |

把 `AgentToolResult` 映射到这张表需要 `details.get("is_error")`，所以本设计把
`is_error` 提升为 `AgentToolResult` 上的一等字段，而不是跨协议边界去读一个无类型字典键。
这个字段在 `ToolResult` 和 `AgentEventToolEnd` 上已经有了，补齐第三处就能去掉每个调用点的
防御性 `.get`。

### 契约测试

验收标准是来自真实外部客户端的端到端测试，不是路由形状断言。
`tests/integration/test_mcp_server.py` 以子进程拉起 `openprogram mcp serve`，用 MCP SDK
自己的客户端连上去，断言：不带 token 时 `initialize` 失败、带 token 时成功；`tools/list`
精确返回白名单交集；白名单内的只读 `tool_call` 返回内容；白名单外的 `tool_call` 返回方法
错误；需要审批的调用返回 `isError` 而不是挂住；`notifications/cancelled` 能停掉在途的
`prompt_send`；进度通知当且仅当提供了 `progressToken` 时到达。

用 SDK 客户端而不是手搓 JSON-RPC 帧，才让这成为一个协议测试 —— 一旦我们偏离规范它就会挂，
而自洽的帧比对抓不到这种偏离。

## 层四 —— 理想状态与差距

### ACP

ACP 能让 Zed 之类的编辑器把 OpenProgram 当自己的 agent 驱动。被调查的框架里五家有它 ——
openclaw、opencode、hermes 在 agent 侧，weclaw 在客户端侧，其余由
`codex --experimental-acp` 这类开关覆盖。权限映射在各家之间是统一的：
`session/request_permission` 返回 `allow_once` / `allow_always` / `reject_once` 这三个
option id。

差距不在协议，在会话模型。ACP 期望在一条 stdio 连接上跑
`newSession` / `loadSession` / `prompt` / `cancel` / `setSessionMode`，并且由编辑器持有
文件系统 —— `fs/read_text_file` 和 `fs/write_text_file` 是客户端能力。我们的会话是 DAG
结构的，带分支、worktree 和按会话的工作目录，我们的文件工具直接读宿主。openclaw 自己的
文档就把 `loadSession` 标为部分实现，把按会话的 `mcpServers`、`fs/*` 和 `terminal/*`
标为不支持 —— 完整实现是一项分量不轻的映射工作。

现在不做，是因为同一份会话映射工作先服务 MCP，而且 MCP 会先把 ACP 否则要独立回答的
authority-tier 和审批问题定下来。等 `prompt_send` 和 `prompt_cancel` 把会话映射跑通了，
ACP 才值得评估。

### 入站 webhook

hermes 和 openclaw 都有，hermes 那版说明了正确性的代价：按路由的 HMAC secret 且启动时
校验、按路由的限流、防重投的幂等缓存、读取前先检查的 body 大小限制。这是地板不是加分项 ——
webhook 默认就是一个无认证的入站触发器。

差距在于 webhook 是**推**式触发，到达时没有 owner 在场，这把它归到跟 cron 触发同一类：
它需要注册时批准并固化的 job capability，而不是投递时算出来的档位。沙箱计划的批次 I 和
第 05B 步（cron 创建与管理）造的正是这套机制。在它之前做 webhook，要么另造一套平行机制，
要么接受一个无人值守触发器带着交互形状的 scope。

现在不做，是因为前置条件排在另一条线上，而且 MCP 覆盖了拉式集成 —— 外部程序想调我们 ——
这是我们真正能点名的需求。

### 客户端 SDK

codex 出 TS 和 Python，opencode 出 TS 且由签入仓库的 OpenAPI spec 生成，openclaw 出一个
private 的 TS 包，claude-code-leaked 出走 control protocol 的 Agent SDK。hermes 一个不出，
让集成方直接用 MCP、ACP 或 HTTP API。

差距在于 SDK 是一份兼容承诺，而我们没有稳定的东西可承诺。198 条 FastAPI 路由和约 100 个
WebSocket 动作是随前端变化的内部面，其中两条钉了形状。在这样的面上发 SDK，等于把每次内部
重构都变成外部调用方的破坏性变更。

现在不做，而且顺序是刻意的：先做 MCP，因为对每种有 SDK 的语言来说，MCP SDK *就是*那个
客户端库，这也正是 hermes 一个 SDK 不出还能被集成的原因。OpenProgram 专属 SDK 只有在 MCP
工具面被证明对真实集成方太粗时才配存在，而这个证据我们还没有。

### 稳定面在哪里

三处推迟的共同线索是：只有在一个我们愿意长期保持形状的面上，协议才值得发布。上面那六个工具
就是按"小到能长期保持"选的。FastAPI 和 WebSocket 两个面不是，本设计也不打算让它们变成
那样 —— 本设计的做法是把外部调用方引开。

## 附录：实现状态

层三的内容一件都没实现。当前状态是层一：一个没有服务端对应物的 MCP 客户端，一个无认证的
本地 HTTP 面，以及一条对本地轮次有效的审批阶梯。

| 项 | 状态 | 阻塞条件 |
|---|---|---|
| `openprogram mcp serve` stdio 服务端 | 未实施 | — |
| 六工具最小集 | 未实施 | — |
| `mcp_server.exposed_tools` 白名单，默认为空 | 未实施 | — |
| token 认证，无关闭开关 | 未实施 | — |
| 外部调用方的 `authority_tier` 行 | 未实施 | 档位门口本身已存在（`openprogram/agent/authority.py`）；缺的是构造带该字段请求的 MCP 入口 |
| 外部来源进 `_hard_constraint_violation()` | 未实施 | 不依赖批次 I，可与服务端同批落地 |
| `notifications/cancelled` → `CancelToken` | 未实施 | 直接用现有 `run_control.py` |
| `notifications/progress` 由 `on_update` 驱动 | 未实施 | 直接用现有 `execute` 签名 |
| `AgentToolResult` 上的一等 `is_error` | 未实施 | 涉及每个 `details.get("is_error")` 调用点 |
| `tests/integration/test_mcp_server.py` 端到端 | 未实施 | — |
| ACP | 本轮不做 | 先由 `prompt_send` / `prompt_cancel` 跑通会话映射 |
| 入站 webhook | 本轮不做 | 依赖沙箱批次 I 与第 05B 步的固化 job capability |
| 客户端 SDK | 本轮不做 | 需要一个稳定到可以承诺的工具面 |

关于当前面的两个事实是本设计的前提而不是本设计的条目：webui 没有认证，以及
`web.host = "0.0.0.0"` 会关掉 origin guard 的 Host 规则。两者都记在
[`sandbox.md`](../runtime/sandbox.md)，加一个 MCP 服务端并不修复它们。
