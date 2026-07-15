# 配置参考

`~/.openprogram/config.json` 的键、`openprogram config` 能读写什么、以及环境变量汇总。日常改设置的入口见[配置与数据目录](../server/configuration.md)。

## openprogram config 能读写什么

```bash
openprogram config list              # 全部设置：值、分组、生效方式
openprogram config get ui.port
openprogram config set ui.web_port 8101
```

设置注册表定义在 `openprogram/config_schema.py`（单一事实来源，setup 向导、TUI 设置页、Web 设置页都从它渲染）。每个设置标注生效方式：`live` 立即生效，`next_start` 下次启动 worker 生效。

| key | 分组 | 含义 | 默认 | 生效 |
|-----|------|------|------|------|
| `ui.port` | Ports | backend（FastAPI，API + WebSocket）端口 | 18109 | next start |
| `ui.web_port` | Ports | frontend（Web UI）端口 | 18100 | next start |
| `ui.open_browser` | Ports | `openprogram web` 是否自动开浏览器 | true | next start |
| `search.default_provider` | Search | 默认 web 搜索 provider，`auto` 选优先级最高的已配置项 | auto | live |
| `memory.backend` | Memory | `local`（磁盘记忆工具）或 `none`（禁用） | local | next start |
| `tools.disabled.<name>` | Tools | 逐工具开关；写入的是 `tools.disabled` 列表的成员 | 全部启用 | live |
| `providers.<name>` | Providers | 只读状态行（是否已配置）；用 `openprogram providers login` 或 Web UI 配置 | — | — |

## config.json 顶层键

实际写入 `~/.openprogram/config.json` 的顶层键（不要手改，走 `openprogram config set` / setup 向导 / Web UI）：

| 键 | 含义 | 代码 |
|----|------|------|
| `ui` | `{port, web_port, open_browser}`，见上表 | `openprogram/config_schema.py` |
| `search` | `{default_provider}` | `openprogram/setup.py` |
| `tools` | `{disabled: [工具名, ...]}` | `openprogram/setup.py`、`openprogram/config_schema.py` |
| `default_provider` | 默认 LLM provider（setup 向导写入） | `openprogram/setup.py` |
| `default_model` | 默认模型（setup 向导写入） | `openprogram/setup.py` |
| `default_workdir` | agent 的默认工作目录 | `openprogram/paths.py` |
| `providers` | 每个 provider 的设置子树（启用的模型、自定义模型等），由 Web UI 模型列表管理 | `openprogram/providers/_config_read.py`、`openprogram/webui/_model_listing/storage.py` |
| `api_keys` | 环境变量名 → API key 的映射，setup 向导写入，worker 启动时导出到环境 | `openprogram/_setup_sections/sections.py`、`openprogram/webui/server.py` |
| `spec_migration_version` | 模型 spec 迁移的一次性标记，含义见代码 | `openprogram/webui/_model_listing/storage.py` |

## 环境变量

在启动 `openprogram`（或 worker）的 shell 里设置。全部逐个在代码里核实过；每行给出定义处。

### 路径与实例

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_PROFILE` | 状态目录 profile，等价 `--profile`，改道到 `~/.openprogram-<name>/` | `openprogram/paths.py` |
| `OPENPROGRAM_STATE_DIR` | 直接覆盖状态目录路径 | `openprogram/paths.py`（memory、rescue 提示均引用） |
| `OPENPROGRAM_HOME` | auth profiles 的替代基目录 | `openprogram/auth/profiles.py` |
| `OPENPROGRAM_WORKDIR` | agent 默认工作目录（优先于 config 的 `default_workdir`） | `openprogram/paths.py` |

### 端口与 web

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_BACKEND_PORT` | backend 端口（默认 18109）；优先级低于显式参数、高于持久化偏好 | `openprogram/worker/lifecycle.py`、`openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_WEB_PORT` | frontend 端口（默认 18100） | `openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_BACKEND_URL` | 前端访问 backend 的 URL（Next.js rewrites 读取），一般自动设置 | `openprogram/worker/web.py` |
| `OPENPROGRAM_NO_WEB` | `1` = worker 不启动 web 前端 | `openprogram/worker/web.py` |
| `OPENPROGRAM_WEB_NO_FRONTEND` | `1` = `openprogram web` 跳过前端只起 backend | `openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_DOCS_BASE` | 文档站的挂载路径（默认 `/docs/`，须以 `/` 开头和结尾） | `tools/docs_site/build.py` |

