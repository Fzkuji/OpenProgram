# 概览

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
openprogram: running (PID 82472, port 18100, up 48m)
  logs: ~/.openprogram/worker.log
```

## 端口

worker 只监听一个端口（默认 18100），承载全部内容：API、WebSocket（TUI 和 Web UI 都连它）以及 web UI 本身——浏览器里打开的就是这个地址。

持久化修改：

```bash
openprogram ports --port 8101
```

单次运行覆盖：环境变量 `OPENPROGRAM_WEB_PORT`，或 `openprogram web --web-port <p>`。优先级：显式参数 → 环境变量 → 持久化偏好 → 默认值。

## 日志

```bash
openprogram logs list           # 所有日志文件（大小、更新时间）
openprogram logs tail [name]    # 最后 N 行（默认 50）；-n 行数，-f 持续跟踪
openprogram logs path [name]    # 打印日志文件的绝对路径
```

日志名有三个：`worker`（默认，`~/.openprogram/worker.log`）、`runtime`（`~/.openprogram/logs/runtime.log`）、`ink-startup`（TUI 启动日志，`~/.openprogram/logs/ink-startup.log`）。名字按前缀匹配，所以 `openprogram logs tail ink` 也行。

## 作为登录服务运行

```bash
openprogram worker install      # 安装为系统服务
openprogram worker uninstall    # 移除
```

macOS 使用 launchd（`~/Library/LaunchAgents/ai.openprogram.worker.plist`），
Linux 使用 systemd --user，Windows source checkout 使用最低权限的每用户任务计划。
安装后 worker 随登录自动启动，崩溃后自动重启。`openprogram status` 会显示服务是否已安装。

Linux 安装服务后，`worker start`、`stop`、`restart` 会继续通过已安装的
systemd 用户服务执行，不会悄悄改成脱管的后台进程。`restart` 还会按当前 CLI
runtime 刷新生成的 unit，因此不可变 release 升级后不会继续启动旧 runtime。
每个命名 [profile](../install/profiles.zh.md) 都有独立 unit，所以一个 profile 的
服务命令不会停止或替换另一个 profile 的 worker。

## 相关页面

- [配置与数据目录](configuration.md) —— `~/.openprogram/` 里有什么，`openprogram config` 怎么用
- [备份与恢复](backup.md) —— 用 `openprogram backup` 快照记忆、会话与配置
- [故障排查](troubleshooting.md) —— 常见的"它不工作"场景
