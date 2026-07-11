# Fast（高速）档 — 判定、存储与线路

状态：已落地（2026-07-12，用户逐条裁决）。本文记录 fast 功能的代码摆放、
数据来源与判定规则，防遗忘。姊妹文档：[`thinking-effort.md`](thinking-effort.md)
（同为"模型能力 → UI 开关"的声明式子系统，结构刻意对齐）。

## 1. Fast 是什么

聊天输入框 + 菜单里的"高速"开关。开了之后，请求按厂商协议带上高速档参数：

| 家族 | 线上形态 | 计费事实 |
|---|---|---|
| GPT 5.4 / 5.5 / 5.6 系 | 请求体 `service_tier: "priority"`（OpenAI 叫 priority processing） | Codex 订阅端把它列成每模型的档（"1.5x 速度、增加用量"）；哪些模型有这个档直接来自 `service_tiers`（§2.1），不靠猜 |
| Claude Opus 4.6 / 4.7 / 4.8 | 请求体 `speed: "fast"` + 头 `anthropic-beta: fast-mode-2026-02-01` | 同样按量计费；订阅账户没充 usage credits 时 Anthropic 返回 429 "Usage credits are required for fast mode"（2026-07-12 实测），**如实透传给界面**——报错是账户问题，不代表模型不支持 |

其他所有模型（Gemini / DeepSeek / Qwen / Llama / MiniMax …）没有 fast 概念。

## 2. 判定：`supports_fast(provider, model)` 三支

入口：`openprogram/webui/_model_listing/listing.py`。判定前先剥
`"provider:"` 线格式前缀（运行时把当前模型记成 `openai-codex:gpt-5.5`）。

1. **openai-codex → 读注册表落盘的 `Model.fast`**。这个字段不是手写的，
   来自官方 codex models 端点（见 §2.1），Fetch 时随 spec 一起写进 config，
   判定时 `get_model("openai-codex", id).fast` 直接读文件。`gpt-5.4-mini`
   这类没有 fast 档的会精确判 False（旧手写前缀表会误判 True）。
2. **claude-code → 手写声明表** `enabled_models.default_fast(model_id)`：
   id 含 `opus-4-6/4-7/4-8`（连字符或点号写法）→ True。订阅端还没验证有没有
   可拉的 models 端点，暂留手写；能拉了就照 codex 的样子换成端点落盘。
3. **其余 provider → models.dev 全自动**：该模型有 `service_tier ==
   "priority"` 或 `id == "fast"` 的档 → True；没有、或目录不认识 → False。

裁决记录：私有网关（如 frontier-intelligence）不特判——目录不认识就没有
fast 按钮。需要例外时用 config 显式覆盖（§3）。

### 2.1 codex 的官方数据源

`GET https://chatgpt.com/backend-api/codex/models?client_version=<ver>`
（官方 `codex` CLI 启动时拉的同一个账户级端点），用订阅 OAuth bearer +
`chatgpt-account-id` 授权。每个模型带 `service_tiers`（有 `id:"priority"`
就是有 fast 档）、`supported_reasoning_levels`（thinking 档）、真实
`context_window`（订阅端 372k，不是 API 平台的 1050k）。请求 / dispatch 都用
`originator: codex_cli_rs` + `version` 身份——后端对灰度 id（如
`gpt-5.6-luna`）按客户端身份放行，用别的 originator 会列表有、dispatch 404。

弃用 models.dev 的原因：它跟踪的是公开 API 平台目录，不是订阅入口。id 会漏
账户跑不了的模型、context 是 API 平台数字、fast 靠 id 前缀猜（`gpt-5.4-mini`
误判）。官方端点这三样都权威。

## 3. 存储：先读官方 → 落 config → 之后读文件

codex 的原则：**信息全从官网实时拿，不手写任何模型清单**。

