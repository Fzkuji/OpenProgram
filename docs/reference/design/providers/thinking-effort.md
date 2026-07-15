# Thinking / Effort 子系统设计

> 模型目录和 provider 配置的整体设计见 [models.md](models.md)。本文只讲 thinking effort 的控制逻辑。

## 1. 问题

不同 LLM provider 用不同方式控制推理深度——参数名不同（`effort` / `reasoning_effort` / `thinkingBudget`）、值类型不同（字符串 / token 数）、支持的级别数不同（3 档到 6 档）。框架需要把这些差异藏起来，给用户一个统一的滑块。

## 2. 各家 API 参数

| Provider | API 参数 | 值类型 | 级别 |
|---|---|---|---|
| Anthropic | `output_config.effort` | 字符串 | low/medium/high/xhigh/max（按模型不同，3-5 档） |
| Anthropic (旧) | `thinking.budget_tokens` | token 数 | 连续值 |
| OpenAI Responses | `reasoning.effort` | 字符串 | minimal/low/medium/high/xhigh |
| OpenAI Chat | `reasoning_effort` | 字符串 | low/medium/high |
| Google Gemini | `thinkingConfig.thinkingBudget` | token 数 | 连续值 |
| DeepSeek V4 | `reasoning_effort` | 字符串 | minimal/low/medium/high/max |
| DeepSeek R1 | 无 | 无 | 只有开/关，不可调 |
| OpenRouter | 透传底层参数 | 同底层 | 从 `supported_parameters` 判断有无 |

## 3. 统一级别

框架定义 6 个级别 + off：

```
ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
```

这是框架的抽象名字，和任何 API 的参数名无关。每个模型可以只支持其中的一个子集（比如 Opus 4.5 只支持 low/medium/high）。UI 按模型实际支持的级别显示滑块。

## 4. 数据流：从用户选择到 API 请求

完整的调用链路：

```
用户在 UI 选 "high"
        │
        ▼
┌─ _thinking.py ──────────────────────────────────┐
│ get_thinking_config_for_model(provider, model)   │
│ → 从 listing.list_models_for_provider 取该模型的  │
│   thinking_levels，构建 UI picker options          │
│ → 返回 {options: [off,low,medium,high,...]}       │
└──────────────────────────────┬───────────────────┘
                               │ 用户选了 "high"
                               ▼
┌─ session_config.py ─────────────────────────────┐
│ _normalize_thinking("high") → "high"             │
│ 存入 SessionDB (per-session 持久化)               │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
┌─ dispatcher → agent_loop ───────────────────────┐
│ SimpleStreamOptions(reasoning="high")            │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
┌─ provider 的 stream_simple() ───────────────────┐
│ thinking_spec.translate_reasoning(               │
│     "anthropic", "claude-opus-4-8", "high"       │
│ )                                                │
│ → 读 thinking.json → effort_map → "high"         │
│ → 塞进请求体:                                     │
│   {"output_config": {"effort": "high"}}          │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
                          Anthropic API
```

## 5. thinking_levels 的推导

每个模型在 UI 里显示几档，由 `listing.py` 在构建模型列表时统一推导。推导优先级（从高到低）：

| 优先级 | 数据来源 | 说明 | 示例 |
|---|---|---|---|
| 1 | thinking.json 的 `model_overrides` | 从 API capabilities 自动写入或手动配 | Opus 4.8: 5 档，Opus 4.5: 3 档 |
| 2 | thinking.json 的 provider 级别 `effort_map`/`budget_map` | 该 provider 的通用映射 | Anthropic 默认 6 档 |
| 3 | Fetch 数据（models.json）里的 `thinking_levels` | Fetch 时从 models.dev 或 API 获取 | DeepSeek V4: 5 档 |
| 4 | OpenAI 兼容 fallback | 无 thinking.json 的 provider 自动用 | groq/mistral: 3 档 |

推导逻辑在 `listing.py` 的 `list_models_for_provider()` 里：

```python
# 先尝试 thinking.json（优先级 1-2）
levels, default, variant = derive_thinking_fields(provider_id, model_id, reasoning)
# 如果 thinking.json 没给出结果，看 Fetch 数据（优先级 3）
if not levels and raw.get("thinking_levels"):
    levels = list(raw["thinking_levels"])
# 优先级 4 的 fallback 已经包含在 derive_thinking_fields 内部
```

**单一数据源原则：** `_thinking.py`（UI picker）、`list_enabled_models`（模型列表）、`list_models_for_provider`（provider 详情）三个消费方全部走同一条推导路径。`_thinking.py` 委托给 `list_models_for_provider`，`list_enabled_models` 也委托给它。不存在分叉。

## 6. translate_reasoning：框架级别 → API 值

用户选了一个框架级别（如 `"high"`），provider 发请求前需要翻译成 API 能理解的值。翻译逻辑在 `thinking_spec.translate_reasoning()`：

```python
def translate_reasoning(provider_id, model_id, level):
    spec = get_thinking_spec(provider_id)

    # 1. 看 model_overrides（精确到模型）
    override = spec.get("model_overrides", {}).get(model_id)
    if override:
        emap = override.get("effort_map")
        if emap is not None:
            return emap.get(level) if emap else None  # 空 dict = 不支持

    # 2. provider 级别翻译
    if spec["wire_format"] == "effort_string":
        return spec["effort_map"].get(level)
    if spec["wire_format"] == "budget_tokens":
        return spec["budget_map"].get(level)

    return None  # wire_format == "none"
```

返回值直接塞进各 provider 的 API 请求体。每个 provider 的 `stream_simple()` 只需要关心"拿到一个值，放到请求的哪个字段"，不需要关心翻译逻辑。

