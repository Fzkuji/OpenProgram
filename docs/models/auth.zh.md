# 认证

本页说明 provider 凭据从哪来、存在哪、如何从已登录的其他 CLI 导入。

## 存放位置

所有凭据统一存在凭据库：`~/.openprogram/auth/<provider>/<profile>.json`（权限 0600；使用 `--profile <name>` 时根目录换成 `~/.openprogram-<name>/`）。

运行时**只从凭据库取密钥，不直接读环境变量**。环境变量里的 key（如 `OPENAI_API_KEY`）需要先导入（见下文 discover），之后改环境变量不影响已导入的凭据。两个例外是云凭据链：Amazon Bedrock（`AWS_PROFILE` / access key / bearer token 等）和 Google Vertex（ADC），它们在运行时自动识别。

## 凭据的几种来源

### API key 登录

```bash
openprogram providers login deepseek                       # 交互式输入
printf %s "$KEY" | openprogram providers login deepseek --api-key-stdin   # 脚本
```

`--api-key` 也可以直接传值，但会留在 shell 历史里，脚本优先用 `--api-key-stdin`。

### OAuth 登录

订阅类 provider 用浏览器 / 设备码登录，`login` 自动选择方式（`--method` 可强制指定）：

- `anthropic` / `claude-code`：Claude 订阅浏览器 PKCE 登录，或粘贴 `claude setup-token` 的产物（两者写入同一个 `anthropic` 凭据池）
- `openai-codex`：ChatGPT 订阅浏览器 PKCE 登录；已有的 `codex` CLI 登录态可改用下文的 `discover` / `adopt` 导入
- `gemini-subscription`：导入 `~/.gemini/oauth_creds.json`——先用 Gemini CLI 登录
- `github-copilot`：GitHub 浏览器设备码登录（或导入 `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` 环境变量）；Copilot 短期 token 按需换取、不落盘

### 从本机已有凭据导入

```bash
openprogram providers discover        # 只扫描列出，不写入
openprogram providers adopt codex_cli # 导入某一项；--all 全部导入
```

扫描的来源：

| 来源 | 位置 | 导入到 |
|---|---|---|
| Codex CLI | `~/.codex/auth.json` | `openai-codex` |
| Qwen CLI | `~/.qwen/oauth_creds.json` | `qwen` |
| gh CLI | `~/.config/gh/hosts.yml` | `github` |
| 环境变量 | 进程环境里的 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等 | 对应 provider |

导入有两种形态：外部 CLI 还在机器上时记指针（每次调用现读外部文件，外部 CLI 自己刷新的 token 自动生效）；否则拷贝 token 进凭据库、由 OpenProgram 负责刷新。

Gemini CLI 的登录态不走 discover：`google-gemini-cli` provider 直接读 `~/.gemini/oauth_creds.json`，装好 Gemini CLI 并登录即可用。Claude 订阅同样不在扫描列表里，走上面的 OAuth 登录。

### 辅助命令（外部进程）

有些企业环境的 token 由厂商或自研 CLI 签发（`aws`、`sso-helper`、`token-fetcher`），拿不到可以直接粘贴的 API key。这类 provider 在 Settings → Providers 里按 `external_process` 类型添加：填命令 argv、stdout 的解析方式、缓存窗口。

| 字段 | 含义 | 默认值 |
|---|---|---|
| `command` | 辅助命令的 argv 列表。直接执行不过 shell，无需转义 | 必填 |
| `parses` | `json` 表示按 JSON 解析 stdout，`text` 表示 stdout 去空白后即 token | `json` |
| `json_key_path` | 在 JSON 里下钻的键路径，例如 `["creds", "token"]`。留空则文档本身必须是字符串 | `[]` |
| `cache_seconds` | 一个 token 复用多久后重新执行命令。`0` 表示每次调用都执行 | `300` |
| `timeout_seconds` | 单次执行的墙钟上限 | `60` |

每次 API 调用只要 token 不在缓存窗口内就会执行该命令；登录冒烟测试也会执行一次，命令有问题在配置阶段就能发现。

命令执行失败（非零退出、超时、输出无法解析、键路径不存在）会让请求直接报错，错误信息里带上命令和它的 stderr，不会回落到其他凭据。这是刻意配置的取值方式，悄悄换成别处的 token 只会掩盖故障。修好命令，或者删掉该凭据改用别的来源。

企业 SSO（`sso`）尚未实现。该凭据类型是预留的，API 和 resolver 都会明确拒绝，不接受一份永远不会生效的配置。

## 管理与排障

```bash
openprogram providers status <provider>    # 当前凭据是否可用
openprogram providers doctor               # 过期、刷新失败、冷却、冲突
openprogram providers logout <provider>    # 删除凭据
openprogram providers use <provider> [profile]   # 多账号切换
openprogram providers list                 # 按 profile 列出凭据池
```

每个 provider 支持多账号（命名 profile），一个账号的凭据池可以放多个 API key。某个 key 返回 401 / 402 / 429 / 503 时进入冷却，凭据池自动把下一把健康的 key 交给后续请求，策略可选（默认 `fill_first`，即"备用 key"语义；另有 `round_robin`、`random`、`least_used`）。跨账号轮换（而不是只用当前激活账号）是每个 provider 单独的开关，默认关闭。
