# 配置

OpenProgram 的全部状态存在 `~/.openprogram/` 一个目录里。本页说明目录里有什么、`openprogram config` 怎么读写设置，以及如何用 profile 隔离多套状态。

## ~/.openprogram/ 里有什么

主要文件和子目录（按用途分组）：

| 路径 | 内容 |
|------|------|
| `config.json` | 用户设置：端口、默认模型、provider 配置、禁用的工具等，见[配置参考](../reference/config.md) |
| `sessions/`、`sessions-git/` | 聊天会话数据及其 git 存档 |
| `agents/`、`agents.json` | agent 定义（persona、模型、技能） |
| `auth/` | provider 凭据存储 |
| `skills/` | 安装的技能（SKILL.md 目录） |
| `plugins/` | 安装的插件 |
| `mcp_servers.json` | MCP server 配置 |
| `memory/` | 持久记忆（wiki + journal） |
| `channels/` | 聊天频道机器人（Telegram、Discord、WeChat 等）状态 |
| `browser-states/`、`chrome-profile/` | 浏览器工具的登录态与 sidecar Chrome profile |
| `projects/`、`worktrees/`、`shadow-git/` | 项目工作区与 git worktree 状态 |
| `logs/`、`worker.log` | 日志；另有 `worker.pid` / `worker.port` / `worker.lock` 等 worker 运行时文件 |
| `models/`、`cache/`、`tool_results/`、`usage.db` | 模型目录缓存、通用缓存、工具结果、用量数据库 |

## openprogram config

```bash
openprogram config list              # 列出每个设置：值、分组、生效方式
openprogram config get <key>         # 读一个设置，如 ui.web_port
openprogram config set <key> <value> # 改一个设置
```

每个设置有生效方式：`live`（立即生效）或 `next start`（下次启动 worker 时生效，`config list` 里标注）。核心键：

| key | 含义 | 默认 | 生效 |
|-----|------|------|------|
| `ui.web_port` | worker 单端口（API + WebSocket + web UI） | 18100 | next start |
| `ui.open_browser` | `openprogram web` 是否自动打开浏览器 | true | next start |
| `search.default_provider` | 默认 web 搜索 provider（`auto` 选优先级最高的已配置项） | auto | live |
| `memory.backend` | `local`（磁盘）或 `none`（不注入、不召回、不自动写入、不整理，也不启动记忆线程） | local | next start |
| `memory.writer.model` | 后台写入使用的可选 `provider/model`；留空时沿用默认聊天 agent 及其凭据 | 空 | live |
| `tools.disabled.<name>` | 逐个工具的开关（写入 `tools.disabled` 列表） | 全部启用 | live |

`config list` 还会显示只读的 `providers.<name>` 状态行 —— 它们不能用 `config set` 改，要用 `openprogram providers login` 或 Web UI 的 Providers 页配置。

## 端口的快捷命令

`openprogram ports` 是端口偏好的专用写入口：

```bash
openprogram ports                    # 查看
openprogram ports --port 8101        # 持久化修改
```

## 谁能连上这个服务

API 不要求调用方鉴权，所以决定谁能连上它的只有两个设置。

`web.host` 决定监听哪个网卡。默认 `127.0.0.1` 只接受本机连接。设成 `0.0.0.0`
等于把 UI 连同所有已存的 API key 一起交给整个局域网，`/api/providers/…/reveal`
是明文返回 key 的。

只绑回环还不够，因为浏览器能从你随手打开的任何页面连上回环。有两条攻击正是走这里：
一个页面可以直接向 `127.0.0.1` 开 WebSocket（同源策略管不到 WebSocket）驱动 agent，
而 agent 手里有 `bash` 工具；或者某个站点的域名在页面加载后重新解析到 `127.0.0.1`，
它的请求看上去就是同源的。所以服务在路由之前会检查：

- 绑在回环上时，`Host` 头必须是回环地址；
- 浏览器没有把这次请求标成 `Sec-Fetch-Site: cross-site`；
- `Origin` 存在时，必须等于本次请求的 `Host`，或者本身是回环地址。

其余一律 403。完全不带 `Origin` 的请求不是浏览器发的（终端 UI、`curl`、Python
客户端都属于这类），放行。

如果你自己在前面架了反向代理，把那个域名加进来，它的页面才会被接受：

```bash
openprogram config set web.allowed_origins '["https://agent.example.com"]'
```

## 网络代理

所有 LLM provider 流量按同一套规则解析代理，优先级如下：

1. **`OPENPROGRAM_PROXY_URL`** —— 显式覆盖。设置后所有 provider 请求都走它，
   接受 `http://`、`https://` 或 `socks5://` 地址。`NO_PROXY` 白名单仍然生效。
2. **标准环境变量** —— `http_proxy` / `HTTP_PROXY`、`https_proxy` /
   `HTTPS_PROXY`、`all_proxy` / `ALL_PROXY`，直连白名单用 `no_proxy` /
   `NO_PROXY`（主机名、域名后缀或 `*`）。macOS 和 Windows 上，这些变量都没设时
   会退回操作系统的代理设置——与 Python 标准库的行为一致。

SOCKS 代理开箱即用（`httpx[socks]` 是硬依赖）。CLI 型 provider（Claude Code、
Codex CLI、Gemini CLI）以子进程运行、继承你的 shell 环境，代理由外部 CLI 自行处理。

`openprogram rescue` 会报告解析出的代理配置，并在 SOCKS 代理缺少支持包时给出警告。

## 多实例：--profile

`--profile <name>`（或环境变量 `OPENPROGRAM_PROFILE`）把 config、sessions、logs 全部改道到 `~/.openprogram-<name>/`，让并行的工作区互不共享状态：

```bash
openprogram --profile dev            # 用 ~/.openprogram-dev/ 跑一套独立实例
OPENPROGRAM_PROFILE=dev openprogram status
```

配合不同的 `OPENPROGRAM_WEB_PORT` 可以同时跑多套服务。安装方式见[安装](../install/profiles.md)。
