# 模型目录 + Provider 配置

## 1. 核心原则

**每个 provider 的所有配置自包含在自己的文件夹里。** 模型列表、thinking 映射——全在 `openprogram/providers/<provider>/` 下。加一个新 provider = 加一个文件夹。

两类数据分开存、分开更新：
- **thinking.json**：进 git，很少变（API 参数格式几个月才改一次）
- **models.json**：`.gitignore` 忽略，运行时 Fetch 生成（模型列表随时在变）

## 2. 数据布局

```
openprogram/providers/
├── anthropic/
│   ├── anthropic.py           ← stream 实现
│   ├── thinking.json          ← 进 git：thinking 映射配置
│   ├── models.json            ← .gitignore：运行时 Fetch 生成的模型列表
│   └── ...
├── google/
│   ├── google.py
│   ├── thinking.json
│   ├── models.json
│   └── ...
├── openai_codex/
│   ├── openai_codex.py
│   ├── thinking.json
│   ├── models.json
│   └── ...
└── _shared/                   ← 公共工具函数，不是 provider
```

`.gitignore`：
```
openprogram/providers/*/models.json
```

### 2.1 开箱即用

用户第一次启动时还没 Fetch 过，`models.json` 不存在。兜底数据从 `models.dev` 缓存获取（启动时自动拉，TTL 24h）。`thinking.json` 跟代码一起发布，始终可用。

## 3. thinking.json 结构

每个 provider 一份，声明该 provider 的 API 怎么接收 thinking/effort 参数：

**Anthropic**（用字符串）：
```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max"
  },
  "default_effort": "high",
  "model_overrides": {
    "claude-opus-4-7": {
      "variant": "opus47",
      "effort_map": {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max"
      }
    }
  }
}
```

**Google Gemini**（用 token 数）：
```json
{
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
```

**OpenAI Codex**（直传字符串，部分模型不支持 xhigh）：
```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max"
  },
  "default_effort": "xhigh",
  "model_overrides": {
    "gpt-4o": {
      "effort_map": {
        "low": "low",
        "medium": "medium",
        "high": "high"
      }
    }
  }
}
```

### 3.1 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `wire_format` | `"effort_string" \| "budget_tokens"` | API 用字符串还是数字 |
| `effort_map` | `dict[str, str]` | 框架 ThinkingLevel → API 字符串。`wire_format="effort_string"` 时用 |
| `budget_map` | `dict[str, int]` | 框架 ThinkingLevel → token 数。`wire_format="budget_tokens"` 时用 |
| `default_effort` | `str` | 该 provider 的默认级别 |
| `model_overrides` | `dict[str, {...}]` | 少数模型的覆盖。有的字段覆盖 provider 级别，没有的继承 |
| `model_overrides.<id>.variant` | `str` | 给 provider 代码的标记，走不同的请求体组装分支 |

### 3.2 thinking_levels 从映射表推导

不需要手动维护 `thinking_levels` 列表。规则：
- 模型 `reasoning=False` → 空列表（UI 隐藏滑块）
- 模型 `reasoning=True` 且有 `model_overrides` → 用 override 的映射表 key
- 模型 `reasoning=True` 无 override → 用 provider 的映射表 key
- 映射表有哪些 key，UI 就显示哪些档位

## 4. models.json 结构

Fetch Models 生成，存在 provider 文件夹里，不进 git：

```json
{
  "fetched_at": "2026-06-18T03:00:00Z",
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

## 5. 加载逻辑

```python
def build_MODELS():
    merged = {}
    for provider_dir in providers 目录下所有 provider 子目录:
        # 1. 读 thinking.json（跟代码发布，始终存在）
        thinking_spec = read(provider_dir / "thinking.json")

        # 2. 读 models.json（Fetch 生成，可能不存在）
        models_data = read(provider_dir / "models.json")

        # 3. 没有 models.json → 兜底到 models.dev 缓存
        if not models_data:
            models_data = models_dev_lookup(provider_id)

        # 4. 合并
        for model_id, model_data in models_data["models"].items():
            thinking_levels = derive_levels(model_data, thinking_spec)
            merged[f"{provider_id}/{model_id}"] = Model(
                id=model_id,
                provider=provider_id,
                thinking_levels=thinking_levels,
                ...
            )
    return merged
```

### 5.1 provider 翻译

provider 的 `stream_simple()` 不再 hardcode 映射 dict：

```python
def translate_reasoning(model, level):
    spec = load_thinking_spec(model.provider)  # 读 thinking.json
    override = spec.get("model_overrides", {}).get(model.id)

    if override and level in override.get("effort_map", {}):
        api_value = override["effort_map"][level]
    elif spec["wire_format"] == "effort_string":
        api_value = spec["effort_map"].get(level, spec["default_effort"])
    else:
        api_value = spec["budget_map"].get(level, 8192)

    return api_value
```

provider 只管把 `api_value` 塞进自己 API 请求体的正确位置。

## 6. 退役清单

| 旧模块 | 替代 |
|---|---|
| `_catalog/*.json` | 各 provider 的 `models.json`（Fetch 生成） |
| `_catalog/fetched/*.json` | 同上 |
| `~/.openprogram/models/` | 不需要了，数据在 provider 文件夹里 |
| `thinking_catalog.py` 的 `THINKING_OVERRIDES` | `thinking.json` 的 `model_overrides` |
| `thinking_catalog.py` 的 `derive_thinking_fields()` | 从映射表 key 推导 |
| `_thinking.py` 的 `THINKING_CONFIGS` | `thinking.json` 的 `default_effort` |
| 各 provider 的 `_EFFORT_MAP` / `_THINKING_BUDGETS` | `thinking.json` |

## 7. 迁移步骤

1. **写 thinking.json**：为每个 provider 创建 `thinking.json`，从代码里的 hardcoded dict 提取
2. **加载器**：写 `load_thinking_spec()` 读 `thinking.json`，各 provider 调它代替内部 dict
3. **Fetch 改向**：Fetch Models 写到 `providers/<provider>/models.json`
4. **加载器改向**：`models_generated.py` 的 `_load()` 改为遍历 provider 文件夹读 `models.json`
5. **.gitignore**：加 `openprogram/providers/*/models.json`
6. **清旧**：删 `_catalog/`、`THINKING_OVERRIDES`、`THINKING_CONFIGS`、各 provider hardcoded dict
7. **验证**：全量测试 + 浏览器自检

每步独立 commit，可验证可回滚。

## 8. Fetch 流程

1. 用户点"Fetch Models"
2. 调 provider 的 fetcher 从 API 拉模型列表
3. 写入 `openprogram/providers/<provider>/models.json`（覆盖式）
4. 触发 `build_MODELS()` 热重建
5. 前端重新请求 `/api/agent_settings`，拿到更新后的数据

## 9. models.dev 的角色

`models.dev` 是通用底料（pricing、能力细节），用于：
- 没 Fetch 过的 provider 的兜底数据
- 补充 Fetch 结果里没有的字段（如 pricing——官方 API 通常不返回价格）

缓存在内存或临时文件，TTL 24h，启动时惰性刷新。不存在 provider 文件夹里（它是跨 provider 的通用数据）。
