# Web UI 端口 — 架构、配置与冲突处理

OpenProgram 如何为其 web UI 选择、配置并守护运行端口。涵盖当前的双端口
方案、单端口目标、配置入口（`openprogram ports`），以及端口被占用时会
发生什么。

## 端口一览

| 角色 | 默认值 | 提供的服务 | 配置方式 |
|------|---------|--------|---------------|
| Backend | `18109` | FastAPI：`/api/*`、`/ws`、`/healthz` | `ports --backend`、`OPENPROGRAM_BACKEND_PORT`、`ui.port` |
| Frontend | `18100` | Next.js web UI（将 `/api`、`/ws` 代理到 backend） | `ports --frontend`、`OPENPROGRAM_WEB_PORT`、`ui.web_port` |

浏览器与 **frontend** 端口通信；frontend 将 API 与 WebSocket 流量代理到
**backend** 端口（`/api/*` 经由 Node 路由处理器
`web/app/api/[...path]/route.ts`，它读取实时的 `worker.port`；`/ws` 与
`/healthz` 经由 `next.config.mjs` 针对 `OPENPROGRAM_BACKEND_URL` 的
rewrite）。

### 为什么是 18109 / 18100

两者都是固定、不常见的 5 位数值，如此选择是为了让它们几乎不会与已在运行
的程序冲突：

- 落在 **registered-port** 范围内（`< 49152`），因此永远不会与内核分配给
  出站套接字的 OS *ephemeral* 范围冲突。
- `18xxx` 段很少被主流开发工具占用 —— 不像 `3000` / `8080` / `5000` /
  `8888` 那样频繁冲突。（旧的默认值是 frontend `:3000` 和 backend
  `:8109`；其中 `:3000` 尤其经常被人抢占。）
- openclaw 出于同样的原因做了同样的选择 —— 它的 gateway 被固定在
  `18789`。

这两个值相邻只是为了便于记忆；并没有任何要求必须如此。在双端口架构存续
期间，它们 **必须不同**。

## 配置

优先级，独立应用于每个端口：

```
explicit flag / arg  >  environment variable  >  stored pref  >  built-in default
```

### `openprogram ports`

```
openprogram ports                                    # 显示当前端口
openprogram ports --backend 18109 --frontend 18100   # 设置并持久化两者
openprogram ports --frontend 9100                    # 只设置其中一个
```

写入 `~/.openprogram/config.json` 的 `ui.port` / `ui.web_port`。
**不会重新绑定任何正在运行的服务** —— 改动在下一次 `openprogram web` /
`openprogram worker` 启动时生效。将 backend == frontend 的设置会被拒绝并
给出警告。

### `openprogram setup ui`

交互式向导会询问两个端口（以及自动打开浏览器的偏好），校验范围
`1–65535`，并拒绝相等的端口。

### 环境变量覆盖（单次运行，不持久化）

- `OPENPROGRAM_BACKEND_PORT` —— 本进程的 backend。
- `OPENPROGRAM_WEB_PORT` —— 本进程的 frontend。
- `OPENPROGRAM_WEB_NO_FRONTEND=1` —— 仅启动 backend。

### 单次启动标志

`openprogram web --port <backend> --web-port <frontend>` 为该次运行覆盖
端口而不持久化。

### 每个入口点从何处读取

| 入口点 | Backend 端口 | Frontend 端口 |
|-------------|--------------|---------------|
| `openprogram web`（`_cli_cmds/web.py:_cmd_web`） | `--port` → pref → 18109 | `--web-port` → `OPENPROGRAM_WEB_PORT` → pref → 18100 |
| `openprogram worker`（`worker/runner.py`） | `OPENPROGRAM_BACKEND_PORT` → pref → 18109 | `worker/web.py`：arg → `OPENPROGRAM_WEB_PORT` → pref → 18100 |

`openprogram/setup.py` 中的 `read_ui_prefs()` / `set_ui_ports()` 是对持久化
的 `ui.port` / `ui.web_port` 唯一的读写路径。

## 冲突处理

