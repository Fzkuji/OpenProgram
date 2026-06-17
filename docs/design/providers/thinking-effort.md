# Thinking / Effort 子系统设计

## 1. 问题

不同 LLM provider 用不同的方式控制推理深度：

- Anthropic：`output_config.effort`（字符串）或 `thinking.budget_tokens`（数字）
- OpenAI：`reasoning_effort`（字符串）或 `reasoning.effort`（字符串）
- Gemini：`thinking_budget`（数字）
- 且各家接受的级别不同（OpenAI 没有 `max`，Gemini 用数字不用字符串）

框架需要给用户一个统一的 UI 和一套统一的级别名，然后翻译成各 provider 的 API 参数。

## 2. 外部框架对比

调研了三个对标框架（Claude Code、OpenCode、OpenClaw）的做法：

| | Claude Code | OpenCode | OpenClaw |
|---|---|---|---|
| 级别数 | 1（全局默认） | 7（none~max） | 5（off~high） |
| 映射方式 | 不映射（只连 Anthropic） | 各 provider 声明支持的子集 + protocol adapter 翻译 | 固定列表，不区分 provider |
| 长度控制 | 不控制，API 自适应 | 不控制（effort 模式）/ budget_tokens（旧模式） | 不控制，传字符串给 API |
| 新增级别 | 改配置 | 改统一枚举，编译器报出未处理的 provider | 改常量 |
| 新增模型 | 不需要 | modelID 关键词匹配默认配置 | catalog 加 `reasoning: true` |

**关键结论**：
1. 长度控制正在退出——Anthropic 废弃了 budget_tokens，趋势是调用方只传深度级别，长度由 API 自适应
2. OpenCode 的"provider 声明支持子集"最可维护——新增级别时有编译期检查
3. 所有框架都不做 per-model 的映射表——模型只声明"能不能 think"，不声明映射

## 3. 设计原则

1. **框架统一级别，provider 各自翻译**——用户看到的永远是同一套级别名
2. **长度不由框架控制**——框架只传深度级别，API 自己决定想多少 token。对少数需要 token 数的旧 API（Gemini、Anthropic budget 模式），有 budget 映射表兜底，但这是过渡方案
3. **映射逻辑集中管理**——不散在各 provider 的 stream_simple 里（当前实现的问题，见 §6）
4. **新模型零配置**——模型只需声明 `reasoning: true`，级别列表和映射自动推导

## 4. 级别定义

```
ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
```

加 `"off"` 表示关闭。定义在 `providers/types.py:52`。

| 级别 | 含义 | 适用场景 |
|---|---|---|
| off | 不推理 | 分类、简单问答 |
| minimal | 极简推理 | 快速确认 |
| low | 轻度推理 | 延迟敏感 |
| medium | 平衡 | 通用默认 |
| high | 深度推理 | 复杂分析 |
| xhigh | 扩展推理 | coding/agentic |
| max | 最大推理 | 正确性优先于成本 |

各 provider 默认值：anthropic/claude-code `high`，openai/openai-codex `xhigh`，gemini `auto`。

## 5. 架构分层

```
┌─────────────────────────────────────────────────┐
│ UI 层                                            │
│ thinking-effort-pill.tsx + use-thinking-effort.ts │
│ 从 /api/agent_settings 拿可用级别，渲染滑块       │
└──────────────┬──────────────────────────────────┘
               │ WS payload: thinking_effort="high"
┌──────────────▼──────────────────────────────────┐
│ Session 层                                       │
│ dispatcher → reasoning_from_config() → "high"    │
│ 存 SessionDB，per-session 持久化                  │
└──────────────┬──────────────────────────────────┘
               │ SimpleStreamOptions(reasoning="high")
┌──────────────▼──────────────────────────────────┐
│ Provider 翻译层                                   │
│ 读 model 的能力声明 + effort_map                  │
│ 翻译成 API 请求体参数                             │
└──────────────┬──────────────────────────────────┘
               │ {"thinking":{"type":"adaptive"},
               │  "output_config":{"effort":"high"}}
┌──────────────▼──────────────────────────────────┐
│ API                                              │
│ 长度由 API 自适应，框架不控制                      │
└─────────────────────────────────────────────────┘
```

