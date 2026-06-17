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
| DeepSeek | `reasoning_effort`(OpenAI 兼容) | 字符串 | 未知 | 无 capabilities API |
| OpenRouter | 透传底层 provider 的参数 | 同底层 | `supported_parameters` 列表含 `"reasoning"` 则支持 | 有/无二值，不知级别 |
| Anthropic (budget, 旧) | `thinking.budget_tokens` | 数字(token) | 无固定级别 | 同 Anthropic |

## 3. 三层探测策略

框架对每个模型按以下顺序探测 thinking 能力，**越早命中越精确**：

### 第 1 层：从 API 精确获取支持的级别

Fetch Models 时，对支持 capabilities API 的 provider，调用模型详情端点，**精确获取每个级别的 supported 状态**。

当前支持的 provider：
- **Anthropic**：`GET /v1/models/{id}` → `capabilities.effort.{low,medium,high,xhigh,max}.supported`
- **Anthropic**：同时获取 `capabilities.thinking.types.adaptive.supported`

结果直接存入 `models.json` 的 `thinking_levels` 字段。这是最权威的数据——API 说支持什么就是什么。

### 第 2 层：判断"有没有 thinking 能力"

对没有精确 capabilities API 的 provider，退而求其次——**至少判断这个模型支不支持 thinking**。

数据来源：
- **OpenRouter**：`supported_parameters` 列表里有 `"reasoning"` → 支持
- **models.dev**：`reasoning: true/false`
- **catalog JSON**：`reasoning: true/false`

知道支持 thinking 后，用 `thinking.json` 的 provider 级别配置给出可用级别列表。不够精确（可能某个模型只支持 3 档但 provider 配了 6 档），但比瞎猜好。

### 第 3 层：调用时试探 + 降级（兜底）

完全没有任何信息时（新 provider、未知模型），**用统一级别名尝试调用，失败就降级重试**。

```
用户选了 max
  → 发 max → API 400? → 发 xhigh → API 400? → 发 high → ... → 发 low → 全 400? → 标记此模型不支持 thinking
```

试探结果缓存——同一个模型只试一次，后续直接用缓存的结果。

降级顺序：`max → xhigh → high → medium → low → minimal → 不发 thinking 参数`

### 策略选择（按数据可用性灵活组合，不是固定 1→2→3）

```
Fetch 时，对每个模型:

  ① 拿到了精确级别列表?（API capabilities）
     → 是: 直接用，结束
     → 否: 继续

  ② 知道"有没有 thinking 能力"?（supported_parameters / catalog / models.dev）
     → 知道没有: 标记 reasoning=false，结束
     → 知道有: 继续
     → 不知道: 标记 reasoning=unknown，继续

  ③ 有 thinking.json 的 provider 配置?
     → 有: 用 provider 级别的映射表给档位，结束
     → 没有: 继续

  ④ 到这里 = 知道有 thinking 但不知道支持哪些级别
     → 标记 reasoning=true, thinking_levels=[]
     → 调用时走第 3 层(试探降级)

调用时:
  thinking_levels 非空? → 用它
  thinking_levels 空但 reasoning=true? → 第 3 层(试探降级，缓存结果)
  reasoning=false? → 不发 thinking 参数
```

三层不是串行走的。比如：
- Anthropic：① 直接搞定（精确到每个级别）
- OpenRouter + thinking.json 有配置：② 知道有 thinking → ③ 用 provider 配置给档位
- 全新未知 provider：② 不知道 → ④ 调用时试探

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
│ UI 层                                            │
│ thinking-effort-pill.tsx                         │
│ 显示 thinking_levels 里有几档就显示几档           │
└──────────────┬──────────────────────────────────┘
               │ thinking_effort="high"
┌──────────────▼──────────────────────────────────┐
│ Session 层                                       │
│ dispatcher → reasoning_from_config() → "high"    │
│ per-session 持久化                                │
└──────────────┬──────────────────────────────────┘
               │ SimpleStreamOptions(reasoning="high")
┌──────────────▼──────────────────────────────────┐
│ Provider 翻译层                                   │
│ thinking_spec.translate_reasoning()              │
│ 读 thinking.json 映射 → API 值                    │
└──────────────┬──────────────────────────────────┘
               │ {"output_config":{"effort":"high"}}
