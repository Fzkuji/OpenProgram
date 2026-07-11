# Fast（高速）档 — 判定、存储与线路

状态：已落地（2026-07-12，用户逐条裁决）。本文记录 fast 功能的代码摆放、
数据来源与判定规则，防遗忘。姊妹文档：[`thinking-effort.md`](thinking-effort.md)
（同为"模型能力 → UI 开关"的声明式子系统，结构刻意对齐）。

## 1. Fast 是什么

聊天输入框 + 菜单里的"高速"开关。开了之后，请求按厂商协议带上高速档参数：

| 家族 | 线上形态 | 计费事实 |
|---|---|---|
| GPT 5.4 / 5.5 / 5.6 系 | 请求体 `service_tier: "priority"`（OpenAI 叫 priority processing） | API 按量计费的加价档，**不是订阅功能** |
| Claude Opus 4.6 / 4.7 / 4.8 | 请求体 `speed: "fast"` + 头 `anthropic-beta: fast-mode-2026-02-01` | 同样按量计费；订阅账户没充 usage credits 时 Anthropic 返回 429 "Usage credits are required for fast mode"（2026-07-12 实测），**如实透传给界面**——报错是账户问题，不代表模型不支持 |

其他所有模型（Gemini / DeepSeek / Qwen / Llama / MiniMax …）没有 fast 概念。

## 2. 判定：`supports_fast(provider, model)` 两层

入口：`openprogram/webui/_model_listing/listing.py`。判定前先剥
`"provider:"` 线格式前缀（运行时把当前模型记成 `openai-codex:gpt-5.5`）。

1. **订阅入口手写声明**——`openai-codex`、`claude-code`（集合
   `_SUBSCRIPTION_FAST_PROVIDERS`）。这两个是订阅入口，公开目录不收录，
   查 `enabled_models.default_fast(model_id)` 家族表：
   `gpt-5.4/5.5/5.6` 开头 → True；id 含 `opus-4-6/4-7/4-8`（连字符或点号
   写法）→ True；其他 → False。
2. **其余 provider 全自动**——查 models.dev 的 `speed_modes`：该模型有
   `service_tier == "priority"` 的档或 `id == "fast"` 的档 → True；
   没有、或目录不认识这个 provider → False。

裁决记录：只有这两个订阅入口享受手写；私有网关（如 frontier-intelligence）
不特判——目录不认识就没有 fast 按钮。需要例外时用 config 显式覆盖（§3）。

## 3. 存储：规则 + 现算，基本不落盘

| 层 | 位置 | 持久化 |
|---|---|---|
| 家族声明表 | `providers/enabled_models.py::default_fast` + `listing.py::_SUBSCRIPTION_FAST_PROVIDERS` | 源码 |
| 用户显式覆盖 | `~/.openprogram/config.json` 模型 spec 行的 `"fast": true/false`（行值优先，`_build_model_from_row` 尊重它） | 配置文件（可选，当前无人使用） |
| `Model.fast` 字段 | 注册表构建 / 动态注册（codex `ensure_codex_model_registered`、anthropic `ensure_anthropic_model_registered`）时按声明表回填 | 仅内存（`ENABLED_MODELS` 是进程内 dict） |
| models.dev 目录 | `webui/_model_listing/sources/models_dev.py`，`https://models.dev/api.json` | 远端；本地只有 1h 内存缓存（失败 60s 重试），**无磁盘缓存** |

已知短板：断网 / models.dev 不可用且内存缓存过期时，自动检测层全部返回
False（订阅入口不受影响）。兜底方案（未做）：把最近一次成功拉取的目录落盘
`~/.openprogram/cache/`。

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
| `providers/openai_codex`（ChatGPT 订阅） | 同上透传（2026-07-12 新增；后端不认时按 OpenAI 惯例忽略未知字段） |
| `providers/anthropic` | `opts.service_tier` 存在 **且** `model.fast` 为真 → 请求体 `extra_body={"speed":"fast"}` + beta 头 `_BETA_FAST`（`_build_client(fast=...)` 追加，不覆盖其他 beta） |
| 其他线路 | 不透传，参数不出网 |

## 6. 文件地图

```
openprogram/providers/types.py                     Model.fast 字段
openprogram/providers/enabled_models.py            default_fast 声明表 + 配置行回填
openprogram/providers/openai_codex/{openai_codex,runtime}.py   codex 透传 + 注册回填
openprogram/providers/anthropic/{anthropic,_claude_code_direct_runtime}.py  Claude fast 线路 + 注册回填
openprogram/webui/_model_listing/listing.py        supports_fast 判定入口；_model_to_dict 透出 fast
openprogram/webui/_model_listing/sources/models_dev.py  目录拉取与 1h 内存缓存
openprogram/webui/routes/runtime.py                /api/agent_settings 下发 chat.fast
web/lib/session-store/types.ts                     AgentBadgeInfo.fast 类型
web/components/chat/composer/index.tsx             开关显隐 + 发送门控
```

改动指南：给某模型加/去 fast → 只动 `default_fast` 声明表（或该模型的
config 行）；换判定逻辑 → 只动 `listing.py::supports_fast`；新 provider
想要 fast → 什么都不用做（models.dev 认识它就自动生效）。