### 5.1 UI → 后端

1. 页面加载时，前端请求 `/api/agent_settings`
2. 后端调 `get_thinking_config_for_model(provider, model_id)` 返回该模型的可用级别列表
3. 前端渲染滑块（0 个选项→隐藏，1 个→固定图标，2+→交互滑块）
4. 用户选中级别，发消息时随 WS payload 送达后端

### 5.2 后端 → Provider

1. dispatcher 从 `req.thinking_effort` 或 agent profile 读取级别
2. `reasoning_from_config()` 规范化（"off"→`None`，"none"→"off"）
3. 传给 `AgentLoopConfig.reasoning`
4. agent_loop 构造 `SimpleStreamOptions(reasoning="high")`
5. provider 的 `stream_simple()` 翻译成 API 请求体

## 6. Provider 翻译层

详见 [`model-catalog-final.md`](model-catalog-final.md) §3-4。

**核心机制**：每个 provider 的 `models.json` 里声明 `provider.thinking`，包含 `wire_format`（用字符串还是数字）、`effort_map`（框架级别→API 值）或 `budget_map`（框架级别→token 数）。provider 的 `stream_simple()` 读这个配置做翻译，不 hardcode 映射 dict。

**当前状态**：映射还 hardcode 在各 provider 的代码里（`_EFFORT_MAP`、`_THINKING_BUDGETS`、`budget_map` 等），`models.json` 机制尚未落地。`"max"` 级别在 5 个 provider 的映射表里缺失。迁移步骤见 model-catalog-final.md §6。

## 7. Model 能力声明

每个 Model 对象上的 thinking 字段（`providers/types.py`）：

| 字段 | 类型 | 来源（目标） |
|---|---|---|
| `reasoning` | `bool` | `models.json` 里每个模型的 `reasoning` 字段 |
| `thinking_levels` | `list[ThinkingLevel]` | 从 `provider.thinking.effort_map`（或 `budget_map`）的 key 自动推导 |
| `default_thinking_level` | `ThinkingLevel \| None` | `provider.thinking.default_effort` |
| `thinking_variant` | `str \| None` | 模型条目的 `thinking_override.variant`（仅少数模型需要） |

### 7.1 目标 filling 链

```
models.json (每个 provider 文件夹里)
  ↓ provider.thinking.effort_map 的 keys
  ↓ + model.reasoning: true/false
  ↓ + model.thinking_override（如果有）
Model.thinking_levels / default_thinking_level / thinking_variant
```

规则简化为：`reasoning=False` → 空列表（UI 隐藏）；`reasoning=True` → 用映射表的 key 作为级别列表；有 `thinking_override` 的模型用 override 的 key。

不再需要 `derive_thinking_fields()`、`THINKING_OVERRIDES`、`supports_xhigh()` 等推导逻辑——映射表有哪些 key，就支持哪些级别。

### 7.2 新模型怎么生效

1. `models.json` 里加一条 `"reasoning": true`（或 Fetch Models 从 API 拉到）
2. 框架从 `provider.thinking.effort_map` 的 key 推导出 `thinking_levels`
3. UI 从 `/api/agent_settings` 拿到级别列表，渲染滑块
4. 不需要改任何代码

### 7.3 per-model 覆盖

少数模型的 wire 格式和同 provider 其他模型不同时（如 Anthropic Opus 4.7），在模型条目里加 `thinking_override`。详见 model-catalog-final.md §3.2。

## 8. UI Picker 配置

`_thinking.py` 的 `get_thinking_config_for_model(provider, model_id)` 决定前端看到什么。

