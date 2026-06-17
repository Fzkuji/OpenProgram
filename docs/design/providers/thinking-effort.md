# Thinking / Effort 子系统设计

## 1. 概述

控制 LLM 调用时的推理深度。不同 provider 用不同的 API 参数名和格式,但 OpenProgram 统一抽象成一套 `ThinkingLevel` + UI picker,对用户暴露一致的体验。

核心思路:用户在 UI 上选一个 effort 级别 → 存进 session config → 每次 LLM 调用时传给 provider → provider 翻译成各自 API 的参数格式。

## 2. 级别定义

```
ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
```

加上 `"off"` 表示关闭推理。定义在 `providers/types.py`。

| 级别 | 含义 | 适用场景 |
|---|---|---|
| off | 不推理 | 分类、简单问答 |
| minimal | 极简推理 | 快速确认 |
| low | 轻度推理 | 简短、延迟敏感的任务 |
| medium | 平衡 | 通用默认 |
| high | 深度推理 | 复杂分析、agent |
| xhigh | 扩展推理 | coding/agentic(Claude Code 默认) |
| max | 最大推理 | 正确性优先于成本的场景 |

各 provider 的默认值不同:
- anthropic / claude-code: `high`
- openai / openai-codex: `xhigh`
- gemini: `auto`(provider 特有值,不在 ThinkingLevel 里)

## 3. 数据流

```
UI picker
  ↓ (WS payload: thinking_effort="high")
dispatcher (process_user_turn)
  ↓ reasoning_from_config(SessionRunConfig(thinking_effort=...))
  ↓ 返回 ThinkingLevel | None ("off" → None)
AgentLoopConfig.reasoning
  ↓
agent_loop
  ↓ SimpleStreamOptions(reasoning="high")
provider.stream_simple()
  ↓ 翻译成 API 请求体参数
API 请求
```

### 3.1 前端 → 后端

1. 用户在 composer 底部的 effort pill 上选择级别
2. 前端 hook (`use-thinking-effort.ts`) 从 `window._thinkingConfig` 读取可用选项
3. `window._thinkingConfig` 由 `providers.ts` 的 `loadAgentSettings()` 从 `/api/agent_settings` 注入
4. `/api/agent_settings` 调用 `get_thinking_config_for_model(provider, model_id)` 返回 picker 配置
5. 用户发消息时,选中的 effort 随 WS payload 发给后端

### 3.2 后端 → Provider

1. `dispatcher/__init__.py` 从 `req.thinking_effort` 或 agent profile 读取
2. `reasoning_from_config()` 规范化("off" → `None`, "none" → "off")
3. 传给 `AgentLoopConfig.reasoning`
4. `agent_loop.py` 构造 `SimpleStreamOptions(reasoning=level)`
5. 各 provider 的 `stream_simple()` 把 `reasoning` 翻译成 API 请求体

## 4. Model 上的 thinking 字段

每个 `Model` 对象有三个 thinking 字段(定义在 `providers/types.py`):

| 字段 | 类型 | 用途 |
|---|---|---|
| `reasoning` | `bool` | 这个模型是否支持推理(快速判断) |
| `thinking_levels` | `list[ThinkingLevel]` | 该模型支持哪些级别(UI picker 的选项源;空列表 = 隐藏菜单) |
| `default_thinking_level` | `ThinkingLevel \| None` | 切换到该模型时的默认值 |
| `thinking_variant` | `str \| None` | 标记特殊 wire 格式(如 Opus 4.7 用 `"opus47"`) |

### 4.1 字段填充

三个来源,优先级从高到低:

1. **THINKING_OVERRIDES**(`thinking_catalog.py`):手动覆盖,用于行为偏离默认的模型(如 Opus 4.7)
2. **derive_thinking_fields()**:根据 `reasoning` bool + `supports_xhigh` 自动生成默认级别列表
3. **静态 catalog JSON**(`_catalog/*.json`, `_catalog/fetched/*.json`):模型数据源

`apply_thinking_catalog()` 在 `models.py` 模块加载时遍历所有 Model,调 `derive_thinking_fields()` 填充。

