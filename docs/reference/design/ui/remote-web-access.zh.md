# 远程 Web 访问——单一所有者的自托管控制

> 本文定义所有者如何在本机、可信局域网或 VPN、SSH 隧道，以及所有者自行运维的
> HTTPS 反向代理上使用 OpenProgram 现有 Web UI。英文正文是规范基准；本文和独立
> HTML 页面表达同一设计。关联代码：`openprogram/webui/owner_auth.py`、
> `openprogram/backend_endpoint.py`、
> `openprogram/webui/server.py`、`openprogram/cli/commands/web.py`、
> `apps/web/lib/net/owner-auth-bootstrap.ts`、`openprogram/agent/authority.py`。
> 关联设计：[说话人身份](../memory/speaker-identity.md)、
> [权限模型](../runtime/permission-model.md)、[MCP 服务端](../integrations/mcp-server.html)。

OpenProgram 在所有部署方式下使用同一 authority 模型：进程生命周期内有效的实例
token 认证当前 profile/state 实例的唯一所有者，每个通过认证的 Web 请求取得所有者
档位及其固定 capability 集。
OpenProgram 不运营公网中转，也不把 Web UI 改成多用户应用。

## 1. 方法与范围

### 1.1 一个当前 state 实例，一个所有者

Web UI 可以操作会话、文件、进程、凭据、设置、工具、审批和 agent 动作。这些功能属于
同一个管理权限边界，不是多个项目角色。Web 认证要判断的是“请求是否持有当前 Web 进程
token”，而不是“请求来自哪一个已注册用户”。

认证成功后，请求映射到 `openprogram/agent/authority.py` 中稳定的 principal
`owner/install/<16hex>` 以及 `owner` 档位。虽然 identifier 格式保留
`install`，对应 `owner.json` 实际位于 profile-aware state directory，因此单一所有者
边界属于当前 profile/state 实例，不是同一 OS 账号下所有 OpenProgram profile 的全局
边界。持有 token 表示请求具备该唯一所有者权限，但不能证明浏览器前具体是哪一个自然人。
OpenProgram 不保存 Web 用户表、密码库、成员关系、角色分配或项目 ACL。

多人参与继续由 channel 层处理。Telegram、Discord 等渠道消息保留 speaker 归因和受限的
`paired` authority tier。渠道中存在某个参与者，不会使该参与者取得 Web 所有者
权限。

### 1.2 四种支持的访问方式

| 方式 | OpenProgram 监听地址 | 浏览器访问方式 | 必需保护 |
|---|---|---|---|
| 同一台机器 | `127.0.0.1` | 直接访问 loopback URL | 实例 token、Host/Origin 校验 |
| 可信局域网或加密 VPN | 显式设置 `web.host`，通常是 `0.0.0.0` | 直接访问地址 | 实例 token、非空精确 Origin、Host 校验，以及 HTTPS 或加密网络；直接 HTTP 只作为带警告的例外，并限于 5.5 节列出的本地或 overlay 地址范围 |
| SSH 隧道 | `127.0.0.1` | 本地转发端口 | 实例 token；SSH 提供传输加密 |
| 所有者公网域名 | 同机代理后端的 `127.0.0.1` | 所有者维护的 HTTPS URL | 实例 token、精确 Origin/Host、仅信任 loopback 代理、HTTPS |

只有局域网或 VPN 直接访问需要修改 `web.host`。SSH 隧道和与 OpenProgram 同机的反向
代理都连接 loopback listener，不需要把 OpenProgram 改绑到外部网卡。

### 1.3 产品边界

OpenProgram 提供应用认证并校验浏览器请求边界。它不负责：

- 签发或续期 TLS 证书；
- 运营公网中转、托管隧道、发现服务或分享 URL；
- 创建 Web 账号、注册、邀请、RBAC 或项目权限；
- 用 identity-aware proxy、Tailscale identity 或 OAuth provider 代替实例 token；
- 提供无认证或 `--insecure` 模式。

SSH、VPN、nginx、Caddy 和证书自动化是独立运维组件。使用这些组件时，OpenProgram
认证仍然开启。

## 2. 当前实现与威胁模型

### 2.1 当前已经存在的能力

`openprogram/webui/server.py` 中的 `_web_config()` 默认使用 `127.0.0.1`，并读取
`web.host` 和 `web.allowed_origins`。`create_app()` 使用 FastAPI lifespan context，
并为 HTTP 与 WebSocket ASGI scope 安装
`openprogram/webui/owner_auth.py` 中的 `OwnerAuthMiddleware`。该 middleware 在 route
dispatch 前验证 canonical request origin，对受保护的 HTTP、SSE 与 WebSocket 使用同一套
cookie 或 Bearer 认证规则，并且只在认证成功后附加当前 profile 的 owner authority。
WebSocket 校验发生在 `websocket.accept` 之前。

`OwnerAuthState` 生成 32 字节进程 token，取得 per-state `web.lock`，写入 owner-only
`web/token` 与不含 token 的 `web/access.json` policy snapshot，派生 profile-specific
HttpOnly cookie，并在关闭时清理自己拥有的状态。
`canonicalize_origin()` 与 `resolve_effective_origins()` 验证精确 Origin，只加入适用的
loopback 默认值，并拒绝没有配置 Origin 的非 loopback bind。Uvicorn 以
`proxy_headers=False` 启动；只有 `OwnerAuthMiddleware` 可以使用原始 immediate peer 为
loopback 时提供的单一 `X-Forwarded-Proto`。

公共 `POST /api/auth/bootstrap` 把 fragment token 交换为派生 cookie。前端
`apps/web/lib/net/owner-auth-bootstrap.ts` 中的 coordinator 同步清除 fragment，在挂载应用子树
之前完成交换，并且不使用 Web Storage。`openprogram web auth-url --base-url ...` 先通过
nonce/HMAC ownership challenge 验证 active listener，再只为 effective Origin 输出 fragment
URL。`/healthz` 现在只返回 `{"status":"ok"}`；需要认证的
运维诊断位于 `/api/diagnostics`。

Provider API 的部分响应默认掩码，但仍有两条生产路径返回明文：

- `GET /api/providers/{provider}/accounts/{name}/reveal`；
- `GET /api/config/key/{env_var}?reveal=1`。

当前剩余缺口小于最终契约的完整范围：两条 reveal 路径及其 frontend control 仍然存在；
启动输出还没有包含 6.1 节要求的全部字段和直接 HTTP 警告。现有可执行覆盖包含 middleware、
token 生命周期、bootstrap coordinator、CLI URL 生成、HTTP 和 WebSocket，但尚未覆盖 6.3
节完整的 browser、SSE、restart、multi-profile 与 nginx/Caddy 验收矩阵。

