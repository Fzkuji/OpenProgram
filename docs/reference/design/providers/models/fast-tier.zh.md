# Fast（高速）档 — 判定、存储与线路

本文记录 fast 功能的代码摆放、数据来源与判定规则。姊妹文档：
[`thinking-effort.md`](thinking-effort.md)（同为"模型能力 → UI 开关"的声明式
子系统，结构刻意对齐）。

## 1. Fast 是什么

思考强度弹窗右上角的仪表按钮控制高速模式。开了之后，请求按厂商协议带上高速档参数：

| 家族 | 线上形态 | 计费事实 |
|---|---|---|
| GPT 5.4 / 5.5 / 5.6 系 | 请求体 `service_tier: "priority"`（OpenAI 叫 priority processing） | Codex 订阅端把它列成每模型的档（"1.5x 速度、增加用量"）；哪些模型有这个档直接来自 `service_tiers`（§2.1），不靠猜 |
| Claude Opus 4.6 / 4.7 / 4.8 | 请求体 `speed: "fast"` + 头 `anthropic-beta: fast-mode-2026-02-01` | 同样按量计费；订阅账户没充 usage credits 时 Anthropic 返回 429 "Usage credits are required for fast mode"，**如实透传给界面** —— 报错是账户问题，不代表模型不支持 |

未列入表格不代表厂商没有高速档。xAI 官方 API 已有 Priority Processing；Grok 订阅线路的 Grok 4.6 走 CLI chat proxy，同样接受 `service_tier`，其他订阅模型仍保持未知。界面及按线路判定的扩展见 [Composer 速度控件设计](../../ui/composer-fast-control.html)，实现验证状态见设计页。

## 2. 按线路判定

`providers.fast.fast_capability` 由 agent settings 与请求分发共同使用，
`supports_fast` 保留为布尔兼容投影。配置显式 false 禁用；Codex 使用账号目录；
线路显式 true 可声明支持；Claude 保留已有声明。官方 xAI API 的 Grok 4.6
按 provider 和 endpoint 确认支持；Grok 订阅线路的 Grok 4.6 同样确认支持，
其他订阅模型保持未知，其他线路保留目录查询。

思考弹窗里的帮助图标改为用户提供的 GaugeIcon。开启不加边框或背景，指针保持
转动后的状态。设置按会话及 provider/model 保存，标准档覆盖 agent 默认值。
详细交互见 [Composer 速度控件设计](../../ui/composer-fast-control.html)。

### 2.1 codex 的官方数据源

`GET https://chatgpt.com/backend-api/codex/models?client_version=<ver>`
（官方 `codex` CLI 启动时拉的同一个账户级端点），用订阅 OAuth bearer +
`chatgpt-account-id` 授权。每个模型带 `service_tiers`（有 `id:"priority"`
就是有 fast 档）、`supported_reasoning_levels`（thinking 档）、真实
`context_window`（订阅端 372k，不是 API 平台的 1050k）。请求 / dispatch 都用
`originator: codex_cli_rs` + `version` 身份——后端对灰度 id（如
`gpt-5.6-luna`）按客户端身份放行，用别的 originator 会列表有、dispatch 404。

这里不用 models.dev，是因为它跟踪的是公开 API 平台目录而不是订阅入口：它会列出
账户跑不了的 id、给出 API 平台的 context 数字，而 fast 只能靠 id 前缀猜
（`gpt-5.4-mini` 会被误判）。官方端点这三样都权威。

## 3. 存储：先读官方 → 落 config → 之后读文件

codex 的原则：**优先读取官方实时目录，其次读取 OpenProgram 的 last-known-good 目录，最后读取官方 CLI 本地缓存**。

| 层 | 位置 | 持久化 |
|---|---|---|
| codex 官方端点与 CLI 缓存 | `openprogram/providers/openai_codex/list_models.py` | 优先实时端点；`~/.codex/models_cache.json` 仅作 stale 展示兜底 |
| OpenProgram 订阅目录 | `providers/subscription_catalog.py` | 原子写入 last-known-good；登录后及后台周期刷新 |
| config spec 行（含 `fast`/`thinking_levels`/`context`） | 官方刷新成功后自动新增、更新和淘汰账户模型，同时尊重用户关闭 tombstone | 配置文件 |
| `Model.fast` 字段 | `_build_model_from_row` 读 config 行的 `fast`（行有值就用，codex 行总带值）；注册表构建时进 `ENABLED_MODELS` | 仅内存（进程内 dict，源头是 config） |
| claude-code 手写表 | `providers/enabled_models.py::default_fast`（仅剩 Opus 部分在判定路径上） | 源码 |
| models.dev 目录 | `openprogram/providers/sources/models_dev.py` | 远端；1h 内存缓存，无磁盘缓存 |

数据流：**官方端点 → 归一化 → last-known-good 缓存 + config.json → 注册表 →
supports_fast / dispatch**。CLI 缓存可以让离线浏览继续显示，但它会同时携带错误标记，因此不会像新鲜目录一样增删已配置模型。

## 4. 事件流：任何切换自适应，无需刷新

```
连接建立 / 会话切换 / 模型切换 / 每轮消息 ack+结束
  → 前端 loadAgentSettings()（lib/runtime-bridge/providers.ts）
  → GET /api/agent_settings（apps/server/openprogram_server/_webui/routes/execution/runtime.py）
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
openprogram/providers/openai_codex/list_models.py                    官方端点拉取 + 归一化（fast/thinking/context 来源）
apps/server/openprogram_server/_webui/_model_listing/fetchers/__init__.py  编排：透传 fetcher 的 fast/thinking，enrich 不覆盖
apps/server/openprogram_server/_webui/_model_listing/listing.py        supports_fast 判定入口；list_models_for_provider 优先用 fetcher thinking
apps/server/openprogram_server/_webui/routes/execution/runtime.py                /api/agent_settings 下发 chat.fast
apps/web/lib/session-store/types.ts                     AgentBadgeInfo.fast 类型
apps/web/components/chat/composer/index.tsx             开关显隐 + 发送门控
```

改动指南：codex 的 fast/thinking 全自动，加/去模型什么都不用做——点 Fetch
重拉端点即可；判定逻辑由 `providers.fast.fast_capability` 统一维护；claude-code 加/去
fast → 动 `default_fast` 的 Opus 部分；其他 provider 使用已确认的线路配置或目录能力，不按模型名推定支持。

## 响应证据

Completions 与 Responses 在 Usage 中分别保留请求档位和实际返回档位。
每次调用的证据按列表累计，随 assistant 消息保存，普通聊天用量区域在重新读取后
仍能显示实际高速、实际标准和未确认的调用数量。Anthropic 已发送高速请求但没有
返回速度证据时标为未确认。xAI 返回的实际费用优先于目录估价；缺少高速价格证据
时记为未知，不直接使用标准档估价。
