# 自包含的认证存储

每个 provider 的凭据都存放在 `~/.openprogram` 下、由 OpenProgram 自己管理，
而不是每次使用时去读其他 CLI 的凭据文件（`~/.codex/auth.json`、
`~/.claude/.credentials.json`、`~/.gemini`、`~/.qwen`、`~/.config/gh`）。
一个存储、一套登录流程，在 CLI、web、TUI 中保持一致。

## 存储

`openprogram/auth/store.py` 实现了 `AuthStore`，位于
`~/.openprogram/auth/<provider_id>/<profile_id>.json` —— 0600 权限、原子的
write→fsync→replace、跨进程 `flock`，以及内存中的 mtime/size 监视，使得文件在
底层被改动后会重新加载。`openprogram/auth/types.py` 定义了凭据 kind：

| kind | 密钥存储方式 |
|---|---|
| `api_key` | key 的副本 |
| `oauth` | 副本：access + refresh + `expires_at_ms` + client_id + token_endpoint |
| `device_code` | 副本（与 oauth 同形态） |
| `cli_delegated` | **仅指针** —— `store_path` + 指向外部文件的 key-path；每次使用时重读 |
| `external_process` | 按需运行的 argv |

`AuthManager` **以存储为准，从不重新发现**：磁盘上是什么池，它就提供什么。
导入是显式的一次性写入步骤（`cli_import`、`import_from_codex_file`），因此一旦
凭据被复制进存储，就不会再有任何东西去读外部文件。
`openprogram/auth/methods/cli_import.py` 的 `mode="copy"` 会对外部文件解引用
一次，构造出一个可写、归存储所有的 `oauth` 凭据。

### 各 provider 的凭据来源

| provider | 来源 | 是否自包含？ |
|---|---|---|
| `openai-codex` | 原生 PKCE 或 `~/.codex/auth.json` 的副本；**由 OpenProgram 刷新**（`_codex_refresh` → `auth.openai.com/oauth/token`，并回写 `~/.codex`） | 是（刷新可用） |
| `github-copilot` | 原生 device-code → 存入 oauth | 是 |
| `openai`、`gemini`、其他 key 类 provider | env → `config.json["api_keys"]`（不是 pool 存储） | 基于 key，但双重存储 |
| `anthropic`（API key） | env/粘贴副本 | 是 |
| `anthropic`（订阅） | `~/.claude/.credentials.json` 指针；**refresh = None** | 否 |
| `gemini-subscription` | `~/.gemini/oauth_creds.json` 指针；**refresh = None** | 否 |
| `qwen` | `~/.qwen/oauth_creds.json` 指针（反正也没有运行时包） | 否 |

## 从参考实现借鉴的模式

**opencode** 完全自包含，不读取任何外部凭据。即便是 `codex` CLI 使用的同一个
OpenAI 账号，opencode 也会用公开的 `CLIENT_ID` 运行自己的 PKCE，并把结果存进
自己的 `auth.json`。它维护一个 `provider → AuthHook` 注册表：每个 provider
声明 `methods[]` 和一个 `authorize()`，后者返回 `method:"auto"`（loopback/轮询，
无需粘贴）或 `method:"code"`（用户粘贴）。刷新在请求的 `fetch` 内部按需进行：
比较 `expires < Date.now()`，单飞刷新，并把 token 写回去。值得注意的是，
**opencode 中 anthropic 和 google 完全没有 OAuth** —— 只有 API key，这正是它对
那两个无法自行刷新的 provider 给出的答案。

**openclaw** 的 `auth-profiles.json` 以 `<provider>:<label>` 这一 profile id
为键（每个 provider 可有多份凭据），并**把密钥与 rotation/usage 状态拆分**到一个
同级文件中。凭据是 `oauth | api_key | token` 的联合类型，其中 `token` 是静态、
不可刷新的 bearer，密钥支持内联或 `SecretRef`（env/file/exec/keychain）。刷新在
一个跨进程锁下按需进行，并**在锁内从磁盘重新读取**，从而采纳并发的刷新结果而
不是覆盖它 —— 这个对竞态安全的核心值得复制。一个共享的
`createVpsAwareOAuthHandlers` 根据远程环境标志在浏览器回调与粘贴码之间选择，
被每个 OAuth provider 复用，其下是一个共享的 PKCE 生成器。

