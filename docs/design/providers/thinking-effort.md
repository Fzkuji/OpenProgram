# Thinking / Effort 子系统设计

## 1. 问题

不同 LLM provider 用不同的方式控制推理深度，参数名、值类型、支持的级别都不同。框架需要给用户统一的体验——一个滑块，选一个深度，不用关心底层 API 叫什么。

## 2. 各家 API 的 thinking 参数

| Provider | API 参数名 | 值类型 | 已知级别 | 检测方式 |
|---|---|---|---|---|
| Anthropic | `output_config.effort` | 字符串 | low/medium/high/xhigh/max | `/v1/models/{id}` 返回 `capabilities.effort.{level}.supported` |
| OpenAI (Responses) | `reasoning.effort` | 字符串 | minimal/low/medium/high/xhigh | 无 capabilities API |
| OpenAI (Chat) | `reasoning_effort` | 字符串 | low/medium/high | 无 capabilities API |
| Google Gemini | `thinkingConfig.thinkingBudget` | 数字(token) | 无固定级别，用 token 数 | 无 capabilities API |
| DeepSeek V4 | `reasoning_effort`(OpenAI 兼容) | 字符串 | minimal/low/medium/high/max | models.dev / Fetch 数据 |
| DeepSeek R1 | 无 | 无 | 无 effort 控制，只有开/关 | model id 推断 |
| OpenRouter | 透传底层 provider 的参数 | 同底层 | `supported_parameters` 列表含 `"reasoning"` 则支持 | 有/无二值，不知级别 |
| Anthropic (budget, 旧) | `thinking.budget_tokens` | 数字(token) | 无固定级别 | 同 Anthropic |

## 3. 三层探测策略

框架对每个模型按以下顺序探测 thinking 能力，**越早命中越精确**：

### 第 1 层：从 API 精确获取支持的级别

Fetch Models 时，对支持 capabilities API 的 provider，调用模型详情端点，**精确获取每个级别的 supported 状态**。

当前支持的 provider：
- **Anthropic**：`GET /v1/models/{id}` → `capabilities.effort.{low,medium,high,xhigh,max}.supported`
- **Anthropic**：同时获取 `capabilities.thinking.types.adaptive.supported`

结果写入 thinking.json 的 `model_overrides`（通过 `probe_thinking.py --update`），并在 Fetch 数据里存 `thinking_levels`。

### 第 2 层：判断"有没有 thinking 能力"

对没有精确 capabilities API 的 provider，退而求其次——**至少判断这个模型支不支持 thinking**。

数据来源：
- **models.dev**：`reasoning: true/false`（Fetch 时从 models.dev 补充）
- **probe_thinking.py**：从 model id 推断（Fetch 时自动调用）
- **OpenRouter**：`supported_parameters` 列表里有 `"reasoning"` → 支持

知道支持 thinking 后，用 `thinking.json` 的 provider 级别配置给出可用级别列表。

### 第 3 层：调用时试探 + 降级（兜底）

完全没有任何信息时（新 provider、未知模型），**用统一级别名尝试调用，失败就降级重试**。

```
用户选了 max
  → 发 max → API 400? → 发 xhigh → API 400? → 发 high → ... → 发 low → 全 400? → 标记此模型不支持 thinking
```

试探结果缓存——同一个模型只试一次，后续直接用缓存的结果。

降级顺序：`max → xhigh → high → medium → low → minimal → 不发 thinking 参数`

### 策略选择

```
Fetch 时，对每个模型:

  ① 拿到了精确级别列表?（API capabilities）
     → 是: 写入 thinking.json model_overrides，结束
     → 否: 继续

  ② 知道"有没有 thinking 能力"?（models.dev / probe_thinking / supported_parameters）
     → 知道没有: 标记 reasoning=false，结束
     → 知道有: 继续

  ③ 有 thinking.json 的 provider 配置?
     → 有: 用 provider 级别的映射表给档位，结束
     → 没有: 用 OpenAI 兼容 fallback（low/medium/high），结束
```

## 4. 级别定义

```
ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
```

加 `"off"` 表示关闭。这是框架的统一名字，和各家 API 的参数名无关。

| 级别 | 含义 |
|---|---|
| off | 不推理 |
| minimal | 极简推理 |
| low | 轻度推理 |
| medium | 平衡 |
| high | 深度推理 |
| xhigh | 扩展推理 |
| max | 最大推理 |

## 5. 架构分层

