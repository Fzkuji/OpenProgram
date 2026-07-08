# claude-code 直连订阅(砍掉 Meridian)

## 目标

把 `claude-code` provider 从「本地 Meridian 代理 daemon」改成「anthropic SDK 用订阅
OAuth token 直连 `api.anthropic.com`」——与 `openai-codex` 直读 `~/.codex/auth.json`
+ 直连 `chatgpt.com/backend-api` 完全同构。

约束(用户拍板):
- **保留 `claude-code` 这个 provider 名字**(WebUI/CLI 不变),只换底层 Runtime。
- **不碰 macOS 钥匙串**,走 OpenProgram 自己的凭证体系(AuthStore +
  `~/.claude/.credentials.json` 文件)。

## 背景:为什么 Meridian 不是技术必需

`anthropic.py:245-261` 早已支持订阅 OAuth 直连:当 token 是 `sk-ant-oat…` 时,用
`auth_token=<token>` + `anthropic-beta: claude-code-20250219,oauth-2025-04-20,…` +
`user-agent: claude-cli/<ver>` 直发 Messages API。这跟 codex 直连 chatgpt.com
是同一套思路。Meridian 当初被选中的两个理由现已不成立:

- 「Max 账号不暴露 api.anthropic.com key」——对,但订阅用的是 **OAuth token**,不是
  api-key;直连走 Bearer + beta header,不需要 api-key。
- 「第三方代理把 image block 弄坏成 `[object Object]`」——那是某个代理的 bug;走官方
  `anthropic` SDK 直发 Messages API 原生支持 image block,多模态不丢。

## 真正的障碍:token 怎么进来 + 怎么 refresh

| 凭证形态 | 来源 | kind | refresh |
| --- | --- | --- | --- |
| 旁观 Claude CLI | `~/.claude/.credentials.json` | `cli_delegated` | Claude CLI 自刷,OpenProgram 旁观重读 |
| 自持 api-key | `openprogram auth login anthropic --api-key` | `api_key` | 不过期 |

现状两个洞:
1. anthropic provider 解析 token 用 `resolve_provider_key()`,它**显式排除 OAuth**
   (`env_api_keys.py:52` 注释:OAuth 走 claude-code daemon)。
2. resolver 的 `_extract_token()` 对 `cli_delegated` 返回 None
   (`resolver.py:132-137`),即使改调 `resolve_api_key_sync` 也取不出订阅 token。

## 方案(轻量,白蹭 Claude CLI 刷新 —— 与 codex 一字不差)

codex 的 cli_delegated 模式:codex CLI 维护 `~/.codex/auth.json`,OpenProgram 每次
重读取最新 access_token。我们对 Claude 照搬:Claude CLI 维护
`~/.claude/.credentials.json`(Linux/Win 是文件,直读;mac 钥匙串不碰),OpenProgram
每次重读 `claudeAiOauth.accessToken`。

### 改动点

1. **resolver 补 cli_delegated 取 token** —— `auth/resolver.py:_extract_token`
   对 `CliDelegatedPayload` 重读 `store_path`,按 `access_key_path` 取出 access_token。
   这是通用修复(codex 的 cli_delegated 也受益)。

2. **anthropic provider 改用统一解析** —— `providers/anthropic/anthropic.py`
   `stream_simple` 里 token 解析从 `resolve_provider_key(provider)` 换成
   `resolve_api_key_sync(provider)`(含 OAuth/cli_delegated/manager-refresh)。
   `AnthropicRuntime.__init__` 同改。

3. **registry 切换** —— `providers/registry.py` 把
   `"claude-code"` 从 `_max_proxy_runtime.ClaudeCodeRuntime` 换成直连 Runtime。
   保留 `claude-code` 名字:新增一个轻 Runtime,模型走 `anthropic:<id>` namespace
   (复用 anthropic provider 的 wire),token 从 `anthropic` pool 解析。
   normalize 模型 alias(opus/sonnet/haiku)沿用。

4. **expiry 处理** —— cli_delegated 过期时 AuthManager 抛 `AuthReadOnlyError`
   (read-only 不能自刷),错误信息引导用户 `claude login`。直连路径沿用,不另做。

### 验证

- 单元:`_extract_token` 对 cli_delegated 重读文件取 token;anthropic provider 在
  只有 cli_delegated 凭证时能解析出 `sk-ant-oat` token;registry 解析 `claude-code`
  得到直连 Runtime 而非 Meridian Runtime。
- 端到端:本机 `claude login` 后,OpenProgram 跑 claude-code 一次,确认直连
  api.anthropic.com(无 Meridian 进程),多模态 image block 正常。

## 实现状态(已落地)