## 7. 探测策略

不同 provider 对 thinking 能力的暴露程度不同。框架用三层策略，尽量自动获取信息：

### 第 1 层：API capabilities（精确）

Fetch 时调用 API 获取每个级别的 supported 状态。

当前只有 Anthropic 支持：`GET /v1/models/{id}` → `capabilities.effort.{level}.supported`。结果写入 `thinking.json` 的 `model_overrides`（通过 `probe_thinking.py --update`）。

### 第 2 层：reasoning 有/无推断

对没有 capabilities API 的 provider，至少判断模型支不支持 reasoning：

- **models.dev**：`reasoning: true/false`
- **probe_thinking.py**：从 model id 推断（如 `v4` / `reasoner` / `o3`）
- **OpenRouter**：`supported_parameters` 含 `"reasoning"` → 支持

知道 `reasoning=true` 后，用 thinking.json 的 provider 级别映射给档位。

### 第 3 层：调用时试探降级（待实现）

完全没有任何信息的模型，发请求时从 max 开始逐级降到 minimal，400 就跳过，缓存结果。

## 8. 自动化

probe_thinking.py 和 Fetch 的集成：

1. 每个 provider 文件夹里有 `probe_thinking.py`，暴露 `probe()` 函数
2. `fetchers/__init__.py` 在 enrichment 步骤自动调 `_load_probe(provider_id)` → `probe()`
3. 结果用来补充 Fetch 数据里缺失的 `reasoning` 字段
4. Anthropic 的 probe 还可以用 `--update` 参数直接更新 thinking.json

| Provider | 探测方式 |
|---|---|
| anthropic | `/v1/models/{id}` capabilities（精确到每个级别） |
| deepseek | model id 推断（v4→reasoning+effort，reasoner→reasoning 无 effort） |
| openai_codex | OpenAI models API + model id 推断（o1/o3/gpt-5） |
| openai_responses | OpenRouter `supported_parameters` |
| openai_completions | model id 推断（o1/o3/gpt-5） |
| google | model name 推断 |

**没有 probe_thinking.py 的 provider 不影响 Fetch**——enrichment 步骤 catch 了 ImportError，静默跳过。

## 9. 关键设计决策

### 9.1 框架不控制推理长度

只传深度级别（effort），让 API 自适应决定用多少 token。Gemini 和 Anthropic 旧模型需要具体 token 数，用 `budget_map` 做映射。

### 9.2 空 effort_map = 无 effort 控制

`model_overrides` 里 `"effort_map": {}` 表示该模型虽然有 reasoning 能力但不支持 effort 调节（如 DeepSeek R1——永远全力推理）。`translate_reasoning` 对空 map 返回 `None`，provider 不发 effort 参数。

### 9.3 无 thinking.json 的 provider 自动兜底

`get_thinking_spec()` 找不到 thinking.json 时返回 OpenAI 兼容 fallback（`effort_string` + `low/medium/high`）。社区 provider 加进来不需要任何配置就能用。

### 9.4 Provider alias

`claude-code` 和 `anthropic` 共用同一份 thinking.json（同 API、同模型）。`_THINKING_ALIASES = {"claude-code": "anthropic"}` 做映射，不复制文件。

## 10. 文件清单

| 文件 | 职责 |
|---|---|
| `providers/<provider>/thinking.json` | 声明该 provider 的 wire_format、effort_map、model_overrides |
| `providers/<provider>/probe_thinking.py` | Fetch 时自动探测 reasoning 能力 |
| `providers/<provider>/models.json` | Fetch 生成的模型列表（含 thinking_levels，gitignore） |
| `providers/thinking_spec.py` | 加载 thinking.json、translate_reasoning、derive_thinking_levels、alias、fallback |
| `providers/thinking_catalog.py` | 启动时用 derive_thinking_fields 填充 Model 对象的 thinking 字段 |
| `providers/types.py` | `ThinkingLevel` 类型定义、`SimpleStreamOptions.reasoning` 字段 |
| `webui/_thinking.py` | UI picker 构建（从 listing 取数据）、apply_thinking_effort（运行时设值） |
| `webui/_model_catalog/listing.py` | list_models_for_provider（统一推导 thinking_levels 的唯一入口） |
| `webui/_model_catalog/fetchers/__init__.py` | Fetch enrichment：自动调 probe_thinking |
| `webui/_model_catalog/fetchers/anthropic.py` | Anthropic Fetch：从 capabilities 提取 thinking_levels |
| `agent/session_config.py` | `VALID_THINKING` 校验、`reasoning_from_config` 转换 |

## 11. 当前各模型实际档位

浏览器验证结果（2026-06-18）：

| Provider | 模型 | 档位 | 来源 |
|---|---|---|---|
| claude-code | opus-4-8 | low/medium/high/xhigh/max (5) | API capabilities |
| claude-code | fable-5 | low/medium/high/xhigh/max (5) | API capabilities |
| claude-code | sonnet-4-6 | low/medium/high/max (4) | API capabilities |
| claude-code | opus-4-5 | low/medium/high (3) | API capabilities |
| deepseek | v4-flash | minimal/low/medium/high/max (5) | Fetch + thinking.json |
| deepseek | v4-pro | minimal/low/medium/high/max (5) | Fetch + thinking.json |
| openai-codex | gpt-5.5 | low/medium/high/xhigh/max (5) | thinking.json override |
| minimax-cn | MiniMax-M3 | low/medium/high (3) | fallback |
| openrouter | gemma-4 / qwen3.7 | low/medium/high (3) | fallback |
| openrouter | llama-3.3 | 无 | reasoning=false |
