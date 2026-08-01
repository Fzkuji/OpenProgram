# claude-code 直连订阅

`claude-code` provider 用 anthropic SDK、以订阅 OAuth token 直连
`api.anthropic.com` —— 与 `openai-codex` 同构：后者直读 `~/.codex/auth.json`
并直连 `chatgpt.com/backend-api`。路径中没有本地代理 daemon。

两条约束决定了设计形态：

- provider 在 WebUI 和 CLI 中的名字是 `claude-code`。只有底层 Runtime 改为
  Anthropic 直连，对用户可见的名字不变。
- 凭证存放在 OpenProgram 自己的体系里（AuthStore 加
  `~/.claude/.credentials.json` 文件）。不触碰 macOS 钥匙串。

## 为什么不需要代理

`anthropic.py:245-261` 直接支持订阅 OAuth：当 token 是 `sk-ant-oat…` 时，请求以
`auth_token=<token>`、`anthropic-beta: claude-code-20250219,oauth-2025-04-20,…`、
`user-agent: claude-cli/<ver>` 发往 Messages API。这与 codex 直连 chatgpt.com
是同一套做法。

曾经支持代理方案的两条理由都不成立。Max 账号不暴露 `api.anthropic.com` key 属实，
但无关紧要 —— 订阅用的是 **OAuth token** 而非 api-key，直连只需要 Bearer token
加 beta header。而 image block 变成 `[object Object]` 是某个特定代理的 bug；官方
`anthropic` SDK 对 Messages API 原生支持 image block，多模态内容不会丢失。

## 凭证形态

| 凭证形态 | 来源 | kind | refresh |
| --- | --- | --- | --- |
| 旁观 Claude CLI | `~/.claude/.credentials.json` | `cli_delegated` | Claude CLI 自刷，OpenProgram 旁观重读 |
| 自持 api-key | `openprogram auth login anthropic --api-key` | `api_key` | 不过期 |

`cli_delegated` 模式与 codex 完全一致。codex CLI 维护 `~/.codex/auth.json`，
OpenProgram 每次使用时重读最新的 access_token；同样地，Claude CLI 维护
`~/.claude/.credentials.json`（在 Linux/Windows 上是普通文件，直接读取），
OpenProgram 每次使用时重读 `claudeAiOauth.accessToken`。刷新由外部 CLI 负责，
这正是这套模式成本低廉的原因。

## 机制

**token 提取。** `auth/resolver.py:_extract_token` 对 `CliDelegatedPayload`
重读 `store_path`，按 `access_key_path` 取出 access_token。这是针对该凭证 kind
的通用实现，codex 的 `cli_delegated` 走同一条路径。

**anthropic provider 使用统一解析。** `providers/anthropic/anthropic.py` 中的
`stream_simple` 与 `AnthropicRuntime.__init__` 都通过
`resolve_api_key_sync(provider)` 解析 token，它涵盖 OAuth、`cli_delegated`
以及 manager 驱动的刷新。

**registry。** `providers/registry.py` 把 `"claude-code"` 映射到直连 Runtime。
该 Runtime 很轻：模型走 `anthropic:<id>` namespace，复用 anthropic provider 的
wire，token 从 `anthropic` pool 解析。模型 alias 归一化（opus / sonnet / haiku）
沿用。

**过期。** `cli_delegated` 凭证过期时，AuthManager 抛出 `AuthReadOnlyError`
—— 该凭证是只读的，无法自行刷新 —— 错误信息引导用户执行 `claude login`。
直连路径复用这套处理，而不另做一份过期逻辑。

`api="claude-code-cli"` 这个 wire 标签声明在 `_claude_code_registry.py` 中且无
消费者；请求恒经 Runtime 走 `anthropic:<id>` Messages wire，因此不涉及任何 wire
实现。

## 订阅登录

直连解决的是"有 token 时怎么用"，登录解决的是"token 怎么进来"。claude-code
使用与 codex 相同的 PKCE 框架。

- **OAuth 参数**位于 `auth_adapter.py`：`OAUTH_CLIENT_ID` =
  `9d1c250a-e61b-44d9-88ed-5944d1962f5e`、authorize =
  `claude.ai/oauth/authorize`、token = `console.anthropic.com/v1/oauth/token`、
  redirect = `console.anthropic.com/oauth/code/callback`。`build_pkce_config()`
  使用 manual-paste 模式，因为 Anthropic 是 hosted redirect、显示 `code#state`
  而不是 loopback callback，另加 token JSON。
- **共享 PKCE 框架**为此带有三个开关 —— `manual_paste_only`、
  `redirect_uri_override`、`token_use_json` —— 以及 `_credential_from_tokens`
  抽取和带 state 的 exchange，都在 `pkce_oauth.py` 中。它们是对框架的泛化，
  而不是在框架内部为 Anthropic 开特例。
- **refresh** 是 `_anthropic_refresh`（refresh_token 换新，JSON），注册到
  ProviderAuthConfig。没有 refresh_token 的凭证（例如 setup-token）自动 no-op。
- **setup-token** 走 `import_setup_token`，存入 oauth kind、空 refresh_token、
  约一年的过期时间。
- anthropic 与 claude-code 的**登录方式**只有 `pkce_oauth`（默认）和
  `setup_token`。不提供从 `~/.claude` 导入，也不提供粘贴 api key。
- **driver**（`login_driver`）带一个 anthropic pkce 分支和 setup_token 分发；
  `_credential_provider_id` 把 claude-code 映射到 anthropic，保证凭证落入
  anthropic pool。
- **多账号**每账号一个 profile，复用统一账号管理与 429 轮换。

## WebUI 中的账号管理

claude-code 的账号走通用账号路由，而非 provider 专属路由。
`webui/routes/accounts.py` 通过 `_pool_id` 把 claude-code 映射到 anthropic pool，
于是所有通用路由都按 pool 存取；`_api_key_env` 对 claude-code 返回 `""`，
从而强制 `add_mode=login` 并隐藏 key 粘贴框。`setup_hints.py` 把该 provider
描述为"以订阅 OAuth 直连 Anthropic"，并说明两种登录方式。

前端不需要 claude-code 分支：`account-manager.tsx` 与 `provider-login.tsx`
是数据驱动的，`add_mode=login` 会自动渲染出两个登录按钮。

## 实现状态

上述直连、订阅登录与 WebUI 账号管理均已就位：

- `auth/resolver.py:_extract_token` 加 `_read_delegated_token` 对
  `cli_delegated` 重读 `store_path`。
- `providers/anthropic/anthropic.py:stream_simple` 与
  `runtime.py:AnthropicRuntime` 通过 `resolve_api_key_sync` 解析。
- `providers/anthropic/_claude_code_direct_runtime.py` 中是直连的
  ClaudeCodeRuntime。
- `providers/registry.py` 把 `claude-code` 指向它。
- `tests/unit/test_claude_code_direct_oauth.py` 覆盖该路径；
  `test_runtime_key_ladder.py` 的 mock 点指向统一解析。

`_max_proxy_runtime.py`、`_claude_max_proxy_registry.py`、`_meridian_cli.py`
仍在磁盘上，但已不被任何路由触达，registry 也不再引用它们。待确认 WebUI
"添加 Claude 账号"处的残余引用消失后即可删除。
