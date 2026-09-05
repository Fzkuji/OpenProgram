# Web UI 端口 — 配置与冲突处理

OpenProgram 如何为其 web UI 选择、配置并守护运行端口：配置入口
（`openprogram ports`），以及端口被占用时会发生什么。让单个端口足够用的
运行时设计见 [single-port.zh.md](single-port.zh.md)。

## 端口一览

| 默认值 | 提供的服务 | 配置方式 |
|---------|--------|---------------|
| `18100` | FastAPI 的 `/api/*`、`/ws`、`/healthz`，以及 web UI 静态导出 | `ports --port`、`OPENPROGRAM_WEB_PORT`、`ui.web_port` |

浏览器、TUI 和 CLI 都与这一个端口通信，没有代理跳转，也没有第二个进程。

### 为什么是 18100

一个固定的、不常见的 5 位数值，选它是为了几乎不会与已在运行的东西撞上：

- 落在 **registered-port** 区间（`< 49152`），因此绝不会与内核分配给出站
  套接字的 *ephemeral* 区间冲突。
- `18xxx` 段很少被主流开发工具使用 —— 不像 3000 / 5173 / 8000 / 8080，
  任何别的项目都可能已经占着。

固定端口同时意味着一个稳定、可收藏的 URL，以及能跨重启存活的浏览器会话
（localStorage、service worker scope）。

## 配置入口

```
显式参数 / 实参  >  环境变量  >  持久化偏好  >  内置默认值
```

### `openprogram ports`

```
openprogram ports                 # 显示当前端口
openprogram ports --port 9100     # 设置并持久化
```

写入 `~/.openprogram/config.json` 的 `ui.web_port`。
**不会重新绑定任何正在运行的服务** —— 改动在下一次 `openprogram web` /
`openprogram worker` 启动时生效。

### `openprogram setup ui`

交互式向导会询问端口（以及自动打开浏览器的偏好），校验范围 `1–65535`。

### 环境变量覆盖（单次运行，不持久化）

- `OPENPROGRAM_WEB_PORT` —— 本进程的端口。

### 单次启动标志

`openprogram web --web-port <p>` 为该次运行覆盖端口而不持久化。

### 每个入口点从何处读取

| 入口点 | 端口 |
|-------------|--------------|
| `openprogram web`（`cli/commands/web.py:_cmd_web`） | `--web-port` → `resolve_worker_port()` |
| `openprogram worker`（`worker/runner.py`） | `resolve_worker_port()` |

`openprogram/worker/lifecycle.py` 中的 `resolve_worker_port()` 是唯一的解析
路径：`OPENPROGRAM_WEB_PORT` → 偏好 `ui.web_port` → 18100。
`openprogram/setup.py` 中的 `read_ui_prefs()` / `set_ui_ports()` 是对持久化
偏好唯一的读写路径。

## 冲突处理

端口是有意固定的 —— 一个稳定的 UI URL 比"无论如何都要启动"更有价值。
因此策略是 **如果是我们的就复用，如果不是就报告并拒绝** —— 绝不杀死占用
者，绝不悄悄漂移到随机端口。这与 openclaw 一致。所有探测都集中在一个
模块 `openprogram/_ports.py` 中：

- **liveness** —— `port_in_use(port)`：一次裸的 TCP 连接。
- **identity** —— `backend_is_ours(port)` 先核对受管理 worker 的 PID 与 port 文件，以及
  当前 profile 中 owner-only 的 `web/access.json` snapshot，再向
  `/api/auth/challenge` 发送新的随机 nonce，并在本机验证返回的 token-HMAC proof。
  Probe 不会向 listener 发送 owner token 或 Bearer header，因此同一端口上的陌生进程
  无法取得 credential。可选 `expected_revision` 会把 proof 绑定到 upgrade target 当前
  提供的 revision。
- **ownership** —— `describe_port_owner(port)` / `port_owner_hint(port)`：
  用 `lsof` / `netstat` + `/proc` / `ps` / Windows CIM 来标识占用的 PID 与命令
  行，并归类为我们的还是外部的。正是它让"端口被占用"错误能说出 *谁* 在
  占用。

### 分情况的行为

| 该固定端口处于… | `openprogram web` | `openprogram worker` |
|--------------------|-------------------|----------------------|
| 空闲 | 绑定并启动 | 绑定并启动 |
| 被 **我们的** 实例占用 | 复用它，将浏览器指向该 UI | worker 锁已经阻止了第二个 worker |
| 被一个 **外部** 程序占用 | 拒绝；打印 *谁* 在占用它（PID + cmdline）以及如何释放它或更改端口；**不要** 在该端口打开浏览器 | 标识占用者，然后回退到一个空闲端口并报告（UI URL 随之更新）—— worker 还托管着 channels，因此它仍必须启动起来 |
| 刚刚退出（TIME_WAIT） | uvicorn 的 `SO_REUSEADDR` 重新绑定它 | `_port_available` 使用 `SO_REUSEADDR`，因此快速的自我重启 **不会** 漂移 |

唯一刻意保留的不对称：`openprogram web` 是一个前台 UI 命令，因此外部抢占
者是硬性中止。worker 是一个长期运行的宿主，同时承载 channels *和*
webui，因此它会保持运行，回退到另一个端口并给出诊断信息，而非彻底拒绝。

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