```
┌─────────────────────────────────────────────────┐
│ 前端 UI                                          │
│ thinking picker (off + N 档)                     │
│ 几档由 /api/agent_settings 返回的 options 决定     │
└──────────────┬──────────────────────────────────┘
               │ thinking_effort="high"
┌──────────────▼──────────────────────────────────┐
│ _thinking.py                                     │
│ get_thinking_config_for_model()                  │
│ 单一数据源: 委托给 listing.list_models_for_provider │
│ 保证前端 picker 和模型列表完全一致                    │
└──────────────┬──────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ listing.py                                       │
│ list_models_for_provider / list_enabled_models   │
│ 合并 combined_models + thinking_spec.derive →    │
│ 统一的 thinking_levels                            │
└──────────────┬──────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ thinking_spec.py                                 │
│ 加载 thinking.json + _THINKING_ALIASES           │
│ translate_reasoning() / derive_thinking_levels() │
│ 无 thinking.json → OpenAI 兼容 fallback           │
└──────────────┬──────────────────────────────────┘
               │ SimpleStreamOptions(reasoning="high")
┌──────────────▼──────────────────────────────────┐
│ Provider 翻译层                                   │
│ 各 provider 的 stream_simple() 调                 │
│ thinking_spec.translate_reasoning()              │
│ 映射 → API 值                                     │
└──────────────┬──────────────────────────────────┘
               │ {"output_config":{"effort":"high"}}
┌──────────────▼──────────────────────────────────┐
│ API                                              │
└─────────────────────────────────────────────────┘
```

**关键设计：单一数据源。** `_thinking.py` 不自己推导 thinking_levels，而是从 `listing.list_models_for_provider` 拿已经合并好的结果。`list_enabled_models` 也委托给 `list_models_for_provider`。这消除了之前多个推导路径不一致的问题。

## 6. 数据来源与优先级

thinking_levels 的推导优先级（从高到低）：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | thinking.json 的 model_overrides | 从 API capabilities 自动写入（Anthropic），或手动配 |
| 2 | thinking.json 的 provider 级别映射 | provider 通用档位 |
| 3 | Fetch 数据 / models.dev 的 thinking_levels | 当 thinking.json 没给出档位时的 fallback |
| 4 | OpenAI 兼容 fallback | 无 thinking.json 的 provider 自动用 low/medium/high |

`listing.py` 的实际逻辑：先调 `derive_thinking_fields`（走优先级 1-2），如果为空再看 Fetch 数据（优先级 3），再不行走 fallback（优先级 4）。

## 7. 两个配置文件

### thinking.json（进 git，很少变）

每个 provider 文件夹里一份。声明：
- `wire_format`：API 用字符串还是数字
- `effort_map` / `budget_map`：框架级别 → API 值的映射
- `default_effort`：provider 默认级别
- `model_overrides`：特殊模型的覆盖（空 `effort_map: {}` = 该模型无 effort 控制）

**没有 thinking.json 的 provider 自动用 OpenAI 兼容 fallback**：`effort_string` + `low/medium/high` 三档。新加社区 provider 不需要手动创建 thinking.json。

**Provider alias**：`claude-code` 共用 `anthropic` 的 thinking.json（同 API，同模型）。由 `thinking_spec._THINKING_ALIASES` 映射。

详见 [models.md](models.md) §3。

### Fetch 数据（`_catalog/fetched/<provider>.json`，.gitignore）

Fetch 时写入，包含 API 返回的精确 thinking_levels。按 provider id 命名。详见 [models.md](models.md) §4。

## 8. 调用时试探（第 3 层）的实现

```python
_probed_levels: dict[tuple[str, str], list[str]] = {}

async def probe_thinking_levels(provider_id, model_id):
    """对未知模型试探支持的 effort 级别。"""
    key = (provider_id, model_id)
    if key in _probed_levels:
        return _probed_levels[key]

    supported = []
    for level in ["max", "xhigh", "high", "medium", "low", "minimal"]:
        try:
            api_value = translate_reasoning(provider_id, model_id, level)
            await _test_call(provider_id, model_id, api_value)
            supported.append(level)
        except BadRequestError:
            continue
        except Exception:
            break

    _probed_levels[key] = supported
    return supported
```

**状态：待实现。** 当前靠 thinking.json + Fetch 数据覆盖绝大多数场景。

## 9. 长度控制

**框架不控制推理长度。** 传深度级别（effort），API 自适应决定想多少 token。

少数 API 需要具体 token 数（Gemini、Anthropic 旧模型）：thinking.json 的 `budget_map` 做映射。

## 10. 外部框架对比

| | Claude Code | OpenCode | OpenClaw |
|---|---|---|---|
| 探测策略 | 不探测（只连 Anthropic） | 不探测（统一 7 级，API 报错就报错） | 不探测（固定 5 级） |
| 我们 | 三层探测（API caps → probe 推断 → 试探降级） | | |