┌──────────────▼──────────────────────────────────┐
│ 降级层（第 3 层兜底）                              │
│ 400? → 降一档重试 → 缓存结果                      │
└──────────────┬──────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ API — 长度由 API 自适应，框架不控制                 │
└─────────────────────────────────────────────────┘
```

## 6. 数据来源与优先级

thinking_levels 的来源（从高到低）：

| 优先级 | 来源 | 什么时候有 | 精度 |
|---|---|---|---|
| 1 | API capabilities（Fetch 时拉） | Anthropic 系 | 精确到每个级别 |
| 2 | thinking.json 的 model_overrides | 手动配的特殊模型 | 精确 |
| 3 | thinking.json 的 provider 级别 | 所有有 thinking.json 的 provider | provider 级别 |
| 4 | catalog JSON 里的 thinking_levels | 静态数据 | 手动维护 |
| 5 | derive_thinking_fields 自动生成 | 最终 fallback | 可能不准 |
| 6 | 调用时试探（第 3 层） | 完全未知的模型 | 试过才知道 |

## 7. 两个配置文件

### thinking.json（进 git，很少变）

每个 provider 文件夹里一份。声明：
- `wire_format`：API 用字符串还是数字
- `effort_map` / `budget_map`：框架级别 → API 值的映射
- `default_effort`：provider 默认级别
- `model_overrides`：特殊模型的覆盖

详见 [models.md](models.md) §3。

### models.json（.gitignore，Fetch 生成）

Fetch 时写入，包含 API 返回的精确 thinking_levels。详见 [models.md](models.md) §4。

## 8. 调用时试探（第 3 层）的实现

```python
# 缓存: {(provider, model_id): list[str]}  — 试探过的结果
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
            # 发一个最小的测试请求
            await _test_call(provider_id, model_id, api_value)
            supported.append(level)
        except BadRequestError:
            continue  # 这个级别不支持
        except Exception:
            break  # 网络错误等，停止试探

    _probed_levels[key] = supported
    return supported
```

**优化**：不用每个级别都试。先试 `high`（最常见），成功了再试 `max` 和 `low` 确定上下限。中间的级别通常都支持。

## 9. 长度控制

**框架不控制推理长度。** 传深度级别（effort），API 自适应决定想多少 token。这是 Anthropic（adaptive thinking）和 OpenAI 的主流做法。

少数 API 需要具体 token 数（Gemini、Anthropic 旧模型）：thinking.json 的 `budget_map` 做映射。但这是过渡方案——Anthropic 已废弃 budget_tokens。

## 10. 外部框架对比

| | Claude Code | OpenCode | OpenClaw |
|---|---|---|---|
| 探测策略 | 不探测（只连 Anthropic） | 不探测（统一 7 级，API 报错就报错） | 不探测（固定 5 级） |
| 我们 | 三层探测（API caps → 参数列表 → 试探降级） | | |

OpenCode 的方式最简单但会报错。我们的三层策略在不增加用户负担的前提下尽量避免报错。

## 11. Session 持久化

`SessionRunConfig.thinking_effort` 存 SessionDB（per-session）。
`_normalize_thinking()`：`"none"` → `"off"`，无效值 → `None`。
`reasoning_from_config()`：`"off"` → `None`，其他原样返回。

## 12. 文件清单

| 文件 | 职责 |
|---|---|
| `providers/<provider>/thinking.json` | provider 级别映射（进 git） |
| `providers/<provider>/models.json` | Fetch 的模型数据含 thinking_levels（gitignore） |
| `providers/thinking_spec.py` | 加载 thinking.json + translate_reasoning + derive_thinking_levels |
| `providers/thinking_catalog.py` | apply_thinking_catalog（启动时填充 Model 对象） |
| `providers/types.py:52` | ThinkingLevel 类型 |
| `providers/types.py:160` | SimpleStreamOptions.reasoning |
| `webui/_thinking.py` | UI picker 配置 + apply_thinking_effort |
| `agent/session_config.py` | VALID_THINKING + reasoning_from_config |
| `agent/dispatcher/__init__.py:839` | 读 thinking_effort 传给 agent_loop |
| `agent/agent_loop.py:412` | 构造 SimpleStreamOptions(reasoning=...) |
| `webui/_model_catalog/fetchers/anthropic.py` | Fetch 时从 capabilities 提取 thinking_levels |

## 13. 待实现

| 项目 | 说明 |
|---|---|
| 第 3 层试探降级 | probe_thinking_levels 函数 + 缓存 |
| OpenRouter fetcher 加 `supported_parameters` 检测 | 从 `"reasoning" in supported_parameters` 设 `reasoning: true` |
| 其他 provider 的 capabilities 检测 | 等各家 API 加上 capabilities 端点 |
