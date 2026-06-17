# 模型目录 + Provider 配置

## 1. 核心原则

**每个 provider 的所有配置自包含在自己的文件夹里。** 模型列表、thinking 映射、pricing——全在 `openprogram/providers/<provider>/` 下，不集中存也不散到别的地方。加一个新 provider = 加一个文件夹。

## 2. 数据布局

### 2.1 代码仓库（静态基线）

每个 provider 文件夹里放一个 `models.json`，跟代码一起发布：

```
openprogram/providers/
├── anthropic/
│   ├── __init__.py
│   ├── anthropic.py           ← stream 实现
│   ├── auth_adapter.py
│   ├── models.json            ← 静态基线：模型列表 + thinking 配置
│   └── ...
├── google/
│   ├── google.py
│   ├── models.json
│   └── ...
├── openai_codex/
│   ├── openai_codex.py
│   ├── models.json
│   └── ...
├── openai_completions/
│   ├── openai_completions.py
│   ├── models.json
│   └── ...
├── openai_responses/
│   ├── openai_responses.py
│   ├── models.json
│   └── ...
├── amazon_bedrock/
│   ├── amazon_bedrock.py
│   ├── models.json
│   └── ...
└── _shared/                   ← 公共工具函数，不是 provider
```

### 2.2 用户目录（运行时动态数据）

Fetch Models 从 API 拉到的数据存用户目录，覆盖式：

```
~/.openprogram/models/
├── models_dev.json              ← models.dev 全量缓存（定期自动刷）
└── fetched/
    ├── anthropic.json           ← Fetch anthropic 官方列表（覆盖式）
    ├── openai.json
    └── <provider>.json
```

### 2.3 两者的关系

| | 代码仓库 `models.json` | 用户目录 `fetched/<p>.json` |
|---|---|---|
| 用途 | 开箱即用的静态基线 | 用户账号下的真实模型列表 |
| 更新方式 | 随代码发布 | 用户点"Fetch Models" |
| 包含 thinking 配置 | ✅ | ❌（只有模型列表） |
| 优先级 | 低（兜底） | 高（有则覆盖模型列表） |

thinking 配置只在代码仓库的 `models.json` 里——它是 provider 的 API 行为，不随用户账号变化。模型列表可以被 Fetch 覆盖（用户账号能用哪些模型因订阅不同），但"这个 provider 的 effort 怎么映射"是固定的。

## 3. models.json 结构

```json
{
  "provider": {
    "id": "anthropic",
    "thinking": {
      "wire_format": "effort_string",
      "effort_map": {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max"
      },
      "default_effort": "high"
    }
  },
  "models": {
    "claude-opus-4-8": {
      "name": "Claude Opus 4.8",
      "reasoning": true,
      "context_window": 1000000,
      "max_tokens": 128000,
      "input_cost": 5.0,
      "output_cost": 25.0,
      "cache_read_cost": 0.5,
      "cache_write_cost": 6.25,
      "vision": true,
      "tools": true
    },
    "claude-sonnet-4-6": {
      "name": "Claude Sonnet 4.6",
      "reasoning": true,
      "context_window": 1000000,
      "max_tokens": 64000,
      "input_cost": 3.0,
      "output_cost": 15.0
    }
  }
}
```

Gemini 的 `models.json`（用 budget_tokens 而不是字符串）：

```json
{
  "provider": {
    "id": "google",
    "thinking": {
      "wire_format": "budget_tokens",
      "budget_map": {
        "minimal": 512,
        "low": 2048,
        "medium": 8192,
        "high": 24576,
        "xhigh": 32768,
        "max": 65536
      },
      "default_effort": "medium"
    }
  },
  "models": { ... }
}
```

### 3.1 provider.thinking 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `wire_format` | `"effort_string" \| "budget_tokens"` | API 用字符串还是数字 |
| `effort_map` | `dict[str, str]` | 框架 ThinkingLevel → API 字符串值。`wire_format="effort_string"` 时必填 |
| `budget_map` | `dict[str, int]` | 框架 ThinkingLevel → token 数。`wire_format="budget_tokens"` 时必填 |
| `default_effort` | `str` | 该 provider 的默认级别 |

### 3.2 per-model 覆盖（少数模型需要）

大多数模型走 provider 级别的 thinking 配置。少数模型需要特殊处理时，在模型条目里加 `thinking_override`：

```json
{
  "models": {
    "claude-opus-4-7": {
      "reasoning": true,
      "thinking_override": {
        "variant": "opus47",
        "effort_map": {
          "low": "low",
          "medium": "medium",
          "high": "high"
        }
      }
    }
  }
}
```

`thinking_override` 有的字段覆盖 provider 级别的对应字段，没有的继承 provider 的。`variant` 标记给 provider 代码，让它知道这个模型的请求体组装方式不同。

## 4. 加载逻辑

### 4.1 构建 MODELS