1. ✅ `auth/resolver.py:_extract_token` + 新 `_read_delegated_token`:cli_delegated
   重读 `store_path` 取 access_token(codex 的 cli_delegated 也受益)。
2. ✅ `providers/anthropic/anthropic.py:stream_simple` + `runtime.py:AnthropicRuntime`
   改用 `resolve_api_key_sync`(含 OAuth/cli_delegated)。
3. ✅ 新 `providers/anthropic/_claude_code_direct_runtime.py`:ClaudeCodeRuntime
   直连,模型映射到 `anthropic:<id>`,token 从 anthropic pool 解析。
4. ✅ `providers/registry.py`:`claude-code` → 直连 Runtime(旧 Meridian Runtime
   仅残留可 import)。
5. ✅ 测试:`tests/unit/test_claude_code_direct_oauth.py`(10)新增;
   `test_runtime_key_ladder.py` mock 点更新(AnthropicRuntime 走统一解析)。
   全量 unit 810 passed / 4 skipped。

注:`api="claude-code-cli"` 这个 wire 标签只在 `_claude_code_registry.py` 声明、
全仓无消费者(悬空标签),实际请求恒走 Runtime → `anthropic:<id>` Messages wire,
所以切换不触及任何 wire 实现。Meridian 的 `x-meridian-profile` header 注入挂在
openai_completions chokepoint,直连模型 api 是 anthropic Messages,天然不经过那里。

## 订阅登录(浏览器 OAuth + setup-token)—— 已落地

直连只解决"有 token 时怎么用",登录解决"token 怎么进来"。照搬 codex 的
PKCE 框架给 claude-code 接订阅登录:

- **OAuth 参数(实测可用)**:`auth_adapter.py` 加 `OAUTH_CLIENT_ID`
  =`9d1c250a-e61b-44d9-88ed-5944d1962f5e`、authorize=`claude.ai/oauth/authorize`、
  token=`console.anthropic.com/v1/oauth/token`、redirect=`console.anthropic.com/oauth/code/callback`。
  `build_pkce_config()` 用 manual-paste 模式(Anthropic 是 hosted-redirect 显示
  `code#state`,不是 loopback callback)+ token JSON。
- **通用 PKCE 框架扩展**:`pkce_oauth.py` 加 `manual_paste_only`/`redirect_uri_override`/
  `token_use_json` 三个开关 + `_credential_from_tokens` 抽取 + exchange 带 state。
- **refresh**:`_anthropic_refresh`(refresh_token 换新,JSON),注册到 ProviderAuthConfig;
  无 refresh_token 的(setup-token)自动 no-op。
- **setup-token**:`import_setup_token` 存 oauth kind、空 refresh_token、~1y 过期。
- **登录方式**:`login_methods` 里 anthropic + claude-code 只留
  `pkce_oauth`(默认) + `setup_token` —— **不要 import_from_cli、不要 api_key**
  (用户明确废弃从 ~/.claude 导入)。
- **driver**:`login_driver` 加 anthropic pkce 分支 + setup_token 分发;
  `_credential_provider_id`(claude-code→anthropic)保证凭证落 anthropic pool。
- **多账号**:每账号一个 profile,复用统一账号管理 + 429 轮换。

## WebUI:claude-code 脱离 Meridian 账号体系 —— 已落地

claude-code 的账号 UI 原本被前后端硬编码到 Meridian 专属路由,绕过通用登录。
切换:

- `webui/routes/providers.py`:删整块 `/api/providers/claude-code/accounts/*`
  字面量路由(原调 `_meridian_cli`,字面量路由优先级高会截走请求)。
- `webui/routes/accounts.py`:加 `_pool_id`(claude-code→anthropic),删 5 处
  `if provider=="claude-code"` 短路,所有通用路由按 pool 存取;`_api_key_env`
  对 claude-code 返回 ""(强制 add_mode=login,不显示 key-paste)。
- `setup_hints.py`:claude-code 文案改成"直连 Anthropic 用订阅 OAuth",删
  backend/Meridian 描述,说明两种登录。
- 前端 `account-manager.tsx`/`provider-login.tsx` 数据驱动,零改动:后端
  add_mode=login → 自动渲染两个登录按钮。已 build + worker restart + 浏览器自查
  确认界面正确(两个按钮、无 Import、文案对)。

`_meridian_cli.py`/`_max_proxy_runtime.py` 等保留在磁盘但不再被任何路由调用。

## Meridian 残留处置

`_max_proxy_runtime.py` / `_claude_max_proxy_registry.py` / `_meridian_cli.py` 暂留
(WebUI 的「添加 Claude 账号」P1/P2 仍引用),仅 registry 不再默认指向它。后续若
确认无引用再删。