默认生成逻辑:
- `reasoning=False` → `thinking_levels=[]`
- `reasoning=True, supports_xhigh=True` → `[minimal, low, medium, high, xhigh, max]`, default `xhigh`
- `reasoning=True, supports_xhigh=False` → `[minimal, low, medium, high, max]`, default `medium`
- 某些模型不支持 `minimal`(如 gpt-5.5),由 `supports_minimal_effort()` 判断

## 5. UI Picker 配置

`_thinking.py` 的 `get_thinking_config_for_model(provider, model_id)` 是核心函数,决定前端显示什么。

查找顺序:
1. Model 对象的 `thinking_levels`(有且非空 → 直接用)
2. Model 有 `reasoning=True` 但 `thinking_levels` 为空 → 调 `derive_thinking_fields()` 动态生成
3. Model `reasoning=False` → 隐藏菜单(`options=[]`)
4. Model 不在 catalog → 查 `THINKING_OVERRIDES`
5. 全都没有 → 用 provider 级别的 `THINKING_CONFIGS` 兜底

### 5.1 前端组件

- `thinking-effort-pill.tsx`:渲染 UI(0 选项→不显示,1 选项→固定图标,2+→交互式滑块)
- `use-thinking-effort.ts`:从 `window._thinkingConfig` 读选项,存选中值;模型切换时 clamp 到有效范围
- 颜色渐变:off=灰,minimal→max 从黄到红

## 6. 各 Provider 的 wire 格式——完整映射表

`SimpleStreamOptions.reasoning` 是统一入口（一个字符串如 `"high"`）。各 provider 在 `stream_simple()` 里把它翻译成各自 API 的请求体参数。以下是每个 provider 的**完整翻译逻辑**，包括判断条件、映射表、代码位置。

### 6.1 Anthropic（`providers/anthropic/anthropic.py`）

**分支判断**（第 663-679 行）：

```
if opts.reasoning:
    if _supports_adaptive_thinking(model.id) or is_oauth:
        → adaptive 路径
    else:
        → budget 路径
```

`_supports_adaptive_thinking()`（第 197 行）：模型 id 包含 `opus-4-6` 或 `sonnet-4-6` 返回 True。OAuth token（`sk-ant-oat` 开头）也走 adaptive。

#### Adaptive 路径（Opus 4.6+, Sonnet 4.6, 或 OAuth 认证）

框架级别 → API 请求体：

```json
{
  "thinking": {"type": "adaptive"},
  "output_config": {"effort": "<映射后的值>"}
}
```

映射表（`_map_thinking_level_to_effort`，第 205 行 + `_EFFORT_MAP`，第 168 行）：

| 框架 ThinkingLevel | Anthropic effort 值 | 备注 |
|---|---|---|
| `"minimal"` | `"low"` | Anthropic 没有 minimal，降到 low |
| `"low"` | `"low"` | 直传 |
| `"medium"` | `"medium"` | 直传 |
| `"high"` | `"high"` | 直传 |
| `"xhigh"` | `"max"` | 仅 Opus 4.6 支持 max；Sonnet 4.6 降到 `"high"` |
| `"max"` | — | **当前未映射**（不在 _EFFORT_MAP 里，fallback 到 `"high"`）⚠️ |

#### Budget 路径（旧模型，Opus 4.5 及以前）

框架级别 → API 请求体：

```json
{
  "thinking": {"type": "enabled", "budget_tokens": N}
}
```

映射表（`_THINKING_BUDGETS`，第 159 行）：

| 框架 ThinkingLevel | budget_tokens |
|---|---|
| `"minimal"` | 1024 |
| `"low"` | 4096 |
| `"medium"` | 8192 |
| `"high"` | 16000 |
| `"xhigh"` | 32000 |
| `"max"` | **未定义**（fallback 到 8192）⚠️ |

如果 `budget_tokens >= max_tokens`，自动调整 `max_tokens = budget + max_tokens`。

`ThinkingBudgets`（`SimpleStreamOptions.thinking_budgets`）可以自定义覆盖上表。

### 6.2 OpenAI Codex（`providers/openai_codex/openai_codex.py`）

**Responses API 格式**。

预处理（第 333-334 行）：

```python
reasoning = opts.reasoning  # 框架传入的字符串
reasoning_effort = reasoning if supports_xhigh(model) else clamp_reasoning(reasoning)
```