端口是有意固定的 —— 一个稳定的 UI URL 比"无论如何都要启动"更有价值。
因此策略是 **如果是我们的就复用，如果不是就报告并拒绝** —— 绝不杀死占用
者，绝不悄悄漂移到随机端口。这与 openclaw 一致。所有探测都集中在一个
模块 `openprogram/_ports.py` 中：

- **liveness** —— `port_in_use(port)`：一次裸的 TCP 连接。
- **identity** —— `backend_is_ours(port)` 探测 `/healthz` 以查找
  openprogram 的特征 JSON（`status` + `uptime_seconds`）；
  `frontend_is_ours(port)` 探测 `/` 以查找 Next.js 特征（`/_next/`、
  `__next`、`x-powered-by: Next.js`）。用于将 *我们的* 实例与同一端口上
  的陌生程序区分开。
- **ownership** —— `describe_port_owner(port)` / `port_owner_hint(port)`：
  用 `lsof` / `netstat` + `/proc` / `ps` / `wmic` 来标识占用的 PID 与命令
  行，并归类为我们的还是外部的。正是它让"端口被占用"错误能说出 *谁* 在
  占用。

### 分情况的行为

| 该固定端口处于… | `openprogram web` | `openprogram worker` |
|--------------------|-------------------|----------------------|
| 空闲 | 绑定并启动 | 绑定并启动 |
| 被 **我们的** 实例占用 | 复用它，将浏览器指向该 UI | worker 锁已经阻止了第二个 worker |
| 被 **我们** 遗留的 Next（frontend）占用 | 不适用 | `_reclaim_web_port` 仅杀死孤立的 `next-server`，然后绑定 |
| 被一个 **外部** 程序占用 | 拒绝；打印 *谁* 在占用它（PID + cmdline）以及如何释放它或更改端口；**不要** 在该端口打开浏览器 | 标识占用者，然后 **响亮地** 回退到一个空闲端口（UI URL 随之更新）—— worker 还托管着 channels，因此它仍必须启动起来 |
| 刚刚退出（TIME_WAIT） | uvicorn 的 `SO_REUSEADDR` 重新绑定它 | `_port_available` 使用 `SO_REUSEADDR`，因此快速的自我重启 **不会** 漂移 |

唯一刻意保留的不对称：`openprogram web` 是一个前台 UI 命令，因此外部抢占
者是硬性中止。worker 是一个长期运行的宿主，同时承载 channels *和*
webui，因此它会保持运行（响亮且带诊断的回退），而非彻底拒绝。

## 与 openclaw 的关系

openclaw 将其 gateway 固定在 `18789`，并在三个层面处理冲突；OpenProgram
的对应实现：

| openclaw 层 | openclaw 源 | OpenProgram 对应实现 |
|----------------|-----------------|------------------------|
| 单实例锁（pid + start-time + argv） | `src/infra/gateway-lock.ts` | `worker.lock`（fcntl）+ `worker.pid`（含 start-time）+ `_process_alive` |
| 用 EADDRINUSE 重试以熬过 TIME_WAIT | `src/gateway/server/http-listen.ts` | 在绑定时使用 `SO_REUSEADDR`（无需重试循环） |
| 通过 `lsof` 标识占用者 | `src/infra/ports.ts` | `_ports.describe_port_owner` / `port_owner_hint`，接入到每一条"端口被占用"消息中 |

值得注意的是，openclaw 的 `lsof` 诊断 **并不** 在其主 gateway 启动路径上
（只在 SSH-tunnel 路径上），因此它的 gateway 启动"端口被占用"错误无法
标识占用者。OpenProgram 将占用者诊断接入到了真正的启动路径中。

## 单端口的未来

双端口拆分是过渡性的。计划中的迁移（见
[`attachment-handling.md`](../ui/attachment-handling.md) 的同期工作以及
项目的单端口笔记）会将 Next.js SPA 静态导出，并由 FastAPI backend 提供
服务，从而收拢为 **一个** 同时提供 UI 与 API 的端口（`18109`）。届时
frontend 端口、它单独的启动器、代理以及 `worker/web.py` 的大部分都将
消失 —— 而"frontend 端口被占用"将不再是一种可能的状态。`openprogram
ports` 入口保留；一旦只剩单个端口，`--frontend` 就只是变成一个 no-op
别名。
