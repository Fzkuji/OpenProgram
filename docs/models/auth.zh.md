# 认证与凭据

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

- `anthropic` / `claude-code`：Claude 订阅 PKCE 登录，或粘贴 `claude setup-token` 的产物
- `openai-codex`：ChatGPT 订阅，需要 `codex` CLI（`codex login`）
- `gemini-subscription`：Google 账号登录
- `github-copilot`：GitHub OAuth token，Copilot 短期 token 按需换取、不落盘

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

## 管理与排障

```bash
openprogram providers status <provider>    # 当前凭据是否可用
openprogram providers doctor               # 过期、刷新失败、冷却、冲突
openprogram providers logout <provider>    # 删除凭据
openprogram providers use <provider> [profile]   # 多账号切换
openprogram providers list                 # 按 profile 列出凭据池
```

每个 provider 支持多账号（命名 profile），一个账号可以放多个 API key 自动轮询——某个 key 被限流会冷却并切到下一个。