## 11. Session 持久化

`SessionRunConfig.thinking_effort` 存 SessionDB（per-session）。
`_normalize_thinking()`：`"none"` → `"off"`，无效值 → `None`。
`reasoning_from_config()`：`"off"` → `None`，其他原样返回。

## 12. 文件清单

| 文件 | 职责 |
|---|---|
| `providers/<provider>/thinking.json` | provider 级别映射（进 git） |
| `providers/<provider>/probe_thinking.py` | Fetch 时自动探测 reasoning 能力 |
| `providers/thinking_spec.py` | 加载 thinking.json + translate + derive + alias + fallback |
| `providers/thinking_catalog.py` | apply_thinking_catalog（启动时填 Model 对象）+ derive_thinking_fields |
| `providers/types.py` | ThinkingLevel 类型 + SimpleStreamOptions.reasoning |
| `webui/_thinking.py` | UI picker 配置（单一数据源: listing）+ apply_thinking_effort |
| `webui/_model_catalog/listing.py` | list_models_for_provider / list_enabled_models（统一推导 thinking_levels） |
| `webui/_model_catalog/fetchers/__init__.py` | Fetch 时自动调 probe_thinking + enrichment |
| `webui/_model_catalog/fetchers/anthropic.py` | Fetch 时从 capabilities 提取 thinking_levels |
| `agent/session_config.py` | VALID_THINKING + reasoning_from_config |

## 13. 实现状态

| 项目 | 状态 | 说明 |
|---|---|---|
| Anthropic capabilities 精确获取 | ✅ 已落地 | Fetch 时自动从 `/v1/models/{id}` 拉 effort/adaptive |
| 各 provider probe_thinking.py | ✅ 已落地 | Fetch 时自动调用，不需要用户手动运行 |
| thinking.json 自动更新 | ✅ 已落地 | `probe_thinking.py --update` 从 API 写入 model_overrides |
| 无 thinking.json 的 provider fallback | ✅ 已落地 | 自动用 OpenAI 兼容 low/medium/high |
| claude-code alias | ✅ 已落地 | 共用 anthropic 的 thinking.json |
| 前后端单一数据源 | ✅ 已落地 | `_thinking.py` 委托给 `listing.list_models_for_provider` |
| 空 effort_map override | ✅ 已落地 | 空 `{}` = 该模型无 effort 控制（如 DeepSeek R1） |
| 第 3 层试探降级 | 待实现 | probe_thinking_levels 函数 + 缓存 |

## 14. 各 provider 的探测文件

每个 provider 文件夹里有 `probe_thinking.py`，Fetch 时自动被 `fetchers/__init__.py` 调用：

| Provider | 文件 | 探测方式 |
|---|---|---|
| anthropic | `anthropic/probe_thinking.py` | `/v1/models/{id}` capabilities（精确到每个级别） |
| deepseek | `deepseek/probe_thinking.py` | model id 推断（v4 = reasoning，reasoner = reasoning 无 effort） |
| openai_codex | `openai_codex/probe_thinking.py` | OpenAI models API + model id 推断 |
| openai_responses | `openai_responses/probe_thinking.py` | OpenRouter `supported_parameters`（有/无） |
| openai_completions | `openai_completions/probe_thinking.py` | model id 推断（o1/o3/gpt-5） |
| google | `google/probe_thinking.py` | model name 推断 |

## 15. 当前各模型实际档位（浏览器验证）

| Provider | 模型 | 档位 | 来源 |
|---|---|---|---|
| claude-code | opus-4-8 | low/medium/high/xhigh/max (5档) | API capabilities |
| claude-code | opus-4-5 | low/medium/high (3档) | API capabilities |
| claude-code | fable-5 | low/medium/high/xhigh/max (5档) | API capabilities |
| claude-code | sonnet-4-6 | low/medium/high/max (4档) | API capabilities |
| deepseek | v4-flash | minimal/low/medium/high/max (5档) | Fetch 数据 + thinking.json |
| deepseek | v4-pro | minimal/low/medium/high/max (5档) | Fetch 数据 + thinking.json |
| openai-codex | gpt-5.5 | low/medium/high/xhigh/max (5档) | thinking.json override |
| minimax-cn | MiniMax-M3 | low/medium/high (3档) | OpenAI 兼容 fallback |
| openrouter | gemma-4 / qwen3.7 等 | low/medium/high (3档) | OpenAI 兼容 fallback |
| openrouter | llama-3.3 | 无（不支持 reasoning） | reasoning=false |