- `supports_xhigh()`（`models.py:83`）：gpt-5.2/5.3/5.4/5.5 返回 True
- `clamp_reasoning()`（`_shared/simple_options.py:57`）：`"xhigh"` → `"high"`，其他原样

请求体（`_build_request_body`，第 392 行）：

```json
{
  "reasoning": {
    "effort": "<reasoning_effort 值>",
    "summary": "auto"
  },
  "include": ["reasoning.encrypted_content"]
}
```

| 框架 ThinkingLevel | 支持 xhigh 的模型 | 不支持 xhigh 的模型 |
|---|---|---|
| `"minimal"` | `"minimal"` | `"minimal"` |
| `"low"` | `"low"` | `"low"` |
| `"medium"` | `"medium"` | `"medium"` |
| `"high"` | `"high"` | `"high"` |
| `"xhigh"` | `"xhigh"` | `"high"`（clamp） |
| `"max"` | `"max"` | `"max"` |

仅当 `model.reasoning == True` 且 `reasoning_effort` 非空时才加 reasoning 字段（第 394 行判断）。

### 6.3 OpenAI Completions（`providers/openai_completions/openai_completions.py`）

**Chat Completions API 格式**（用于标准 OpenAI 模型）。

请求体（第 313-315 行）：

```json
{
  "reasoning_effort": "<映射后的值>"
}
```

映射表（hardcoded，第 314 行）：

| 框架 ThinkingLevel | OpenAI reasoning_effort |
|---|---|
| `"minimal"` | `"low"` |
| `"low"` | `"low"` |
| `"medium"` | `"medium"` |
| `"high"` | `"high"` |
| `"xhigh"` | `"high"` |
| `"max"` | **未定义**（fallback 到 `"medium"`）⚠️ |

### 6.4 OpenAI Responses（`providers/openai_responses/openai_responses.py`）

**和 Codex 基本相同**，但走标准 OpenAI Responses 端点。

预处理（第 126-127 行）：

```python
reasoning_effort = supports_xhigh(model) and reasoning or clamp_reasoning(reasoning)
```

请求体（第 220-228 行）：和 Codex 相同的 `reasoning.effort` + `reasoning.summary` 格式。

### 6.5 Google Gemini（`providers/google/google.py`）

**Gemini API 用 `thinking_budget` 数字**，不是字符串。

请求体（第 143-147 行）：

```python
gtypes.ThinkingConfig(thinking_budget=<数字>)
```

映射表（hardcoded，第 144 行）：

| 框架 ThinkingLevel | thinking_budget |
|---|---|
| `"minimal"` | 512 |
| `"low"` | 2048 |
| `"medium"` | 8192 |
| `"high"` | 24576 |
| `"xhigh"` | 32768 |
| `"max"` | **未定义**（fallback 到 8192）⚠️ |

不传 reasoning 时设 `thinking_budget=0`（显式关闭）。

Gemini 还有一个框架里独有的 `"auto"` 级别（`THINKING_CONFIGS` 里定义，`_thinking.py:86`），让模型自行决定。但 `"auto"` 不在 `ThinkingLevel` 类型里，仅 Gemini picker 显示。

### 6.6 Amazon Bedrock（`providers/amazon_bedrock/amazon_bedrock.py`）

走 Anthropic Messages API 的 Bedrock 变体。分两条路径，逻辑和 6.1 类似。

预处理（第 242-264 行）：

```python
if _supports_adaptive_thinking(model_id):
    # 直传 reasoning 字符串
else:
    # adjust_max_tokens_for_thinking + budget
```

Adaptive 路径的 effort 映射（`_map_thinking_level_to_effort`，第 81 行）：

| 框架 ThinkingLevel | Bedrock effort |
|---|---|
| `"minimal"` / `"low"` | `"low"` |
| `"medium"` | `"medium"` |
| `"high"` | `"high"` |
| `"xhigh"` | `"max"` |
| `"max"` | **未定义**（fallback 到 `"high"`）⚠️ |

Budget 路径用 `adjust_max_tokens_for_thinking()`（`_shared/simple_options.py`），budget 表和 6.1 的 `_THINKING_BUDGETS` 一致。

### 6.7 claude-code（`providers/anthropic/_claude_code_direct_runtime.py`）