### 行为开关

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_NO_AUTO_WORKER` | `1` = TUI 不自动拉起 worker，只连已有的 | `openprogram/cli_ink.py` |
| `OPENPROGRAM_NO_AUTO_UPDATE` | `1` = 禁用自动更新 | `openprogram/updater/runner.py` |
| `OPENPROGRAM_NO_SLEEP` | `1` = 禁用记忆的 sleep 整理调度器 | `openprogram/memory/scheduler.py` |
| `OPENPROGRAM_NO_PROGRAMS_WATCH` | `1` = 禁用 programs 目录的文件监听 | `openprogram/functions/watcher.py` |
| `OPENPROGRAM_PROJECT_AUTOCOMMIT` | `0` = 关闭项目自动 commit | `openprogram/store/project/project_commit.py` |
| `OPENPROGRAM_WEBSEARCH_DISABLE` | 按名禁用某个 web 搜索 provider（如 `ollama`） | `openprogram/functions/tools/web_search/providers/ollama.py` |

### LLM 调用

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_MAX_RETRIES` | Runtime 的 API 瞬态故障重试次数（默认 6） | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_EXEC_TIMEOUT_S` | 单次 `runtime.exec` 的时间预算（秒） | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_FALLBACK_MODELS` | 逗号分隔的 `provider/model` 列表，主模型失败时按序切换 | `openprogram/providers/utils/failover.py` |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | 流式请求的最大重试次数 | `openprogram/providers/utils/stream_retry.py` |
| `OPENPROGRAM_STRICT_TOOLS` | `0` = 关闭严格工具 schema（默认开） | `openprogram/providers/_schema/__init__.py` |
| `OPENPROGRAM_FORCE_IPV4` | `1` = 强制 IPv4 源地址（IPv6 网络异常时用） | `openprogram/providers/utils/http_client.py` |

### 调试

| 变量 | 用途 | 代码 |
|------|------|------|
| `OPENPROGRAM_DEBUG_RUNTIME` | `1` = runtime 日志镜像到 stderr | `openprogram/webui/server.py` |
| `OPENPROGRAM_DEBUG_REGISTRY` | `1` = 显示函数注册表的导入失败 | `openprogram/functions/_registry.py` |
| `OPENPROGRAM_DEBUG_DISPATCHER` | `1` = dispatcher 调试日志 | `openprogram/agent/dispatcher/runtime_attach.py` |
| `OPENPROGRAM_DEBUG_PROVIDER` | `1` = provider 层调试日志 | `openprogram/providers/openai_codex/openai_codex.py` |
| `OPENPROGRAM_EVENT_LOG` | `1` 或文件路径 = 把每个类型化事件追加为 JSON 行 | `openprogram/agent/event_bus.py` |

### 其他

代码里还有一批更内部的变量（HTTP/SSE 超时细调 `OPENPROGRAM_HTTPX_*` / `OPENPROGRAM_SSE_*`、TCP keepalive `OPENPROGRAM_TCP_*`、各 provider 单独的重试次数 `OPENPROGRAM_<PROVIDER>_MAX_RETRIES`、`OPENPROGRAM_TASK_WORKERS`、`OPENPROGRAM_IMAGE_DIR`、`OPENPROGRAM_BROWSER_CDP_URL` 等）。用 `grep -rn "OPENPROGRAM_" openprogram/` 可以列出全集；每个变量在定义处都有注释。