| 层 | 位置 | 持久化 |
|---|---|---|
| codex 官方端点 | `webui/_model_listing/fetchers/codex.py::_fetch_codex_live` | 远端；`_browse_models` 10 分钟内存缓存，**无磁盘缓存** |
| config spec 行（含 `fast`/`thinking_levels`/`context`） | 用户启用某模型时，`fetch_and_normalize` 归一化后的整行写进 `~/.openprogram/config.json`；Fetch 按钮（`fetch_models_remote`）用新端点数据 heal 已启用行 | 配置文件（这就是"存文件"这一环） |
| `Model.fast` 字段 | `_build_model_from_row` 读 config 行的 `fast`（行有值就用，codex 行总带值）；注册表构建时进 `ENABLED_MODELS` | 仅内存（进程内 dict，源头是 config） |
| claude-code 手写表 | `providers/enabled_models.py::default_fast`（仅剩 Opus 部分在判定路径上） | 源码 |
| models.dev 目录 | `webui/_model_listing/sources/models_dev.py` | 远端；1h 内存缓存，无磁盘缓存 |

数据流：**官方端点 → 归一化 → config.json → 注册表 → supports_fast /
dispatch**。断网 / 没登录时端点返回 error、保留已存 config 行不覆盖——反正没
token 也 dispatch 不了这些模型，token-less 浏览拿不到列表不算回归。

## 4. 事件流：任何切换自适应，无需刷新

```
连接建立 / 会话切换 / 模型切换 / 每轮消息 ack+结束
  → 前端 loadAgentSettings()（lib/runtime-bridge/providers.ts）
  → GET /api/agent_settings（webui/routes/runtime.py）
      chat.fast = supports_fast(当前会话的 provider, model)   ← 每次现算
  → zustand agentSettings.chat.fast
  → composer 订阅重渲染：显/隐 "高速" 菜单项与 chip
```

发送侧双保险：composer 只在 `fastEnabled && fastSupported` 时给消息带
`service_tier: "priority"`（切到不支持的模型后，残留的会话级 fast 设置
不会发出去）。

## 5. 线路侧（请求构建器）

| 构建器 | 行为 |
|---|---|
| `providers/openai_responses` / `openai_completions` | `opts.service_tier` → 请求体 `service_tier`（原有行为） |
| `providers/openai_codex`（ChatGPT 订阅） | 同上透传 `opts.service_tier` → 请求体；dispatch 用 `originator: codex_cli_rs` + `version` 身份（后端对灰度 id 按客户端身份放行，见 §2.1） |
| `providers/anthropic` | `opts.service_tier` 存在 **且** `model.fast` 为真 → 请求体 `extra_body={"speed":"fast"}` + beta 头 `_BETA_FAST`（`_build_client(fast=...)` 追加，不覆盖其他 beta） |
| 其他线路 | 不透传，参数不出网 |

## 6. 文件地图

```
openprogram/providers/types.py                     Model.fast 字段
openprogram/providers/enabled_models.py            default_fast（仅 claude-code Opus）+ 配置行回填
openprogram/providers/openai_codex/{openai_codex,runtime}.py   service_tier 透传；codex_cli_rs 身份 + _CODEX_CLIENT_VERSION
openprogram/providers/anthropic/{anthropic,_claude_code_direct_runtime}.py  Claude fast 线路 + 注册回填
openprogram/webui/_model_listing/fetchers/codex.py 官方端点拉取 + 归一化（fast/thinking/context 来源）
openprogram/webui/_model_listing/fetchers/__init__.py  编排：透传 fetcher 的 fast/thinking，enrich 不覆盖
openprogram/webui/_model_listing/listing.py        supports_fast 判定入口；list_models_for_provider 优先用 fetcher thinking
openprogram/webui/routes/runtime.py                /api/agent_settings 下发 chat.fast
web/lib/session-store/types.ts                     AgentBadgeInfo.fast 类型
web/components/chat/composer/index.tsx             开关显隐 + 发送门控
```

改动指南：codex 的 fast/thinking 全自动，加/去模型什么都不用做——点 Fetch
重拉端点即可；换判定逻辑 → 只动 `listing.py::supports_fast`；claude-code 加/去
fast → 动 `default_fast` 的 Opus 部分；其他 provider 想要 fast → models.dev
认识它就自动生效。