**不是独立的 wire 格式**。claude-code Runtime 在构造时把 model 改写为 `anthropic:<model_id>`（第 158 行），之后走标准 Anthropic provider（6.1）。认证用 subscription OAuth token，所以一定走 adaptive 路径。映射表同 6.1。

### 6.8 Azure OpenAI Responses

走 OpenAI Responses API 格式（同 6.4），但端点是 Azure 的。映射逻辑完全复用 6.4。

---

## 6.9 映射缺口汇总

以下是 `"max"` 级别在各 provider 的映射缺口（新增 `"max"` 后尚未全部补到位）：

| Provider | `"max"` 的映射 | 状态 |
|---|---|---|
| Anthropic adaptive | fallback 到 `"high"` | ⚠️ 应映射到 `"max"` |
| Anthropic budget | fallback 到 8192 | ⚠️ 应有独立 budget |
| OpenAI Completions | fallback 到 `"medium"` | ⚠️ 应映射到 `"high"` 或直传 |
| Gemini | fallback 到 8192 | ⚠️ 应有独立 budget（如 65536） |
| Bedrock | fallback 到 `"high"` | ⚠️ 应映射到 `"max"` |
| OpenAI Codex/Responses | 直传 `"max"` | ✅ |
| claude-code | 走 Anthropic，同上 | ⚠️ 同 Anthropic |

## 7. Session 持久化

`SessionRunConfig.thinking_effort` 存在 `SessionDB`(per-session)。规范化由 `_normalize_thinking()` 处理("none"→"off","max" 不再映射成 xhigh)。

## 8. THINKING_OVERRIDES

`thinking_catalog.py` 中的静态字典,用于覆盖特定模型的默认 thinking 配置。

当前覆盖:
- `anthropic/claude-opus-4-7`: `thinking_variant="opus47"`,levels 受限(历史原因)

覆盖条目的字段:
- `thinking_levels`: 完整的级别列表
- `default_thinking_level`: 默认值
- `thinking_variant`: wire 格式标记

## 9. 已知问题与 follow-up

| 问题 | 状态 | 说明 |
|---|---|---|
| Opus 4.7 override 没有 xhigh/max | 待修 | API 支持但 override 限制了 |
| claude-code 模型的 thinking_levels 为空 | 已修(动态生成) | `_thinking.py` 对 `reasoning=True` 但 levels 空的模型动态调 `derive_thinking_fields` |
| 前端 clamp 依赖 500ms 轮询 | 设计局限 | 长期应改成事件驱动(WS broadcast) |
| `thinking_variant` 仅有一个值 | 正常 | 目前只有 Opus 4.7 需要特殊 wire 格式;新模型如需特殊处理加新 variant |

## 10. 文件清单

| 文件 | 职责 |
|---|---|
| `providers/types.py:52` | `ThinkingLevel` 类型定义 |
| `providers/types.py:310-320` | `Model` 的 thinking 字段 |
| `providers/types.py:160` | `SimpleStreamOptions.reasoning` |
| `providers/thinking_catalog.py` | `THINKING_OVERRIDES` + `derive_thinking_fields()` + `apply_thinking_catalog()` |
| `webui/_thinking.py` | `THINKING_CONFIGS` + `get_thinking_config_for_model()` + `apply_thinking_effort()` |
| `agent/session_config.py` | `VALID_THINKING` + `reasoning_from_config()` + `_normalize_thinking()` |
| `agent/dispatcher/__init__.py:839` | 读 thinking_effort 传给 agent_loop |
| `agent/agent_loop.py:412` | 构造 `SimpleStreamOptions(reasoning=...)` |
| `providers/anthropic/anthropic.py:663` | Anthropic wire 翻译 |
| `providers/openai_codex/openai_codex.py:392` | Codex wire 翻译 |
| `providers/anthropic/_claude_code_direct_runtime.py` | claude-code 模型注册 |
| `web/components/chat/composer/controls/thinking-effort-pill.tsx` | 前端 UI 组件 |
| `web/components/chat/composer/controls/use-thinking-effort.ts` | 前端 hook |
| `web/lib/runtime-bridge/providers.ts:127` | `window._thinkingConfig` 注入 |
| `webui/routes/runtime.py:215` | `/api/agent_settings` 返回 thinking 配置 |
