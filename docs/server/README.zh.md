# 服务总览

OpenProgram 的 Web UI、TUI、CLI 背后是同一个常驻本地服务，代码和日志里叫 worker。本页说明它如何启动、如何查看状态、端口和日志在哪。

## 启动

不需要手动启动。直接运行 `openprogram`（终端 UI）时，如果没有 worker 在跑，会自动拉起一个后台 worker 并连上去。不想自动拉起时设 `OPENPROGRAM_NO_AUTO_WORKER=1`，此时 TUI 只连接已有 worker。

手动控制用 `openprogram worker` 子命令：

```bash
openprogram worker start     # 后台启动一个 worker 并返回
openprogram worker run       # 前台运行（阻塞），调试用，Ctrl-C 停止
openprogram worker status    # 是否在跑、PID、端口、运行时长
openprogram worker stop      # 停止（SIGTERM，必要时升级为 SIGKILL）
openprogram worker restart   # 停掉再起一个新的
```

`openprogram web` 在当前终端启动服务并打开浏览器 UI（`http://localhost:18100`）。

## status / stop / restart

顶层也有三个快捷命令：

```bash
openprogram status     # 后台服务是否在跑（PID、端口、运行时长、日志路径）
openprogram stop       # 停止后台服务
openprogram restart    # 重启（改了代码或配置之后用）
```

`openprogram status` 的输出示例：

```
openprogram: running (PID 82472, port 18109, up 48m)
  logs: ~/.openprogram/worker.log
```

## 端口

| 端口 | 用途 | 默认值 |
|------|------|--------|
| backend | FastAPI 后端（API + WebSocket），TUI 和 Web UI 都连它 | 18109 |
| frontend | Next.js 前端（浏览器里打开的地址） | 18100 |

持久化修改：

```bash
openprogram ports --backend 8102 --frontend 8101
```

单次运行覆盖：环境变量 `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT`，或 `openprogram web --port <backend> --web-port <frontend>`。优先级：显式参数 → 环境变量 → 持久化偏好 → 默认值。

## 日志

```bash
openprogram logs list           # 所有日志文件（大小、更新时间）
openprogram logs tail [name]    # 最后 N 行；-n 行数，-f 持续跟踪
openprogram logs path [name]    # 打印日志文件的绝对路径
```

日志名有三个：`worker`（默认，`~/.openprogram/worker.log`）、`runtime`（`~/.openprogram/logs/runtime.log`）、`ink`（TUI 启动日志，`~/.openprogram/logs/ink-startup.log`）。

## 作为登录服务运行

```bash
openprogram worker install      # 安装为系统服务
openprogram worker uninstall    # 移除
```

macOS 用 launchd（`~/Library/LaunchAgents/ai.openprogram.worker.plist`），Linux 用 systemd --user。安装后 worker 随登录自动启动，崩溃后自动重启。`openprogram status` 会显示服务是否已安装。

## 相关页面

- [配置与数据目录](configuration.md) —— `~/.openprogram/` 里有什么，`openprogram config` 怎么用
- [故障排查](troubleshooting.md) —— 常见的"它不工作"场景
