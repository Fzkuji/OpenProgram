# 配置

`~/.openprogram/config.json` 的键、`openprogram config` 能读写什么、以及环境变量汇总。日常改设置的入口见[配置与数据目录](../server/configuration.md)。

## openprogram config 能读写什么

```bash
openprogram config list              # 全部设置：值、分组、生效方式
openprogram config get ui.web_port
openprogram config set ui.web_port 8101
```

设置注册表定义在 `openprogram/config_schema.py`（单一事实来源，setup 向导、TUI 设置页、Web 设置页都从它渲染）。每个设置标注生效方式：`live` 立即生效，`next_start` 下次启动 worker 生效。

| key | 分组 | 含义 | 默认 | 生效 |
|-----|------|------|------|------|
| `ui.web_port` | Ports | 单端口的遗留别名（弃用过渡期保留） | 18100 | next start |
| `ui.open_browser` | Ports | `openprogram web` 是否自动开浏览器 | true | next start |
| `search.default_provider` | Search | 默认 web 搜索 provider，`auto` 选优先级最高的已配置项 | auto | live |
| `memory.backend` | Memory | `local`（磁盘记忆）或 `none`（不注入记忆、不召回、不自动写入、不整理，也不启动记忆线程） | local | next start |
| `memory.writer.model` | Memory | 后台写入使用的可选 `provider/model`；留空时沿用默认聊天 agent 的 provider、模型和凭据 | 空 | live |
| `sandbox.mode` | Sandbox | `danger-full-access`，或`workspace-write`：对本地模型驱动命令使用宿主原生沙箱，写入限制在工作目录/已配置根，deny-read路径不可读，网络禁用 | workspace-write | live |
| `sandbox.writable_roots` | Sandbox | 沙箱内额外可写的目录，JSON列表 | [] | live |
| `sandbox.deny_read` | Sandbox | 沙箱内不可读的glob，默认包含凭证路径。Linux不能强制`**/.env`这类中段通配；敏感内容要使用精确路径，或`/absolute/path/to/secrets/**`这类具有确定前缀的目录级deny | 见`openprogram config get sandbox.deny_read` | live |
| `sandbox.deny_write` | Sandbox | 沙箱内不可写的glob，函数watcher自动导入的目录始终禁写、不在此列 | [] | live |
| `sandbox.network` | Sandbox | 沙箱内是否有网络 | false | live |
| `sandbox.pass_env` | Sandbox | 内置白名单之外还要透传的环境变量名 | [] | live |
| `sandbox.unavailable_policy` | Sandbox | 平台后端缺失或无法创建所需隔离时，`refuse`让命令失败，`warn`允许命令在没有沙箱的情况下执行 | refuse | live |
| `tools.disabled.<name>` | Tools | 逐工具开关；写入的是 `tools.disabled` 列表的成员 | 全部启用 | live |
| `agent.output_style` | Agent | 回复怎么写，往系统提示追加一段文字。见[输出风格](output-styles.zh.md) | default | live |
| `providers.<name>` | Providers | 只读状态行（是否已配置）；用 `openprogram providers login` 或 Web UI 配置 | — | — |

本地沙箱在 macOS 使用 Seatbelt，在 Linux 使用 bubblewrap，在 Windows 则把 bubblewrap 委托给默认 WSL2 发行版。该后端不可用时，沙箱开启状态下默认拒绝命令；只有 owner 显式设置不安全的 `sandbox.unavailable_policy=warn` 或 `sandbox.mode=danger-full-access` 才会改变该行为。Docker 不是自动回退后端。

## config.json 顶层键

实际写入 `~/.openprogram/config.json` 的顶层键（不要手改，走 `openprogram config set` / setup 向导 / Web UI）：

| 键 | 含义 | 代码 |
|----|------|------|
| `ui` | `{port, web_port, open_browser}`，见上表 | `openprogram/config_schema.py` |
| `search` | `{default_provider}` | `openprogram/setup.py` |
| `memory` | `{backend, writer: {model}}`，见上表 | `openprogram/config_schema.py`、`openprogram/memory/` |
| `tools` | `{disabled: [工具名, ...]}` | `openprogram/setup.py`、`openprogram/config_schema.py` |
| `sandbox` | `{mode, writable_roots, deny_read, deny_write, network, pass_env, unavailable_policy}`，见上表 | `openprogram/sandbox/__init__.py`、`openprogram/config_schema.py` |
| `default_provider` | 默认 LLM provider（setup 向导写入） | `openprogram/setup.py` |
| `default_model` | 默认模型（setup 向导写入） | `openprogram/setup.py` |
| `default_workdir` | agent 的默认工作目录 | `openprogram/paths.py` |
| `providers` | 每个 provider 的设置子树（启用的模型、自定义模型等），由 Web UI 模型列表管理 | `openprogram/providers/_config_read.py`、`openprogram/providers/storage.py` |
| `api_keys` | 环境变量名 → API key 的映射，setup 向导写入，worker 启动时导出到环境。用于 web 搜索 / TTS 的 key；LLM provider 的 key 存在凭据库（`openprogram providers login`），不在这里 | `apps/cli/python/openprogram_cli/_impl/setup_sections/sections.py`、`apps/server/openprogram_server/server.py` |
| `spec_migration_version` | 模型 spec 迁移的一次性标记，含义见代码 | `openprogram/providers/storage.py` |