openclaw 中本设计不采纳的部分：`cli-credentials.ts` 与 `external-cli-sync.ts`
直接读取 codex/minimax/claude 的 CLI 文件 —— 这正是本设计要去掉的跨 CLI 耦合。

## 不属于工程缺口的约束

1. **`gemini-subscription`** 无法自行刷新：Google 的 Code-Assist OAuth 使用了一个
   OpenProgram 无法分发的内嵌 client secret
   （`google_gemini_cli/auth_adapter.py:14-21`）。
2. **`anthropic` 订阅版 OAuth** 无法自行运行：Anthropic 尚未发布第三方 OAuth
   client（`anthropic/auth_adapter.py:16-21`）。

对这两者，自包含的*存储*可以做到 —— 把 token 复制进存储、不再指向外部文件 ——
但自包含的*刷新*做不到。短时效的 access token 过期后，OpenProgram 只能请用户
重新登录，或退回到 API key，也就是 opencode 的选择。

## 设计

1. **一个存储，复制而非指针。** 每份凭据都被复制进
   `~/.openprogram/auth/<provider>/<profile>.json`。`cli_delegated` 指针不再是
   默认方式；导入是一次性复制，之后外部文件与运行无关。指针式链接仍作为显式的
   可选项保留。
2. **一个登录注册表。** 一张 `provider → [auth method]` 表，每个 method 是
   `pkce_oauth | device_code | api_key | paste_code` 之一，其下由共享 helper
   支撑（`pkce_browser_flow`、`device_code_flow`，以及一个按远程/无头标志选择的
   `browser_vs_paste`）。它取代 `auth/cli.py::_available_login_methods` 中零散的
   映射成为唯一事实来源，每个 method 指名一个共享 handler。handler 由
   `auth/methods/{pkce_oauth,device_code,api_key_paste,cli_import}.py` 提供。
3. **三个界面驱动同一个注册表。** web 与 TUI 都驱动它，因此任何 provider 的登录
   都可以从任何界面完成，而不是只有 CLI 能原生跑 PKCE 与 device-code。
4. **刷新由自己持有**，只要存在公开 client（目前是 codex 与 copilot）。上述两个
   受约束的 provider 采取复制进存储、过期时提示重新登录。
5. **api key 只有一个事实来源。** key 类 provider 目前在运行时经
   env → `config.json["api_keys"]` 解析，而不是走凭据池。改为以池为准、
   向 `config.json` 镜像，双重存储随之消失。

对受约束的 provider，接受的行为是明确的：把 token 复制进存储、过期时重新登录，
不再指向 `~/.gemini` 与 `~/.claude`。既然 OpenProgram 无法自动刷新它们，
access token 过期就提示重新登录；它们很少过期，一次轮换也不过是再登录一次。

## 构建顺序

1. **登录方式注册表** —— 声明式的 `provider → [method]` 表作为唯一事实来源，
   CLI 优先从它读取。这是纯重构、行为不变，因此可以独立验证。
2. **共享登录 handler** —— 从 `auth/methods/*` 中抽出 `pkce_browser_flow`、
   `device_code_flow` 和 `browser_vs_paste` 选择器，使三个界面调用同一份代码。
3. **web 原生登录** —— 在 provider 详情页驱动该注册表。
4. **TUI 原生登录** —— 同样，从 `/login` 面板驱动。
5. 把 gemini-subscription、qwen、anthropic-subscription 复制进存储，以及合并
   api_key 的双重存储。

先做没有争议的核心 —— codex 与 copilot 完全自包含，加上跨 CLI/web/TUI 的统一
登录 —— 可以让已经可用的 codex 共享保持完好，而不是一次性改动全部。
