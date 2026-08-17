# 界面

OpenProgram 有四种用户界面：macOS Desktop App、外部浏览器中的 Web UI、终端 TUI 和命令行单发。本页说明它们的关系，帮助用户选择入口。

## 四个客户端，一个服务

四种界面共用同一个本地后台服务（代码里叫 worker）：一个常驻进程，在单个端口（默认 18100）上承载 FastAPI + WebSocket 后端和 web UI 本身，外加可选的聊天渠道适配器。Desktop 内嵌同一个 Web UI，并增加内置 Browser 与 Terminal 原生 Pane；Web UI 和终端 TUI 直接连接 worker。没有 worker 时，Desktop 或 TUI 会自动启动一个。

会话统一存放在 `~/.openprogram/sessions/`（每个会话是一个 git 仓库），四种界面读写同一个存储。因此：

- 终端里开的聊天会出现在 Web UI 的侧栏里，点开即接着聊。
- Web 里的会话可以在 TUI 内用 `/resume` 选中续聊，或用 `openprogram --resume <session-id> --print "..."` 非交互地续接。（`--resume` 参数目前在启动交互式 TUI 时不生效——交互续聊请用 TUI 内的 `/resume`。）
- `openprogram --print "..."` 单发的对话也会写入会话存储，事后可以在任一界面翻看。

worker 的管理命令：`openprogram status` / `stop` / `restart`；`openprogram worker install` 可注册为登录自启服务。详见 `openprogram -h`。

## 四种界面

| 界面 | 进入方式 | 适合 |
|---|---|---|
| [macOS Desktop](desktop.zh.md) | 打开 `/Applications/OpenProgram.app` | 多 Pane 聊天、Files、内置 Browser、Terminal，以及 Agent 控制可见的内置网页 |
| [Web UI](web.zh.md) | `openprogram web`，浏览器打开 `http://localhost:18100` | 日常主界面：聊天、DAG 分支视图、函数 / skill / MCP / 记忆管理、设置 |
| [终端 TUI](tui.zh.md) | `openprogram tui`（裸 `openprogram` 会先询问进终端还是网页） | 不离开终端的完整聊天：斜杠命令、权限档切换、历史滚动 |
| [CLI 单发](cli.zh.md) | `openprogram --print "..."` | 脚本化、被其他程序调用、快速问一句 |

## 隔离的工作区

`--profile <name>`（或环境变量 `OPENPROGRAM_PROFILE`）把整个状态目录从 `~/.openprogram/` 换到 `~/.openprogram-<name>/`——配置、会话、日志、凭据全部隔离，每个 profile 有自己的 worker。用于并行跑互不干扰的多套环境。
