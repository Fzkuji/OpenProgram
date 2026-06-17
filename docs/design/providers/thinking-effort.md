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

## 6. 各 Provider 的 wire 格式

不同 provider 用不同的 API 参数,`SimpleStreamOptions.reasoning` 是统一入口,各 provider 在 `stream_simple()` 里翻译。

### 6.1 Anthropic(Messages API)

| 模型 | 参数格式 |
|---|---|
| Opus 4.7+(`thinking_variant="opus47"`) | `thinking: {type: "adaptive"}` + `output_config: {effort: "<level>"}` |
| Opus 4.6 / Sonnet 4.6(adaptive) | `thinking: {type: "adaptive"}` + `output_config: {effort: "<level>"}` |
| 旧模型(deprecated budget 模式) | `thinking: {type: "enabled", budget_tokens: N}` |
| Fable 5 | thinking 始终开启,不能 disabled;`output_config: {effort: "<level>"}` |

level 映射:`minimal/low/medium/high` 直传;`xhigh/max` 也直传(Opus 4.7+ 和 Sonnet 4.6 支持)。

### 6.2 OpenAI Codex(Responses API)

```json
{
  "reasoning": {
    "effort": "<level>",
    "summary": "auto"
  }
}
```

`reasoning_effort` 接受完整的 level 集合。Codex 返回加密的 reasoning 内容,需要 `include: ["reasoning.encrypted_content"]`。

### 6.3 claude-code(直连 api.anthropic.com)

内部改写为 `anthropic:<model_id>`,走 Anthropic Messages API 的 wire 格式。和 anthropic provider 完全一样,只是认证用 subscription OAuth token。

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
