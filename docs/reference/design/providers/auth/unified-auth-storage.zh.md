# 统一的自包含认证存储

**目标（用户需求）：** 所有 provider 的凭据都存放在 `~/.openprogram` 下，
由 OpenProgram 自己管理 —— 不再以只读方式"收编"（adopt）其他 CLI 的
凭据文件（`~/.codex/auth.json`、`~/.claude/.credentials.json`、
`~/.gemini`、`~/.qwen`、`~/.config/gh`）以及外部的 Meridian profile 目录。
一个存储、一套登录流程，在 CLI / web / TUI 之间保持一致。借鉴
`references/opencode` 和 `references/openclaw` 中已验证的模式。

## 已有基础（我们并非从零开始）

`openprogram/auth/store.py` 已经实现了一个真正的 `AuthStore`，位于
`~/.openprogram/auth/<provider_id>/<profile_id>.json`（0600 权限，原子化
write→fsync→replace，跨进程 `flock`，内存中监视 mtime/size，因此底层文件
被改动后会重新加载）。`openprogram/auth/types.py` 定义了以下凭据类型：

| kind | 密钥存储方式 |
|---|---|
| `api_key` | 密钥的副本 |
| `oauth` | 副本：access + refresh + `expires_at_ms` + client_id + token_endpoint |
| `device_code` | 副本（与 oauth 结构相同） |
| `cli_delegated` | **仅为指针** —— `store_path` + 指向外部文件的 key-path；每次使用时重新读取 |
| `external_process` | 按需运行的 argv |

`AuthManager` 是 **以存储为权威、从不重新发现**的：它只提供磁盘上现有的
pool；"收编"是一个显式的、只写一次的步骤（`cli_import`、
`import_from_codex_file`）。因此一旦凭据被复制进存储，就不会再重新读取
外部文件。迁移所需的原语已经存在：`openprogram/auth/methods/cli_import.py`
的 `mode="copy"` 会*当场*解引用外部文件，并构建一个可写的、归存储所有的
`oauth` 凭据。

### 各 provider 当前的来源

| provider | 当前来源 | 是否自包含？ |
|---|---|---|
| `openai-codex` | 原生 PKCE 或 `~/.codex/auth.json` 的副本；**由 OpenProgram 刷新**（`_codex_refresh` → `auth.openai.com/oauth/token`，并回写 `~/.codex`） | 是（刷新可用） |
| `github-copilot` | 原生 device-code → 存入 oauth | 是 |
| `openai`、`gemini`、其他 key 类 provider | env → `config.json["api_keys"]`（不是 pool 存储） | 基于 key，但双重存储 |
| `anthropic`（API key） | env/粘贴副本 | 是 |
| `anthropic`（订阅） | 收编 `~/.claude/.credentials.json` 指针；**refresh = None** | 否 |
| `gemini-subscription` | 收编 `~/.gemini/oauth_creds.json` 指针；**refresh = None** | 否 |
| `qwen` | 收编 `~/.qwen/oauth_creds.json` 指针（反正也没有运行时包） | 否 |
| `claude-code` | **Meridian 守护进程**在 `~/.config/meridian` 中持有 OAuth；在 AuthStore 中无任何痕迹 | 独立子系统 |

## 借鉴的模式

- **opencode**（`references/opencode`）—— 完全自包含，零收编。
  即便是 `codex` CLI 使用的*同一个* OpenAI 账号，opencode 也会运行自己的
  PKCE（公开的 `CLIENT_ID`），并把结果存进自己的 `auth.json`。它有一个
  `provider → AuthHook` 注册表：每个 provider 声明 `methods[]` 和一个
  `authorize()`，后者返回 `method:"auto"`（loopback/轮询，无需粘贴）或
  `method:"code"`（用户粘贴）。刷新是**在请求的 `fetch` 内部按需进行**的：
  比较 `expires < Date.now()`，单飞（single-flight）刷新，并把 token 写
  回去。关键在于：**opencode 中 anthropic 和 google 没有 OAuth —— 只有
  API key。** 这正是对 OpenProgram 无法自行刷新的那两个 provider 的务实
  答案。
- **openclaw**（`references/openclaw`）—— `auth-profiles.json` 以
  `<provider>:<label>` 这一 profile id 为键（每个 provider 可有多份凭据），
  **把密钥与 rotation/usage 状态拆分**到一个同级文件中，使用
  `oauth | api_key | token` 的联合类型，其中 `token` = 静态、不可刷新的
  bearer，密钥支持内联或 `SecretRef`（env/file/exec/keychain）。刷新是
  **在一个跨进程锁下按需进行的，并在锁内从磁盘重新读取**（采纳并发的
  刷新结果，而不是覆盖它）—— 这是值得复制的、对竞态安全的核心。一个共享
  的 `createVpsAwareOAuthHandlers` 根据远程环境标志在浏览器回调与粘贴码
  之间选择，被每个 OAuth provider 复用；还有一个共享的 PKCE 生成器。
  **不应复制的部分：** openclaw 的 `cli-credentials.ts` + `external-cli-sync.ts`
  会收编 codex/minimax/claude 的 CLI 文件 —— 这正是我们要摒弃的跨 CLI
  耦合。

## 硬性约束（不是工程上的缺口）

1. **`gemini-subscription`** 无法自行刷新：Google 的 Code-Assist OAuth
   使用了一个 OpenProgram 无法分发的内嵌 client secret
   （`google_gemini_cli/auth_adapter.py:14-21` 正是因此被否决）。
2. **`anthropic` 订阅版 OAuth** 无法自行运行：Anthropic 尚未发布
   第三方 OAuth client（`anthropic/auth_adapter.py:16-21`）。
