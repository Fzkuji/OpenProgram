# 界面

OpenProgram 有三种使用方式：浏览器里的 Web UI、终端里的 TUI、命令行单发。本页说明三者的关系，帮你选一个入口开始。

## 三个客户端，一个服务

三种界面共用同一个本地后台服务（代码里叫 worker）：一个常驻进程，承载 FastAPI + WebSocket 后端（默认 18109 端口）和可选的聊天渠道适配器。Web UI 和终端 TUI 都通过 WebSocket 连接它；没有 worker 在跑时，TUI 会自动拉起一个。

会话统一存放在 `~/.openprogram/sessions/`（每个会话是一个 git 仓库），三种界面读写同一个存储。因此：

- 终端里开的聊天会出现在 Web UI 的侧栏里，点开即接着聊。
- Web 里的会话可以在终端里用 `/resume` 选中续聊，或用 `openprogram --resume <session-id>` 直接指定。
- `openprogram --print "..."` 单发的对话也会写入会话存储，事后可以在任一界面翻看。

worker 的管理命令：`openprogram status` / `stop` / `restart`；`openprogram worker install` 可注册为登录自启服务。详见 `openprogram -h`。

## 三种界面

| 界面 | 进入方式 | 适合 |
|---|---|---|
| [Web UI](web.md) | `openprogram web`，浏览器打开 `http://localhost:18100` | 日常主界面：聊天、DAG 分支视图、函数 / skill / MCP / 记忆管理、设置 |
| [终端 TUI](tui.md) | `openprogram`（无参数） | 不离开终端的完整聊天：斜杠命令、权限档切换、历史滚动 |
| [CLI 单发](cli.md) | `openprogram --print "..."` | 脚本化、被其他程序调用、快速问一句 |

## 隔离的工作区

`--profile <name>`（或环境变量 `OPENPROGRAM_PROFILE`）把整个状态目录从 `~/.openprogram/` 换到 `~/.openprogram-<name>/`——配置、会话、日志、凭据全部隔离，每个 profile 有自己的 worker。用于并行跑互不干扰的多套环境。