查找顺序：
1. Model 的 `thinking_levels` 非空 → 直接用
2. Model 有 `reasoning=True` 但 `thinking_levels` 为空 → `derive_thinking_fields()` 动态生成
3. Model `reasoning=False` → 隐藏菜单
4. Model 不在 catalog → 查 `THINKING_OVERRIDES`
5. 都没有 → provider 级别的 `THINKING_CONFIGS` 兜底

前端组件：
- `thinking-effort-pill.tsx`：0 选项→隐藏，1 选项→固定图标，2+→滑块
- `use-thinking-effort.ts`：读 `window._thinkingConfig`，模型切换时 clamp 到有效范围
- 颜色：off=灰，minimal→max 黄→红渐变

## 9. Session 持久化

`SessionRunConfig.thinking_effort` 存在 SessionDB（per-session）。
`_normalize_thinking()` 规范化：`"none"`→`"off"`，无效值→`None`。
`reasoning_from_config()`：`"off"`→`None`（不启用推理），其他原样返回。

## 10. 待修项

| 项目 | 优先级 | 说明 |
|---|---|---|
| `"max"` 在 5 个 provider 的映射缺口 | 高 | Anthropic/Bedrock/OpenAI Completions/Gemini 的映射表没有 max，fallback 到错误值 |
| Opus 4.7 override 限制了级别 | 中 | 只有 [low,medium,high]，API 实际支持 xhigh/max |
| 映射 hardcode 在 provider 代码里 | 中 | 应提取为 ProviderThinkingSpec（§6.3），新增级别时不必逐个 provider 改 |
| 前端 clamp 依赖 500ms 轮询 | 低 | 长期应改成 WS broadcast |
| 缺少 Fable 5 的说明 | 低 | thinking 始终开启，不能 disabled |

## 11. 文件清单

| 文件 | 职责 |
|---|---|
| `providers/types.py:52` | ThinkingLevel 类型 |
| `providers/types.py:310-320` | Model 的 thinking 字段 |
| `providers/types.py:160` | SimpleStreamOptions.reasoning |
| `providers/thinking_catalog.py` | THINKING_OVERRIDES + derive_thinking_fields + apply_thinking_catalog |
| `webui/_thinking.py` | THINKING_CONFIGS + get_thinking_config_for_model + apply_thinking_effort |
| `agent/session_config.py` | VALID_THINKING + reasoning_from_config + _normalize_thinking |
| `agent/dispatcher/__init__.py:839` | 读 thinking_effort 传给 agent_loop |
| `agent/agent_loop.py:412` | 构造 SimpleStreamOptions(reasoning=...) |
| `providers/anthropic/anthropic.py:168,663` | Anthropic _EFFORT_MAP + wire 翻译 |
| `providers/openai_codex/openai_codex.py:333` | Codex wire 翻译 |
| `providers/openai_completions/openai_completions.py:313` | OpenAI Completions wire 翻译 |
| `providers/openai_responses/openai_responses.py:126` | OpenAI Responses wire 翻译 |
| `providers/google/google.py:143` | Gemini wire 翻译 |
| `providers/amazon_bedrock/amazon_bedrock.py:81` | Bedrock wire 翻译 |
| `providers/anthropic/_claude_code_direct_runtime.py` | claude-code 模型注册 |
| `providers/_shared/simple_options.py:57` | clamp_reasoning + adjust_max_tokens_for_thinking |
| `providers/models.py:83` | supports_xhigh 判断 |
| `web/components/chat/composer/controls/thinking-effort-pill.tsx` | 前端 UI |
| `web/components/chat/composer/controls/use-thinking-effort.ts` | 前端 hook |
| `web/lib/runtime-bridge/providers.ts:127` | window._thinkingConfig 注入 |
| `webui/routes/runtime.py:215` | /api/agent_settings 返回 thinking 配置 |