3. **`claude-code`** 完全通过 Meridian 守护进程运行；它的 OAuth 存在
   Meridian 的 profile 目录中，并且由 Meridian（而非 OpenProgram）来刷新。

对于 (1) 和 (2)，"自包含存储"是可实现的（把 token 复制进我们的存储，
不再指向外部文件），但"自包含刷新"则不行 —— 当短期的 access token 过期
时，OpenProgram 只能请用户重新登录（或回退到 API key，即 opencode 的
选择）。

## 目标架构

1. **一个存储，复制而非指针。** 每份凭据都复制到
   `~/.openprogram/auth/<provider>/<profile>.json`。`cli_delegated` 指针
   不再是默认方式；收编变为"导入（复制）一次"，之后外部文件就无关紧要了。
   收编为链接（adopt-link）仅作为显式的可选项保留。
2. **一个登录注册表**（opencode/openclaw 的形态）。一张
   `provider → [auth method]` 表，其中每个 method 是 `pkce_oauth | device_code | api_key |
   paste_code` 之一，由**共享辅助函数**支撑（`pkce_browser_flow`、
   `device_code_flow`，以及一个以远程/无头标志为键的 `browser_vs_paste`
   选择器 —— 它同时也清理了 claude-code 的粘贴码流程）。OpenProgram
   已经有 `methods/{pkce_oauth,device_code,api_key_paste,cli_import}.py`；
   只是它们尚未接到每个 provider 或每个界面。
3. **三个界面驱动同一个注册表。** 如今只有 CLI 能原生运行
   PKCE/device-code（codex/copilot）；web 仅对 claude-code（Meridian）做
   原生支持，TUI 把其他一切都推给 web。目标：web + TUI 都驱动这个注册表，
   这样任何 provider 的登录都能从任何界面完成。
4. **刷新的所有权尽可能内迁**（codex ✓，copilot ✓；对任何拥有公开 client
   的 provider，新写刷新器都是可行的）。受约束的那两个 provider 采用
   复制进存储 + 过期时提示重新登录。
5. **整合 api_key 的双重存储。** key 类 provider 在运行时通过
   env → `config.json["api_keys"]` 解析，而非 pool。让 pool 成为权威并
   镜像到 `config.json`（或反之），从而只有一个事实来源。

## 分阶段迁移

- **P1 —— Codex 默认自包含。** 优先使用 OpenProgram 自己的 PKCE +
  刷新；首次使用时把 `~/.codex` 复制进存储，而不是指向它；保留导入作为
  显式选项。（风险最低：刷新本就可用。）
- **P2 —— 统一的登录注册表 + 共享辅助函数 + web/TUI 原生登录**，面向
  codex 和 copilot（这两个拥有可用的原生 OAuth）。提取出
  browser-vs-paste 选择器。
- **P3 —— 把 gemini-subscription / qwen / anthropic-subscription 复制进
  存储**（不再指向 `~/.gemini`/`~/.qwen`/`~/.claude`）；过期时提示
  重新登录（对刷新约束如实说明），并以 API key 作为始终可用的回退。
- **P4 —— 把 Meridian（claude-code）迁到 `~/.openprogram` 下**，将其
  config 目录指向 `~/.openprogram/meridian` 而非 `~/.config/meridian`，
  这样连代理的 profile 也都位于 app 目录之下。
- **P5 —— 收拢 api_key 的双重存储**（pool 为权威 + config.json 镜像）。

## 决策（已与用户确认）

- **范围：** 先做没有争议的核心 —— codex/copilot 完全自包含 +
  CLI/web/TUI 之间统一登录 —— 然后逐阶段确认 P3 及其余部分。不做一次性
  大爆炸式改动（避免破坏正常工作的 codex 共享以及刚修好的 claude-code
  账号）。
- **受约束的 provider（gemini-subscription、anthropic 订阅）：** 把 token
  复制进存储，过期时重新登录。不再指向 `~/.gemini` / `~/.claude`；由于
  OpenProgram 无法自动刷新它们，过期的 access token 会触发一次全新登录
  —— 可以接受，它们很少过期，一次 rotation 也只意味着再登录一次。（P3。）
- **Meridian（claude-code）：** 保留在 `~/.config/meridian`，**不**迁移。
  claude-code 已经与终端的 `claude login` 隔离开；只是它的目录在物理上
  不位于 `~/.openprogram` 之下，这是可以接受的。**P4 被取消。**

## 构建顺序（在决策之后修订）

1. **登录方法注册表** —— 一张声明式的 `provider → [method]` 表
   （`pkce_oauth | device_code | api_key | paste_code | import`）作为唯一
   的事实来源，取代 `auth/cli.py` `_available_login_methods` 中的临时
   映射。每个 method 指明一个共享 handler。CLI 优先从中读取（行为不变 ——
   纯重构，可验证）。
2. **共享登录 handler** —— 从现有的 `auth/methods/*` 中提取出
   `pkce_browser_flow`、`device_code_flow`，以及一个 `browser_vs_paste`
   选择器（远程/无头标志），使三个界面都调用同一套代码。
3. **web 原生登录** —— 从 provider 详情页驱动这个注册表，使
   codex/copilot 能从 web 登录（如今 web 只对 claude-code 做原生支持）。
4. **TUI 原生登录** —— 同理，从 `/login` 面板进行（如今它把任务推给 web）。
5. 之后，按单独确认进行：P3（gemini/qwen/anthropic 复制进存储）、
   P5（api_key 在 pool↔config.json 之间单一来源）。
