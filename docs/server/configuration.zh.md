# 配置与数据目录

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
openprogram config get <key>         # 读一个设置，如 ui.port
openprogram config set <key> <value> # 改一个设置
```

每个设置有生效方式：`live`（立即生效）或 `next start`（下次启动 worker 时生效，`config list` 里标注）。核心键：

| key | 含义 | 默认 | 生效 |
|-----|------|------|------|
| `ui.port` | backend（FastAPI）端口 | 18109 | next start |
| `ui.web_port` | frontend（Web UI）端口 | 18100 | next start |
| `ui.open_browser` | `openprogram web` 是否自动打开浏览器 | true | next start |
| `search.default_provider` | 默认 web 搜索 provider（`auto` 选优先级最高的已配置项） | auto | live |
| `memory.backend` | 记忆后端：`local`（磁盘）或 `none`（禁用） | local | next start |
| `tools.disabled.<name>` | 逐个工具的开关（写入 `tools.disabled` 列表） | 全部启用 | live |

`config list` 还会显示只读的 `providers.<name>` 状态行 —— 它们不能用 `config set` 改，要用 `openprogram providers login` 或 Web UI 的 Providers 页配置。

## 端口的快捷命令

`openprogram ports` 是 `ui.port` / `ui.web_port` 的专用写入口：

```bash
openprogram ports                        # 查看
openprogram ports --backend 8102 --frontend 8101   # 持久化修改
```

## 多实例：--profile

`--profile <name>`（或环境变量 `OPENPROGRAM_PROFILE`）把 config、sessions、logs 全部改道到 `~/.openprogram-<name>/`，让并行的工作区互不共享状态：

```bash
openprogram --profile dev            # 用 ~/.openprogram-dev/ 跑一套独立实例
OPENPROGRAM_PROFILE=dev openprogram status
```

配合不同的 `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT` 可以同时跑多套服务。安装方式见[安装](../install/profiles.md)。
