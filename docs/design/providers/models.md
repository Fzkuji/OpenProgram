# 模型目录 + Provider 配置

## 1. 核心原则

**每个 provider 的配置自包含在自己的文件夹里。** 加一个新 provider = 加一个文件夹（或什么都不加，走 fallback）。

两类数据分开存、分开更新：
- **thinking.json**：进 git，很少变（API 参数格式几个月才改一次）
- **Fetch 数据**：`.gitignore` 忽略，运行时 Fetch 生成（模型列表随时在变）

## 2. 数据布局

```
openprogram/providers/
├── anthropic/
│   ├── anthropic.py           ← stream 实现
│   ├── thinking.json          ← 进 git：thinking 映射配置
│   ├── probe_thinking.py      ← Fetch 时自动运行的探测脚本
│   └── ...
├── deepseek/
│   ├── thinking.json          ← V4 五档 + R1 空 override
│   ├── probe_thinking.py
│   └── __init__.py
├── google/
│   ├── google.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── ...
├── openai_codex/
│   ├── openai_codex.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   ├── models.json            ← .gitignore：Fetch 生成的模型列表
│   └── ...
├── thinking_spec.py           ← 公共加载器：读 thinking.json + 翻译 + 推导
├── thinking_catalog.py        ← 启动时给 Model 对象填 thinking 字段
└── _shared/                   ← 公共工具函数，不是 provider
```

### 2.1 开箱即用

用户第一次启动时还没 Fetch 过。兜底数据从 `models.dev` 缓存获取（启动时自动拉，TTL 24h）。

`thinking.json` 跟代码一起发布。**没有 thinking.json 的 provider 自动用 OpenAI 兼容 fallback**（`effort_string` + `low/medium/high` 三档）——社区 provider（groq、mistral、openrouter 等）加进来不需要任何配置。

### 2.2 Provider alias

`claude-code` 共用 `anthropic` 的 thinking.json（同 API、同模型、同 capabilities）。由 `thinking_spec._THINKING_ALIASES` 映射，不需要复制文件。

## 3. thinking.json 结构

每个 provider 一份，声明该 provider 的 API 怎么接收 thinking/effort 参数。

**Anthropic**（effort 字符串 + 从 API 自动获取的 model_overrides）：
```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "low", "low": "low",
    "medium": "medium", "high": "high",
    "xhigh": "xhigh", "max": "max"
  },
  "default_effort": "high",
  "model_overrides": {
    "claude-opus-4-8": {
      "effort_map": {"low":"low","medium":"medium","high":"high","xhigh":"xhigh","max":"max"}
    },
    "claude-opus-4-5-20251101": {
      "effort_map": {"low":"low","medium":"medium","high":"high"}
    }
  }
}
```

**DeepSeek**（V4 五档 + R1 无 effort 控制）：
```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "minimal", "low": "low",
    "medium": "medium", "high": "high", "max": "max"
  },
  "default_effort": "medium",
  "model_overrides": {
    "deepseek-reasoner": {"effort_map": {}},
    "deepseek-chat": {"effort_map": {}}
  }
}
```

**Google Gemini**（token 数）：
```json
{
  "wire_format": "budget_tokens",
  "budget_map": {
    "minimal": 512, "low": 2048, "medium": 8192,
    "high": 24576, "xhigh": 32768, "max": 65536
  },
  "default_effort": "medium"
}
```

**GitHub Copilot**（不支持 thinking）：
```json
{
  "wire_format": "none",
  "default_effort": null
}
```

### 3.1 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `wire_format` | `"effort_string" \| "budget_tokens" \| "none"` | API 用字符串、数字、还是不支持 |
| `effort_map` | `dict[str, str]` | 框架 ThinkingLevel → API 字符串 |
| `budget_map` | `dict[str, int]` | 框架 ThinkingLevel → token 数 |
| `default_effort` | `str \| null` | 该 provider 的默认级别 |
| `model_overrides` | `dict[str, {...}]` | 特殊模型的覆盖 |
| `model_overrides.<id>.effort_map` | `dict` | 空 `{}` = 该模型无 effort 控制 |

### 3.2 thinking_levels 从映射表推导

不需要手动维护 `thinking_levels` 列表。规则：
- `reasoning=false` → 空列表（UI 隐藏滑块）
- 有 `model_overrides` → 用 override 的映射表 key（空 `{}` → 空列表）
- 无 override → 用 provider 的映射表 key
- 映射表有哪些 key，UI 就显示哪些档位

## 4. Fetch 数据结构

Fetch Models 后存入 `providers/<provider_dir>/models.json`，不进 git：

```json
{
  "provider": "deepseek",
  "models": [
    {
      "id": "deepseek-v4-flash",
      "reasoning": true,
      "thinking_levels": ["minimal", "low", "medium", "high", "max"],
      "default_thinking_level": "medium",
      "context_window": 1000000,
      "max_tokens": 384000
    }
  ]
}
```

## 5. 数据合并逻辑

```python
# listing.py: list_models_for_provider()

for raw in combined_models(provider_id):    # Fetch 数据 + models.dev
    # 优先级 1-2: thinking.json 的 model_overrides / provider 级别
    levels = derive_thinking_fields(provider_id, model_id, reasoning)

    # 优先级 3: Fetch 数据里的 thinking_levels
    if not levels and raw.get("thinking_levels"):
        levels = raw["thinking_levels"]

    # 优先级 4: 无 thinking.json → fallback (low/medium/high)
    # (已包含在 derive_thinking_fields 里)
```

`list_enabled_models` 委托给 `list_models_for_provider`，保证两者输出一致。`_thinking.py` 的 `get_thinking_config_for_model` 也从 `list_models_for_provider` 取数据。**三个消费方走同一条路径。**

### 5.1 provider 翻译

provider 的 `stream_simple()` 调 `thinking_spec.translate_reasoning()`：

```python
def translate_reasoning(provider_id, model_id, level):
    spec = get_thinking_spec(provider_id)  # 读 thinking.json（或 fallback）

    # 1. model_overrides
    override = spec["model_overrides"].get(model_id)
    if override:
        emap = override.get("effort_map")
        if emap is not None:
            return emap.get(level) if emap else None  # 空 = 不支持

    # 2. provider 级别
    if spec["wire_format"] == "effort_string":
        return spec["effort_map"].get(level)
    elif spec["wire_format"] == "budget_tokens":
        return spec["budget_map"].get(level)

    return None  # wire_format == "none"
```

provider 只管把返回值塞进自己 API 请求体的正确位置。

## 6. Fetch 流程

1. 用户点"Fetch Models"
2. 调 provider 的 fetcher 从 API 拉模型列表
3. **Anthropic 系**：对每个模型额外调 `GET /v1/models/{id}`，从 `capabilities.effort` 提取每个级别的 supported 状态
4. **其他 provider**：自动调 `probe_thinking.probe()` 从 model id 推断 reasoning
5. enrichment 步骤合并 models.dev 数据（pricing、capabilities）
6. 写入 `_catalog/fetched/<provider>.json`
7. 前端重新请求 `/api/agent_settings`，拿到更新后的数据

## 7. models.dev 的角色

`models.dev` 是通用底料（pricing、reasoning 标记、能力细节），用于：
- 没 Fetch 过的 provider 的兜底数据
- 补充 Fetch 结果里没有的字段（如 pricing——官方 API 通常不返回价格）

缓存在内存或临时文件，TTL 24h，启动时惰性刷新。