### 2.2 为什么 loopback 也必须使用 token

Loopback 只限制哪些网络接口接受连接，不认证浏览器请求，也不认证本机其他进程。

| 调用方 | 剩余威胁 | 必需控制 |
|---|---|---|
| 任意网页 | 可以向 localhost 发送部分 HTTP 请求；WebSocket 不受 HTTP 同源读取规则保护 | 任何动作前同时验证 token、精确 Origin 和 Host |
| DNS rebinding 网页 | 可以让恶意域名解析为 loopback 地址，并发送外部 Host | Host authority 必须 fail-closed 校验 |
| 本机进程或另一个系统账号 | 可以像 `curl` 和原生客户端一样省略 `Origin` | Bearer token；缺少 Origin 不表示可信 |
| 外部监听后的局域网设备 | 可以直接连接当前全部 route 和 `/ws` | 强制 token、精确 Origin、Host 校验和受保护传输 |
| 反向代理客户端 | 后端保持 loopback，但请求呈现公网 Host | 显式公网 Origin，并仅信任 loopback 代理 |

Jupyter Notebook 4.3 默认启用 token 认证，并把生成的 token 提供给自动打开的浏览器，
在保持正常启动无需输入的同时认证可执行操作的本地浏览器环境
（[4.x changelog](https://github.com/jupyter/notebook/blob/4.x/docs/source/changelog.rst)）。
当前 Jupyter Server 文档继续使用 token/cookie 认证，并要求公网部署使用 HTTPS。这里引用
的是交互方式先例，不是断言两个产品的代码和威胁集合完全相同。

### 2.3 安全不变量

设计与实现保持以下不变量：

1. 认证前不返回受保护的应用状态、会话数据、项目或用户文件字节、secret metadata、
   SSE event 或 WebSocket frame。
2. 认证前不执行 HTTP、SSE 或 WebSocket 动作。
3. 缺少 `Origin` 不能建立信任。只有有效 Bearer 请求，或方法本身不要求 Origin 的安全
   cookie 请求，才可以省略 Origin。
4. Cookie 认证继续执行 CSRF 控制。Token 与 Host、Origin 校验叠加，不能替代它们。
5. 非 loopback 直接监听且 Origin 配置不完整时，在 server 接受连接前失败。
6. 初次录入完成后，任何 route 都不返回已保存 secret 或 Web 实例 token。

## 3. 开源框架对比

### 3.1 调查边界

调查覆盖 OpenProgram 现有设计语料中的开源系统，以及与 Web 部署、认证、代理或 secret
处理直接相关的补充系统。不存在的能力会明确记录。这不是对所有公开 agent 仓库的穷举。

| 系统 | 已核实设计 | 对远程访问的含义 | OpenProgram 的处理 |
|---|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) | Gateway 默认 loopback；非 loopback 需要认证；Control UI 使用显式 Origin；文档包含 SSH 和反向代理，以及 Host/DNS rebinding 和可信代理控制（[远程访问](https://github.com/openclaw/openclaw/blob/main/docs/gateway/remote.md)、[安全](https://github.com/openclaw/openclaw/blob/main/docs/gateway/security/index.md)、[Control UI](https://github.com/openclaw/openclaw/blob/main/docs/web/control-ui.md)） | 覆盖本机、隧道和所有者自行部署的远程方式 | 采用精确 Origin、fragment bootstrap、SSH/代理方式和代理信任限制；即使有外部身份层也保留 token |
| [Jupyter Server](https://github.com/jupyter-server/jupyter_server) | Notebook 4.3 默认启用 token，并把生成 token 提供给自动打开的浏览器；当前 Server 使用 token/cookie 认证，公网部署要求 HTTPS，并保留 XSRF 与 WebSocket Origin 校验（[4.x changelog](https://github.com/jupyter/notebook/blob/4.x/docs/source/changelog.rst)、[安全](https://github.com/jupyter-server/jupyter_server/blob/main/docs/source/operators/security.rst)、[公网 server](https://github.com/jupyter-server/jupyter_server/blob/main/docs/source/operators/public-server.rst)） | 是成熟的单一所有者浏览器交互先例 | 采用自动 token 输入和 cookie 转换；把 query token 改成 fragment |
| [Hermes Agent](https://github.com/NousResearch/Hermes-Agent) | Dashboard 默认 loopback；非 loopback bind 强制使用 password 或 OAuth provider，缺少 provider 时拒绝启动；`--insecure` 已弃用且不能关闭该校验；Desktop 用认证 session 换取单次 WS ticket；key 列表掩码，但仍保留认证后限流的 [reveal route](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/web_server.py#L7663-L7696)（[dashboard](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md#when-the-gate-engages)、[WS ticket](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/dashboard_auth/ws_tickets.py)） | 提供外部 bind fail-closed、session/WS 和掩码先例，但 user-auth provider 与 secret retrieval 不适用 | 采用外部 fail-closed 和掩码视图；使用 OpenProgram 实例 token 与同源 cookie，不增加 auth-provider 账号或第二套 WS ticket；拒绝 reveal |
| [Agent Zero](https://github.com/agent0ai/agent-zero) | 本地 UI、可选单组登录、session cookie、CSRF 与 WebSocket Origin 校验；支持反向代理和内置第三方隧道；[settings](https://github.com/agent0ai/agent-zero/blob/main/helpers/settings.py) 使用掩码占位符（[安装](https://github.com/agent0ai/agent-zero/blob/main/docs/setup/installation.md)、[VPS 部署](https://github.com/agent0ai/agent-zero/blob/main/docs/setup/vps-deployment.md)） | Cookie/CSRF/Origin 的组合方式和 secret 更新方式可复用 | 采用组合校验与仅掩码更新契约；拒绝可选认证和内置公网隧道 |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Agent Canvas 用 session API key 认证 HTTP 与 WebSocket，并记录 SSH、nginx/HTTPS 自托管方式（[自托管](https://github.com/OpenHands/OpenHands/blob/main/docs/SELF_HOSTING.md)）；其他版本增加账号型控制 | 表明本地产品可以使用实例 key，不必先引入 RBAC | 采用统一 HTTP/WS key 和 loopback 后端；拒绝把 token 暴露在公共 HTML 或 browser storage，也拒绝认证后明文取 secret |
| [opencode](https://github.com/sst/opencode) | Server/Web 默认 loopback；Basic Auth 可选；支持配置 CORS Origin 和短期 PTY WebSocket ticket（[server](https://github.com/sst/opencode/blob/dev/packages/web/src/content/docs/server.mdx)、[network options](https://github.com/sst/opencode/blob/dev/packages/opencode/src/cli/network.ts)、[PTY ticket](https://github.com/sst/opencode/blob/dev/packages/core/src/pty/ticket.ts)） | 本机默认值可用，但缺少密码时不足以构成外部 fail-closed 规则 | 采用 loopback 默认值；HttpOnly cookie 已避免长期 WebSocket query credential，不再增加第二套 ticket |
| [Open WebUI](https://github.com/open-webui/open-webui) | 多用户账号与角色；官方 nginx/Caddy 文档覆盖 HTTPS、WebSocket Upgrade 和 SSE buffering（[nginx](https://github.com/open-webui/docs/blob/main/docs/reference/https/nginx.md)、[Caddy](https://github.com/open-webui/docs/blob/main/docs/reference/https/caddy.md)） | 部署方法可复用，身份模型不适用 | 采用代理配置；拒绝 signup、账号、JWT 用户 session、group 和 RBAC |
| [Dify](https://github.com/langgenius/dify) | Workspace/account role 和反向代理部署；[credential response](https://github.com/langgenius/dify/blob/main/api/core/entities/provider_configuration.py) 使用混淆值和隐藏值更新语义 | 在不同身份模型中提供了可靠的 secret response 先例 | 采用仅掩码 secret response 和显式替换；拒绝 tenant 与 role 层 |
| [LibreChat](https://github.com/danny-avila/LibreChat) | 注册、管理员、用户/组/角色权限、JWT session，以及 nginx HTTPS/WebSocket 部署文档（[认证](https://github.com/LibreChat-AI/librechat.ai/blob/main/content/docs/features/authentication.mdx)、[nginx](https://github.com/LibreChat-AI/librechat.ai/blob/main/content/docs/remote/nginx.mdx)） | 说明真正的多用户设计所需的数据模型 | 只采用代理配置细节，不实现其身份数据模型 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 单用户和多用户模式相互区分；单用户未配置 password token 时，请求绕过认证；多用户模式增加角色校验（[认证 middleware](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/middleware/validatedRequest.js)、[角色 middleware](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/middleware/multiUserProtected.js)） | 单一共享凭据符合一个所有者，但可选认证不符合要求 | 保留单一所有者概念，认证改为不可关闭 |
| [AutoGen Studio](https://github.com/microsoft/autogen/tree/main/python/packages/autogen-studio) | 默认 loopback；[默认 auth type](https://github.com/microsoft/autogen/blob/main/python/packages/autogen-studio/autogenstudio/web/auth/manager.py) 是 none；可选 OAuth/JWT；项目文档将其定义为 research prototype | 不能作为生产公网访问基准 | 只采用 loopback 默认值；拒绝 query/localStorage credential、接受 WS 后再认证和可选认证 |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | 当前仓库记录命令行 agent 执行，不提供所有者自托管 Web control UI（[文档目录](https://github.com/SWE-agent/SWE-agent/tree/main/docs)） | 目标远程 Web 能力不存在 | 对本认证设计记为不适用 |
| [pi-mono](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) | Coding-agent package 记录了 TUI、[SDK](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md) 和面向进程的 [RPC mode](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md)，但 package 文档没有定义所有者自托管远程 Web control server 的认证契约 | 存在相关传输和 UI 组件，目标能力不存在 | 对本认证设计记为不适用 |
| [pi-ai](https://github.com/badlogic/pi-mono/tree/main/packages/ai) | Provider transport library，不是 agent control UI | 不存在远程 Web ownership 或部署边界 | 记为不适用 |
| [WeClaw](https://github.com/fastclaw-ai/weclaw/blob/main/README.md) | 文档中的 HTTP API 默认使用 `127.0.0.1:18011`，也允许修改监听地址，但项目没有记录所有者 Web control UI 的浏览器认证契约 | 存在外部 HTTP，但目标浏览器界面不存在 | 不将其作为远程 Web 安全先例 |
| [Codex CLI](https://github.com/openai/codex) | `codex app-server` 提供 stdio 和实验性 WebSocket transport，remote-control 使用 enrollment 与 pairing；它不是所有者自托管浏览器 UI（[app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)） | 协议和托管远程控制问题与本文自托管页面不同 | OpenProgram 不运营 relay，因此不采用其 service-mediated remote-control 方式 |

### 3.2 必须区分的三类设计

反复出现的三类设计对应不同需求，不能在没有需求时合并：

- **实例 credential：**Jupyter、OpenClaw 和 OpenHands 使用 server 或 session
  级 credential，适合一个管理所有者。
- **应用账号：**Open WebUI、Dify、LibreChat 和 AnythingLLM 多用户模式需要注册、持久
  session、角色和资源策略。
- **托管远程控制：**带 pairing、托管隧道或 relay 的产品，需要在本地进程之外管理设备
  enrollment 和公网连接。

OpenProgram 使用第一类。第二类会引入当前产品不需要的状态和策略。第三类要求 OpenProgram
运营一个已明确不提供的服务。

## 4. OpenProgram 采用、修改和拒绝的部分

| 来源模式 | 处理 | OpenProgram 形式 |
|---|---|---|
| Jupyter 自动 launch-token 体验 | 修改 | Token 放 URL fragment，单次换取 HttpOnly cookie，并在任何认证 fetch 前清除 fragment |
| OpenClaw 显式 Origin、SSH 文档和代理信任边界 | 采用 | 精确 canonical Origin、解析后的 Host authority、仅信任 loopback 代理，不提供 Host fallback |
| 高熵进程 credential | 采用 | 每个 Web 进程用标准库生成 32 个随机字节，owner-only 保存，对解码后的字节使用 `hmac.compare_digest` 比较 |
| Agent Zero 的 cookie + CSRF + Origin + WebSocket 校验 | 采用 | 浏览器使用 cookie 时四项同时要求；没有有效 Origin 的非安全 cookie 请求被拒绝 |
| Dify 与 Agent Zero 的掩码 secret 更新 | 采用 | 只返回掩码；未修改 secret 时省略字段；显式替换必须提供新值 |
| opencode 可选 Basic Auth | 拒绝 | 不存在关闭认证的组合，也不存在只警告但继续外部监听的模式 |
| Open WebUI/LibreChat 账号与 JWT 模型 | 拒绝 | 不增加用户数据库、refresh token、role、group 或项目 ACL |
| OpenClaw trusted-proxy 或 Tailscale identity 认证 | 修改 | Proxy 可以提供传输或额外控制，但 OpenProgram token 仍然强制 |
| Agent Zero 内置隧道和托管 remote-control relay | 拒绝 | 只记录 SSH 和所有者管理的 HTTPS；OpenProgram 不创建公网 endpoint |
| 登录后明文 reveal secret | 拒绝 | Owner 认证允许替换和使用，不允许通过 UI 取回明文 |

最终设计只有一个根密钥、两种传输凭证形式，配一套浏览器 bootstrap、一种应用 authorization
映射，以及一组 HTTP/WS/SSE 校验规则。根密钥就是每次启动生成的实例 token，它到达 server 的
形式有两种：`Bearer` header（原生 HTTP、SSE、WebSocket 和内部客户端），或浏览器用 fragment
token 换取的 profile 作用域 HttpOnly cookie。两种形式携带同一个密钥、走同一套校验，因此
不增加第二套 WebSocket ticket、用户 session database 或 OAuth flow。

## 5. 最终远程访问设计

### 5.1 Token 生命周期

每次 Web server 启动时，OpenProgram 用 `secrets.token_bytes(32)` 生成恰好 32 个随机
字节。外部形式是不带 padding 的 base64url，因此恰好是 43 个 ASCII 字符。写 token 前，
进程先取得 `<state-dir>/web.lock` 的 owner-only 操作系统 exclusive lock。每个 profile/state directory
只允许一个活动 Web 进程；第二个进程失败，并且不得读取、替换或使第一个进程的 token 失效。
该 token：

- 只在一个 Web 进程生命周期内有效，重启后更换；
- 在 listener ready 前原子写入 `<state-dir>/web/token`；
- 同时写入 owner-only `<state-dir>/web/access.json` snapshot，其中只能包含
  `version`、`bind_host`、`port`、canonical `effective_origins` 与
  `token_fingerprint`，不包含 token；
- 创建时使用 owner-only 权限，再调用 `openprogram._compat.restrict_to_user()` 做跨平台
  权限限制；
- 不从配置、命令行参数或环境变量读取；
- 先解码为恰好 32 字节，再只通过 `hmac.compare_digest` 比较；
- 不出现在常规 server log、exception、access log、telemetry 或 Web response 中；
- 日志只记录 `sha256:<sha256(raw_token_bytes).hexdigest()[:12]>`。

文件写入复用 `openprogram/mcp/token_storage.py` 已有的临时文件、`os.replace` 和权限处理
方式。OpenProgram 无法取得 lock，或无法安全创建、回读 token 文件时，启动失败。
`read_active_web_access()` 只有在 snapshot schema 与 Origin 都有效，并且 fingerprint 与
live token file 匹配时才返回 `ActiveWebAccess`。Bind 或后续启动失败时，只有当前进程仍持有 lock 且文件
内容仍是自身 token 才能删除 token；只有 `access.json` 中 fingerprint 是自己的值时才能
删除 snapshot。正常关闭采用相同 ownership 校验。下个进程取得 lock 后原子替换未加锁的
遗留文件。

### 5.2 Credential 形式

所有 credential 形式都来自同一个进程 token：

| 客户端 | Credential | 传输规则 |
|---|---|---|
| Bootstrap 后的浏览器 | `openprogram_owner_<owner-id-suffix>` HttpOnly cookie，值为 `base64url_no_pad(HMAC-SHA256(key=raw_token_bytes, msg=b"openprogram-web-cookie-v1"))` | Suffix 是当前 profile principal 的 16-hex 后缀；`SameSite=Strict`、`Path=/`、无 `Domain`；可信 effective scheme 为 HTTPS 时加 `Secure`；不设持久过期时间 |
| 原生 HTTP/SSE | `Authorization: Bearer <token>` | 只接受 header，拒绝 query 参数 |
| 原生 WebSocket | Upgrade 请求中的 `Authorization: Bearer <token>` | 只接受 header |
| 浏览器 WebSocket | 同一个 HttpOnly cookie | 浏览器在 Upgrade 时发送 cookie 与 Origin |

Per-profile 名称避免同一 hostname 不同端口上的多个 profile server 相互覆盖 cookie；cookie
本身不按端口隔离。Cookie 值同样恰好是 43 个 base64url 字符。它不是用户 session，没有
数据库记录。预期值由当前 token 重新计算，server 重启后原 cookie 自动失效。有效 cookie 或 Bearer 都映射到
`owner_authority(owner_principal_id)`，其中 principal 在 `OwnerAuthState` 为当前 profile
启动时确定。除 bootstrap 外，受保护 route 的请求存在 `Authorization`
header 时只使用 Bearer 路径：非 Bearer scheme、格式错误或 token 错误均返回 `401`，
不能回退到有效 cookie。

### 5.3 Fragment bootstrap

本机正常启动保持无需输入：

```text
CLI                     Browser                  Web server
 | start, read token       |                         |
 | open /#token=<token> -->|                         |
 |                         | GET / (fragment absent)|
 |                         |------------------------>|
 |                         | public static shell     |
 |                         |<------------------------|
 |                         | read token in memory    |
 |                         | history.replaceState()  |
 |                         | POST /api/auth/bootstrap|
 |                         | token in request body ->|
 |                         | Set-Cookie: HttpOnly    |
 |                         |<------------------------|
 |                         | authenticated HTTP/WS/SSE
```

前端先把 fragment 读入内存，用 `history.replaceState` 清除 `#token=...`，然后才发送
bootstrap 或其他数据请求。Fragment 不会进入 HTTP 请求、proxy access log、Referer
header 或 server route。Bootstrap endpoint：

1. 只接受 `POST`；
2. 要求有效的精确 Origin 和 Host；
3. 任何包含 `Authorization` header 的请求都被拒绝，并使用 body token 缺失或错误时的
   同一种 `401` response；
4. 要求 `Content-Type: application/json`、body 不超过 256 字节，并且内容只能是
   `{"token":"<43-character unpadded base64url>"}`；unknown key、duplicate key、错误
   base64url 和其他长度全部拒绝；
5. 解码为 32 字节后使用常数时间比较；
6. 成功时返回 `204` 和 cookie；
7. token 缺失或错误时返回同一种 `401` body；
8. 添加 `Cache-Control: no-store`，response 不含 token。

远程情况下显式执行：

```bash
openprogram web auth-url --base-url https://agent.example.com
```

命令先核对 worker PID/port 文件、`access.json` 与 listening process：向
`GET /api/auth/challenge` 发送新的随机 nonce，并在本机验证返回的 token-HMAC proof。Owner
token 不会发送到被探测端口。只有验证成功后，命令才读取 live token file，并只向调用它的
终端输出一个完整 fragment URL，不写入应用日志。`--base-url` 必须是冻结在
`access.json` 中的 effective canonical Origin，不得包含 path、query、fragment 或 user
information。只有实际 listener 是 loopback 时的精确 `localhost`，或属于 5.5 节显式
本地/overlay 地址范围的 IP literal 可以使用 HTTP；其他 DNS name 都必须使用 HTTPS。

### 5.4 Route 策略

只有以下请求可以不经过常规认证 middleware 而进入对应处理：

- 静态应用 shell 和不可变静态 asset；
- `POST /api/auth/bootstrap`，它自己执行 token 校验；
- `GET /api/auth/challenge?nonce=<43-character-base64url>`，可以附带
  `revision=<40-lowercase-hex>`；它在 Host 校验后只返回输入 nonce 的 versioned
  token-HMAC proof；
- `GET /healthz`，缩减为 `{"status":"ok"}` 之类不暴露身份信息的 liveness response。

Ownership challenge 只接受一个解码后为 32 字节的 unpadded-base64url nonce，最多再接受
一个 revision；revision 存在时必须等于当前进程提供的 40 字符小写 revision。成功 response
恰好是 `200 {"proof":"<43-character-base64url>"}`，decoded proof 等于
`HMAC-SHA256(key=raw_token_bytes,
msg=b"openprogram-web-challenge-v1\0" + raw_nonce_bytes + b"\0" +
revision_ascii)`。Ownership probe 不发送 credential；endpoint 构造 proof 时不使用 request
credential，不返回 token 或诊断字段，也不能代替正常请求认证。

其他 HTTP route、raw file response、provider route、diagnostic route、SSE stream 和
WebSocket Upgrade 都要求有效 cookie 或 Bearer token。详细 health 字段移到认证之后。
静态 HTML 不包含 token、配置 secret、session identifier、用户数据或动态 credential。

静态 shell 的 Content Security Policy 至少包含 `object-src 'none'`、
`base-uri 'none'` 和 `frame-ancestors 'none'`。可执行 script 仅限同源文件及构建生成的
hash 或 nonce，不允许第三方 script 和 `unsafe-eval`。Shell 同时返回
`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer` 和
`X-Content-Type-Options: nosniff`。

认证失败使用稳定且不区分具体原因的 response：

- credential 缺失或无效：HTTP `401`，body 为
  `{"error":"authentication_required"}`，并返回
  `WWW-Authenticate: Bearer realm="OpenProgram"`；
- Host、Origin 或浏览器请求上下文无效：HTTP `403`，body 为
  `{"error":"request_origin_rejected"}`；
- ownership challenge 格式错误或 revision 不匹配：HTTP `400`，body 为
  `{"error":"invalid_challenge"}`；
- 非 loopback 启动配置无效：不启动 listener。

Bootstrap 与 ownership-challenge response、认证失败、受保护 API response、
credential-status response 和 SSE response 都使用 `Cache-Control: no-store`。只有
content-addressed immutable static asset 使用长期缓存。

WebSocket 在 `websocket.accept` 之前完成认证和 Host/Origin 校验。Credential 缺失或无效
时返回 HTTP `401` Upgrade denial，而不是先接受 socket 再发送应用 close frame。

### 5.5 Host、Origin、CSRF 与传输矩阵

OpenProgram 从当前 profile 的 `<state-dir>/config.json` 读取 Web 配置。
`allowed_origins` 的值是完整 canonical Origin：

```json
{
  "web": {
    "host": "0.0.0.0",
    "allowed_origins": [
      "https://agent.example.com",
      "http://192.168.1.20:18100"
    ]
  }
}
```

每一项只能包含 `scheme://host[:port]`。Wildcard、path、query、fragment、user information、
opaque Origin 和错误 IPv6 都无效。只允许 `http` 与 `https` scheme。DNS name 进行 IDNA
与大小写规范化，IPv6 literal 使用方括号，默认端口被移除。
Unspecified 和 multicast IP literal 不能作为 Origin，因此 `0.0.0.0`、`::` 这类监听
地址不能出现在 `allowed_origins` 中。

Server 把已验证的配置 Origin 与严格受限的 loopback 默认值合并为
`effective_origins`。实际 listener 是 loopback 时，默认值包含
`http://localhost:<actual-port>`，以及实际监听 literal 形成的 Origin，例如
`http://127.0.0.1:<actual-port>` 或 `http://[::1]:<actual-port>`；没有实际监听的 literal
不加入。非 loopback listener 没有隐式 Origin。SSH forward 使用不同本地端口时，必须把
对应本地 Origin 显式加入 `allowed_origins`。任何请求 `Host` 都不能自动进入该集合。

OpenProgram 对每个请求要求恰好一个语法合法的 `Host` authority；重复、逗号拼接、包含
userinfo、unspecified address 或 multicast address 的形式都被拒绝。系统从浏览器等价
transport scheme 和解析后的 `Host` 构造 `request_origin`：`http`、`ws` 映射为
`http`，`https`、`wss` 映射为 `https`。只有 loopback peer 可以通过单一有效
`X-Forwarded-Proto` 值替换 scheme。
`request_origin` 必须属于 `effective_origins`。当 `Origin` header 必须存在或实际存在时，
其 canonical 值必须与 `request_origin` 相同；仅属于配置集合中的另一个 Origin 不够。这一
比较同时执行精确浏览器 Origin 和 DNS rebinding 校验。

安全 method 仅包含 `GET`、`HEAD` 和 `OPTIONS`。所有这些 route 都不得改变状态；`HEAD`
只返回对应 `GET` 的 header，`OPTIONS` 只返回协议 metadata。所有 mutation 使用 `POST`、
`PUT`、`PATCH` 或 `DELETE`。

| 请求形式 | Credential | Origin 规则 | Host 规则 |
|---|---|---|---|
| Cookie、非安全 HTTP method | 必须 | 必须精确匹配 `request_origin`；缺少 Origin 时拒绝 | `request_origin` 必须有效 |
| Cookie、WebSocket | accept 前必须 | 必须精确匹配 `request_origin`；缺少 Origin 时拒绝 | `request_origin` 必须有效 |
| Cookie、安全 HTTP/SSE | 必须 | 显式 Origin 必须等于 `request_origin`；同源 navigation 可以省略 | `request_origin` 必须有效 |
| Bearer HTTP/SSE | 必须 | 可以省略；存在时必须等于 `request_origin` | `request_origin` 必须有效 |
| Bearer WebSocket | accept 前必须 | 原生客户端可以省略；存在时必须等于 `request_origin` | `request_origin` 必须有效 |
| Listener ownership challenge | 无 credential；只允许有界 nonce 与可选 revision | 可以省略；存在时必须等于 `request_origin` | `request_origin` 必须有效 |
| Fragment bootstrap | Body 中的 token | 必须精确匹配 `request_origin` | `request_origin` 必须有效 |

`Sec-Fetch-Site: cross-site` 继续作为浏览器请求的拒绝信号。CORS header 控制浏览器代码
能否读取 response，不用于认证。即使值为 same-site，也仍然需要 token、Host 和对应的
Origin 规则。

直接非 loopback 监听必须配置至少一个有效 Origin，否则 fail closed。只有 `ipaddress`
确认地址属于以下显式网络时才允许直接 HTTP：IPv4 loopback `127.0.0.0/8`；RFC 1918
的 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`；IPv4 link-local
`169.254.0.0/16`；RFC 6598 shared space `100.64.0.0/10`；IPv6 loopback `::1/128`；
IPv6 ULA `fc00::/7`；IPv6 link-local `fe80::/10`。Origin 不接受 IPv6 zone identifier。
这里使用显式 allowlist，不使用 `is_private`，因为后者包含不可用或保留地址，同时排除
RFC 6598。Unspecified、multicast、documentation、benchmarking、reserved 和公网可路由
地址都不能使用 HTTP。

每个非 loopback HTTP Origin 都输出明确启动警告，因为网络观察者能读取 bearer
credential。RFC 6598 用于所有者配置的 Tailscale 等加密 overlay，但该地址本身不能证明
传输已经加密；所有者必须只在加密 overlay 内使用。传输保护不确定时应使用 HTTPS。
实际 listener 是 loopback 时的精确 `localhost` 是唯一 HTTP DNS-name 例外；其他 DNS
Origin 都必须使用 HTTPS。

### 5.6 反向代理信任

同机 nginx 或 Caddy 可以终结 HTTPS，OpenProgram 继续监听 `127.0.0.1`。Web server 使用
`proxy_headers=False` 启动 Uvicorn，避免 Uvicorn 在 OpenProgram 判断前改写 ASGI client
或 scheme。OpenProgram 不信任 `X-Forwarded-For`；只有原始 immediate peer 是 loopback
时才信任 `X-Forwarded-Proto`。该 header 必须只包含一个 `http` 或 `https` 值；list 和
其他值均拒绝。任何非 loopback peer 提供的 forwarded header 都被忽略。Proxy 保留公网
Host、覆盖 effective scheme、支持 WebSocket Upgrade，并为 SSE 关闭 response buffering。
所得 `request_origin` 仍必须匹配 effective Origin。DNS 或公网 IP Origin 的 bootstrap 在
可信 effective scheme 不是 HTTPS 时返回 `403`，因此 proxy 漏传 header 不会生成不安全
owner cookie。Proxy 不能代替实例 token。

同机反向代理下，OpenProgram 配置仍保持 loopback：

```json
{
  "web": {
    "host": "127.0.0.1",
    "allowed_origins": ["https://agent.example.com"]
  }
}
```

最小 nginx 配置：

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name agent.example.com;

    # ssl_certificate and ssl_certificate_key are owner-managed.

    location / {
        proxy_pass http://127.0.0.1:18100;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

最小 Caddy 配置：

```caddyfile
agent.example.com {
    reverse_proxy 127.0.0.1:18100
}
```

Caddy 管理证书只是所有者选择 Caddy 的结果；OpenProgram 不调用 Caddy 的证书 API。
两个示例中的配置 Origin 都是 `https://agent.example.com`，OpenProgram backend 保持
loopback。

### 5.7 直接访问与隧道示例

SSH 隧道：

```bash
ssh -N -L 18100:127.0.0.1:18100 owner@remote-host
```

OpenProgram 仍监听 `127.0.0.1`。隧道建立后，所有者在远端主机执行：

```bash
openprogram web auth-url --base-url http://127.0.0.1:18100
```

该示例的本地与远端端口相同，因此隐式 loopback Origin 生效。如果本地端口不同，执行
`auth-url` 前必须把对应 `http://localhost:<local-port>` 或 loopback literal Origin 加入
`allowed_origins`。

局域网或 VPN 直接地址需要同时显式配置 bind 和 Origin：

```json
{
  "web": {
    "host": "0.0.0.0",
    "allowed_origins": ["http://192.168.1.20:18100"]
  }
}
```

浏览器使用 `allowed_origins` 中的地址；`0.0.0.0` 只是 bind address，不能作为浏览器
Origin。

### 5.8 Secret 处理

Provider credential 在 Web UI 中只允许录入，不允许读取原文：

- `GET /api/config/key/{env_var}` 在未设置时只返回
  `{"has_value":false,"masked":""}`，已设置时只返回
  `{"has_value":true,"masked":"sk-…abc4"}`，其中 mask 使用实际值计算；`env_var`
  必须是已声明的 provider 或 search credential name，未知名称返回 `404`；
- `GET /api/providers/{provider}/accounts` 保留非 secret account metadata，但 API-key
  credential 只用 `has_value` 与 `masked_key` 表示；不返回 `identity`、`can_reveal`、
  `value` 或任何 full-key field。

两种存储形式使用不同的 mutation 契约：

| 目标 | 保留 | 替换 | 删除 |
|---|---|---|---|
| Config/environment key | 从 `api_keys` map 中省略该名称 | `POST /api/config`，body 恰好是 `{"api_keys":{"ENV_VAR":"new printable-ASCII value"}}` 或包含多个 key 的同类 map；列出的 value 必填，成功返回 `200 {"saved":true}` | `DELETE /api/config/key/{env_var}`，无 body；幂等删除保存项和当前进程 environment value，返回 `204` |
| API-key account | use、rename、reorder、rotation 等 account metadata operation 不接受也不修改 `api_key` | `POST /api/providers/{provider}/accounts/{name}/update`，body 恰好是 `{"api_key":"new printable-ASCII value","validate":true}`；`validate` 是唯一可选 field，默认 `true`；成功响应恰好是 `200 {"ok":true}` | `POST /api/providers/{provider}/accounts/remove`，body 恰好是 `{"id":"account-name"}`，删除整个 credential pool；成功返回 `200 {"removed":true,"name":"account-name","cleared_active":<bool>}` |

Unknown field、未知 credential name、`null`、空字符串、non-printable/non-ASCII value
和任何界面 mask 都在 mutation 前返回 `400`。替换 key 时，如果 validation 明确拒绝该
credential，则返回 `400`；临时或 offline `unknown` validation 不阻止保存。Account
replacement 在 provider/account 不存在或不是 API-key account
时返回 `404`。Account deletion 的 `id` 缺失或不存在时返回 `404`。删除 config key 也会
移除当前进程的 environment value；parent environment 再次提供的值只能在后续进程重启后
重新出现。

Provider detail、API-key settings 和 account manager 删除所有 reveal button 与 reveal
request。Backend 不把界面显示的掩码解释成 secret 值。

只有长度至少为十二个字符的值才使用前三个 ASCII 字符、U+2026 和末四个字符作为稳定
mask，保证至少隐藏五个字符。长度不足十二的值，以及可见字符不是 ASCII 的值，统一显示
`••••••••`，不会通过 mask 编码短 credential 的原始长度。Mask 只用于显示，write payload 永远不接受它。

Account reveal route 整体删除并返回 `404`。Config-key 掩码状态 route 保留，但带
`reveal` query parameter 的请求返回 `404`，不能改变为明文 response。无关的
project-file reveal action 继续受文件权限和 Web 认证控制；它不是 credential retrieval
endpoint。

## 6. 实现契约与验收测试

### 6.1 启动契约

Server 依次完成配置验证、token 生成与安全保存、冻结 `access.json` snapshot 的写入与验证、
fingerprint 计算，然后才接受连接。启动日志记录：

- 实际 bind address，以及它是否为 loopback；
- 配置的公网 Origin；
- 是否启用仅信任 loopback proxy 的 scheme 处理；
- token fingerprint；
- 直接非 loopback HTTP 警告。

日志不记录 token。Malformed Origin、没有 Origin 的非 loopback bind、不安全的公网 HTTP
Origin、不安全 token 文件或已被占用的 Web-process lock 都会导致启动错误。
Listener 启动前，server 配置关闭 Uvicorn proxy-header 改写；只有 OpenProgram 的共同
ASGI policy 可以解释原始 peer 和 `X-Forwarded-Proto`。

### 6.2 请求处理顺序

共同 ASGI 处理顺序是：

```text
request
  -> immediate peer, trusted effective scheme, canonical Host/request_origin
  -> route + method + credential-source classification
  -> Origin / Sec-Fetch-Site / CSRF policy
  -> public, ownership-challenge, bootstrap, cookie, or Bearer rule
  -> owner authority attachment
  -> HTTP route, SSE generator, or WebSocket accept
```

公共静态 route 可以省略 credential authentication，但不能省略 Host 和浏览器上下文校验。
Bootstrap route 用自身的常数时间 body-token exchange 替代共同 credential check。
Ownership-challenge route 只执行有界 nonce/revision proof 契约，不授予 authority。其他
应用 route 不得各自定义第二种认证解释。

### 6.3 必需测试

只有以下行为都由可执行测试覆盖，功能才算完成：

1. Loopback HTTP、SSE、WebSocket 在没有 credential 时失败。
2. 正确 Bearer token 可以在无 `Origin` 时成功；错误 token 返回同一 `401` 结构且不执行
   动作。
3. Browser bootstrap 在其他 fetch 前清除 fragment，只接受有界的精确 JSON schema，不在
   URL/query/Referer 发送 token，按规定 HMAC 派生 cookie，拒绝 body token 与
   Authorization 同时存在的请求，随后可以连接 HTTP、SSE 和 WS。
4. Cookie 认证的非安全 HTTP 与 WebSocket 对 missing、opaque、cross-site 和未列出的
   Origin 均在执行动作或接受 socket 前拒绝。
5. Loopback、直接外部监听和反向代理部署都拒绝外部、重复、逗号拼接、unspecified 或
   multicast Host；HTTP/WS 和 HTTPS/WSS 分别生成相同的浏览器等价 Origin。
6. 默认 loopback 只接受隐式 `localhost` 与实际监听 literal Origin；SSH 使用其他本地端口
   时，未显式配置之前必须拒绝。
7. 非 loopback bind 没有非空有效 Origin list 时拒绝启动；显式本地/overlay HTTP 地址
   范围带警告接受，unspecified、multicast、documentation、benchmarking、reserved 和
   global literal 的 HTTP 被拒绝。
8. 关闭 Uvicorn proxy-header 改写；只接受原始 loopback peer 的 forwarded scheme，忽略
   所有非 loopback peer 的值；伪造 `X-Forwarded-For` 不能改变信任，公网 bootstrap 的
   effective scheme 不是 HTTPS 时拒绝，direct WS 与 Caddy proxy WSS 都生成预期的浏览器
   等价 Origin。
9. 同一 state directory 的第二个 Web 进程不能修改 live token 或 access snapshot；bind
   失败只能清理失败进程自身持有的文件。
10. 重启会更换 token，使旧 Bearer 和 cookie 都失效。
11. 错误或 malformed Authorization header 不能回退到有效 cookie。
12. 静态 asset 和缩减后的 liveness response 不包含进程 token、session 数据、filesystem
   数据、credential 数据或详细诊断。
13. Security header 阻止 framing；protected/auth/credential response 使用 `no-store`，
    `401` response 声明 Bearer realm。
14. 两种 reveal 请求返回 `404`；config-key replace/preserve/DELETE 与 account
    replace/preserve/remove 执行上文精确 schema 和 status code；8–11 字符 credential 使用
    固定 mask，mask 永远不能写入，frontend build 和 type 中没有 reveal action 或
    full-secret response field。
15. nginx 与 Caddy smoke deployment 可以通过 HTTPS 传输已认证 HTTP、SSE、WebSocket，
    backend 保持 loopback。
16. Channel message 保留 paired authority tier，不继承 Web owner authority。
17. 同一 loopback hostname、不同端口上的两个 profile server 使用不同 cookie 名称，独立
    认证并忽略对方 cookie。
18. Listener ownership probe 同时核对 worker PID/port 与冻结 access snapshot，只发送新的
    32 字节 nonce，验证精确的 versioned HMAC proof，可以把 proof 绑定到 served revision，
    禁用 ambient HTTP proxy，并且绝不发送或记录 owner token。Foreign listener、stale
    snapshot，以及 fingerprint、port、proof 或 revision 不匹配时，不能判定为当前 owner
    server。

## 7. 实现进度

设计陈述不能作为实现证据。以下状态只依据当前生产路径和测试。

### 已实现

| 项目 | 证据 |
|---|---|
| 默认 loopback bind | `openprogram/webui/server.py` 中的 `_web_config()` 默认使用 `127.0.0.1` |
| FastAPI lifespan | `create_app()` 使用 `_lifespan`，不存在已弃用的 `@app.on_event` handler |
| 稳定的 per-profile owner principal 与显式 owner/paired authority tier | `openprogram/agent/authority.py`；Web、TUI、desktop、runtime 和已配对 channel 入口附加 tier；`tests/unit/providers/test_authority_scope.py` 与 permission 测试覆盖固定档位表 |
| Owner 进程 credential | `openprogram/webui/owner_auth.py` 中的 `OwnerAuthState` 生成 32 字节 token、持有 `<state-dir>/web.lock`、原子写入 owner-only `<state-dir>/web/token`、派生 profile-specific cookie、使用 `hmac.compare_digest` 比较解码后的 token，并且只清理自己拥有的状态；`test_process_token_is_owner_only_locked_and_replaced_after_release` 覆盖 lock、mode、替换、repr 隐去 token 和 release 后轮换 |
| Canonical effective Origin | `canonicalize_origin()` 与 `resolve_effective_origins()` 验证精确 Origin、执行显式 HTTP 网段限制、加入有限 loopback 默认值，并在非 loopback bind 没有 Origin 时失败；参数化 owner-auth 测试覆盖接受与拒绝的输入 |
| 共同 owner-auth 边界 | `create_app()` 在 route 前安装 `OwnerAuthMiddleware`，保护 HTTP 和 WebSocket ASGI scope，并执行 cookie/Bearer 选择、Host/Origin/CSRF 校验、通用 `401`/`403`、no-store 与 owner-authority 附加；owner-auth 测试覆盖 HTTP mutation 与 accept 前 WebSocket |
| Fragment bootstrap backend 与 frontend coordinator | `POST /api/auth/bootstrap`、`apps/web/lib/net/owner-auth-bootstrap.ts` 和 `OwnerAuthBoundary` 已实现 body-token 交换、同步清除 fragment、禁止 Web Storage 与应用挂载 gate；`apps/web/scripts/check-owner-auth-bootstrap.mjs`、TypeScript 检查和 production Web build 验证 frontend 契约 |
| 认证 URL 命令 | `openprogram web auth-url --base-url ...`、`build_owner_auth_url()` 与 `tests/unit/providers/test_web_auth_url.py` 覆盖 active-server 查找和 effective-Origin 校验；正常 CLI browser launch 使用同一 fragment URL |
| 最小公共 liveness | `/healthz` 只返回 `{"status":"ok"}` 并带 `no-store`；运维字段位于受保护的 `/api/diagnostics`；integration 与 owner-auth 测试覆盖两条 route |
| Raw-peer proxy 边界 | Uvicorn 使用 `proxy_headers=False`；`OwnerAuthMiddleware` 只接受 immediate loopback peer 的单一 forwarded scheme，并测试 HTTPS Origin 匹配 |
| Secret 不可取回 | 两种明文 reveal 形式都已删除：account reveal route 整体移除，`GET /api/config/key/{env_var}?reveal=1` 返回 `404`；`_credential_secrets` 提供统一掩码与 declared-name 校验；`/api/config`、`/api/settings`、`/api/config/verify`、`DELETE /api/config/key/{env_var}` 和 account route 只接受各自的精确 bounded schema，且不返回完整 secret；frontend 不存在 reveal action、control 或 response type。MCP 服务器凭证遵循同一套契约：`MCPServerConfig.to_storage_dict()` 只用于写配置文件、保留完整值，`to_response_dict()` 把每一个 `env` 和 `headers` 值以及 bearer token、OAuth client secret 都替换成掩码，因此 `/api/mcp/servers`、`/api/mcp/servers/{name}`、`/api/mcp/catalog`、`/api/mcp/catalog/diff` 返回的是 `{has_value, masked}` 而不是值；`PATCH /api/mcp/servers/{name}` 采用 preserve（未提交）/ replace（提交新值）/ delete（显式空值）语义，且只在 restart 成功后才落盘；`mcp_servers.json` 经临时文件、`fsync`、`os.replace` 以 `0600` 写入；`tests/component/programs/test_mcp_secret_non_retrievability.py` 与 `apps/web/scripts/check-secret-non-retrieval.mjs` 分别覆盖响应、权限、preserve 语义和前端显示 |
| 内部客户端认证 | `resolve_backend_endpoint()` 返回经 challenge 验证的 `BackendEndpoint`（base URL、WebSocket URL、Origin 和 token），credential 只在 listener 证明持有同一 token 之后才读取；`cli/ink.py` 将其传入 TUI 环境，`cli/commands/mcp.py` 用于 MCP CLI，Node 客户端只对 backend URL 发送 Bearer header（`apps/cli/src/utils/backend.ts`、`apps/cli/tests/backendAuth.test.ts`） |
| 启动输出 | `start_server()` 打印 bind 地址、binding scope、effective Origin、forwarded-scheme 信任边界和 token fingerprint，并在 effective Origin 是非 loopback 明文 HTTP 时告警；`test_startup_logs_warn_about_plaintext_http_for_remote_origins` 逐项断言 |
| 真实 listener transport 验收 | `tests/component/providers/test_web_owner_auth_listener.py` 在 ephemeral 端口绑定真实 Uvicorn socket，覆盖带认证与无认证 HTTP、带 `no-store` 的 SSE 认证、accept 前以 `401` 加 Bearer-realm/`no-store` header 拒绝的原始 WebSocket handshake、bind-failure 清理、wire 层 token 轮换、双 profile cookie 隔离、reverse-proxy Origin 矩阵，以及证明 token 不出现在任何 response body、header、日志和渲染页面中的全面扫描 |

### 部分实现

| 项目 | 已实现部分 | 缺失部分 |
|---|---|---|
| Browser-level audit | Shell 带 CSP、frame、referrer、content-type 与 cache header，server 端测试断言 response 和渲染 HTML 不含 token | 尚无 browser 驱动的 audit 遍历所有导出 asset 与 navigation path 证明其不嵌入 dynamic data |
| 部署运维 | Reverse-proxy 契约由真实 listener 的 `X-Forwarded-Proto`/`X-Forwarded-Host` Origin 矩阵覆盖，raw-peer 边界已强制 | nginx 与 Caddy 的 HTTPS/WS/SSE smoke deployment 未测试 |

### 明确不做

| 项目 | 边界 |
|---|---|
| Web 账号、signup、邀请和用户 session | 一个当前 profile/state 实例只有一个 owner principal |
| RBAC、group、tenant/workspace role 和项目权限 | Web UI 是一个全 capability owner 管理界面 |
| OAuth/OIDC/SSO 和 identity-aware proxy 认证 | 不能代替强制实例 token |
| 内置 TLS、ACME、证书存储和续期 | 所有者运维 nginx、Caddy、VPN 或 SSH |
| 公网 relay、托管 tunnel、发现、pairing service 和公共 share URL | OpenProgram 不为 Web 可达性做外部注册 |
| 关闭认证或 `--insecure` 模式 | 包括 loopback 在内，token 认证始终开启 |
| Query 参数、localStorage 或公共 HTML 中的长期 token | Fragment bootstrap 和 HttpOnly cookie 是唯一浏览器初次认证方式 |
| 读取已保存 provider credential 明文 | 支持替换，不支持 reveal |