```python
def build_MODELS():
    merged = {}
    for provider_dir in providers目录下所有子目录:
        # 1. 读代码仓库的 models.json（静态基线）
        static = read(provider_dir / "models.json")
        provider_id = static["provider"]["id"]
        thinking_spec = static["provider"]["thinking"]

        # 2. 读用户目录的 fetched（如果有）
        fetched = read(~/.openprogram/models/fetched/{provider_id}.json)

        # 3. 读 models.dev 缓存补 pricing
        models_dev = read(~/.openprogram/models/models_dev.json)

        # 4. 合并：fetched > static > models_dev
        model_list = fetched 的模型列表 if fetched else static["models"]
        for model_id, model_data in model_list:
            # 补 pricing（从 models.dev）
            pricing = models_dev_lookup(provider_id, model_id)
            # 补 thinking_levels（从 provider.thinking + model.thinking_override）
            levels = derive_thinking_levels(model_data, thinking_spec)

            merged[f"{provider_id}/{model_id}"] = Model(
                id=model_id,
                provider=provider_id,
                thinking_levels=levels,
                ...合并所有字段
            )

    return merged
```

### 4.2 thinking_levels 推导

不再需要 `thinking_catalog.py` 里的 `derive_thinking_fields()` 和 `THINKING_OVERRIDES`——逻辑简化为：

```python
def derive_thinking_levels(model_data, provider_thinking_spec):
    if not model_data.get("reasoning"):
        return []  # 不支持 thinking，UI 隐藏

    # 模型有 override → 用 override 的 key 作为 levels
    override = model_data.get("thinking_override")
    if override and override.get("effort_map"):
        return list(override["effort_map"].keys())

    # 否则用 provider 的 effort_map/budget_map 的 key
    if provider_thinking_spec.get("effort_map"):
        return list(provider_thinking_spec["effort_map"].keys())
    if provider_thinking_spec.get("budget_map"):
        return list(provider_thinking_spec["budget_map"].keys())

    return []
```

`thinking_levels` 不再是手动维护的字段，而是从映射表的 key 自动推导——映射表有哪些 key，就支持哪些级别。

### 4.3 provider 翻译

provider 的 `stream_simple()` 不再写 hardcoded dict，而是读 `ProviderThinkingSpec`：

```python
def translate_reasoning(model, level):
    spec = get_provider_thinking_spec(model.provider)

    # 如果模型有 override，优先用 override 的映射
    override = model.thinking_override
    if override and level in override.get("effort_map", {}):
        api_value = override["effort_map"][level]
    elif spec["wire_format"] == "effort_string":
        api_value = spec["effort_map"].get(level, spec["default_effort"])
    else:  # budget_tokens
        api_value = spec["budget_map"].get(level, 8192)

    return api_value
```

## 5. 退役清单

以下模块被 `models.json` 机制取代：

| 旧模块 | 状态 | 替代 |
|---|---|---|
| `_catalog/*.json` | 删除 | 各 provider 文件夹的 `models.json` |
| `_catalog/fetched/*.json` | 移到 `~/.openprogram/models/fetched/` | 位置变，格式不变 |
| `thinking_catalog.py` 的 `THINKING_OVERRIDES` | 删除 | `models.json` 里的 `thinking_override` |
| `thinking_catalog.py` 的 `derive_thinking_fields()` | 简化 | 从映射表 key 自动推导 |
| `_thinking.py` 的 `THINKING_CONFIGS` | 删除 | `models.json` 里的 `provider.thinking` |
| 各 provider 内部的 `_EFFORT_MAP` / `_THINKING_BUDGETS` / `budget_map` | 删除 | `models.json` 里的 `effort_map` / `budget_map` |

## 6. 迁移步骤

1. **写 models.json**：为每个 provider 创建 `models.json`，从现有 `_catalog/<provider>.json` + provider 代码里的 hardcoded dict 提取数据
2. **加载器**：`models_generated.py` 的 `_load()` 改为遍历 provider 文件夹读 `models.json`
3. **翻译器**：各 provider 的 `stream_simple()` 改为读 `ProviderThinkingSpec` 而不是内部 dict
4. **清旧**：删 `_catalog/`、`THINKING_OVERRIDES`、`THINKING_CONFIGS`、各 provider 的 hardcoded dict
5. **验证**：全量测试 + 浏览器自检（UI 模型列表正确、thinking 滑块正确、实际 API 调用参数正确）

每步独立 commit，可验证可回滚。

## 7. Fetch 流程

用户点"Fetch Models"时：
1. 调 provider 的 fetcher（各 provider 实现）从 API 拉模型列表
2. 写入 `~/.openprogram/models/fetched/<provider>.json`（覆盖式）
3. 触发 `build_MODELS()` 热重建（fetched 覆盖静态基线的模型列表，但 thinking 配置不变）
4. 前端重新请求 `/api/agent_settings`，拿到更新后的模型列表和 thinking 配置