## 环境变量

在启动 `openprogram`（或 worker）的 shell 里设置。全部逐个在代码里核实过；每行给出定义处。

### 路径与实例

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_PROFILE` | 状态目录 profile，等价 `--profile`，改道到 `~/.openprogram-<name>/` | `openprogram/paths.py` |
| `OPENPROGRAM_HOME` | auth 账号的替代基目录 | `openprogram/auth/accounts.py` |
| `OPENPROGRAM_WORKDIR` | agent 默认工作目录（优先于 config 的 `default_workdir`） | `openprogram/paths.py` |

### 端口与 web

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_WEB_PORT` | worker 单端口（默认 18100）；优先级低于显式参数、高于持久化偏好 | `openprogram/worker/lifecycle.py`、`apps/cli/python/openprogram_cli/_impl/commands/web.py` |
| `OPENPROGRAM_NO_WEB` | `1` = worker 跳过前端构建检查，不提供 web UI | `openprogram/worker/runner.py` |
| `OPENPROGRAM_WEB_NO_FRONTEND` | `1` = `openprogram web` 跳过前端只起 backend | `apps/cli/python/openprogram_cli/_impl/commands/web.py` |
| `OPENPROGRAM_DOCS_BASE` | 文档站的挂载路径（默认 `/docs/`，须以 `/` 开头和结尾） | `scripts/docs_site/build.py` |

### 行为开关

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_NO_AUTO_WORKER` | `1` = TUI 不自动拉起 worker，只连已有的 | `apps/cli/python/openprogram_cli/_impl/ink.py` |
| `OPENPROGRAM_NO_SLEEP` | `1` = 禁用记忆的 sleep 整理调度器 | `openprogram/memory/scheduler.py` |
| `OPENPROGRAM_NO_PROGRAMS_WATCH` | `1` = 禁用 programs 目录的文件监听 | `openprogram/programs/watcher.py` |
| `OPENPROGRAM_PROJECT_AUTOCOMMIT` | `0` = 关闭项目自动 commit | `openprogram/store/project/project_commit.py` |
| `OPENPROGRAM_WEBSEARCH_DISABLE` | 按名禁用某个 web 搜索 provider（如 `ollama`） | `openprogram/programs/tools/web/web_search/providers/ollama.py` |

### LLM 调用

| 变量 | 用途 | 代码 |
|------|------|------|
| `AGENTIC_PROVIDER` / `AGENTIC_MODEL` | `detect_provider()`（进而 `create_runtime()`）最先选用的 provider / 模型，优先于配置文件和 CLI 检测 | `openprogram/providers/registry.py` |
| `OPENPROGRAM_MAX_RETRIES` | Runtime 的 API 瞬态故障重试次数（默认 6） | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_RETRY_BACKOFF_BASE` | 指数重试退避的基数秒数（默认 1.5） | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_EXEC_TIMEOUT_S` | 调用方没传 `timeout_s` 时每次 `runtime.exec` 的默认墙钟预算（秒；没设或为 `0` = 不限时） | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_FALLBACK_MODELS` | 主模型在产生输出前失败时使用的候选链。不设置＝同一 provider 下启用的其他模型（最多 2 个）；设成逗号分隔的 `provider/model` 列表可覆盖它并允许跨 provider；设成 `off` 关闭故障转移 | `openprogram/providers/utils/failover.py` |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | 流式请求的最大重试次数 | `openprogram/providers/utils/stream_retry.py` |
| `OPENPROGRAM_STRICT_TOOLS` | `0` = 关闭严格工具 schema（默认开） | `openprogram/providers/_schema/__init__.py` |
| `OPENPROGRAM_FORCE_IPV4` | `1` = 强制 IPv4 源地址（IPv6 网络异常时用） | `openprogram/providers/utils/http_client.py` |

### 调试

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_DEBUG_RUNTIME` | `1` = runtime 日志镜像到 stderr | `openprogram/webui/server.py` |
| `OPENPROGRAM_DEBUG_REGISTRY` | `1` = 显示函数注册表的导入失败 | `openprogram/programs/_registry.py` |
| `OPENPROGRAM_DEBUG_DISPATCHER` | `1` = dispatcher 调试日志 | `openprogram/agent/dispatcher/runtime_attach.py` |
| `OPENPROGRAM_DEBUG_PROVIDER` | `1` = provider 层调试日志 | `openprogram/providers/openai_codex/openai_codex.py` |

### 其他

代码里还有一批更内部的变量（HTTP/SSE 超时细调 `OPENPROGRAM_HTTPX_*` / `OPENPROGRAM_SSE_*`、TCP keepalive `OPENPROGRAM_TCP_*`、各 provider 单独的重试次数 `OPENPROGRAM_<PROVIDER>_MAX_RETRIES`、`OPENPROGRAM_JOB_WORKERS`、`OPENPROGRAM_IMAGE_DIR`、`OPENPROGRAM_BROWSER_CDP_URL` 等）。用 `grep -rn "OPENPROGRAM_" openprogram/` 可以列出全集；每个变量在定义处都有注释。
