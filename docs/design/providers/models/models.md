# 模型目录与 Provider 配置

> Thinking effort 的控制逻辑见 [thinking-effort.md](thinking-effort.md)。本文讲模型目录的数据布局、配置结构、Fetch 流程和数据合并。

## 1. 核心原则

**每个 provider 自包含。** 所有配置（thinking 映射、探测脚本、Fetch 数据）都在 `providers/<provider>/` 下。加一个新 provider = 加一个文件夹；什么都不加也行，走 OpenAI 兼容 fallback。

两类数据分开存：

| | thinking.json | models.json |
|---|---|---|
| 内容 | API 参数格式、effort 映射、per-model override | Fetch 拉到的模型列表（id、context、reasoning 等） |
| 变化频率 | 几个月一次（API 格式改了才变） | 每次 Fetch 覆写 |
| 版本控制 | 进 git | .gitignore |

## 2. 文件布局

```
openprogram/providers/
├── anthropic/
│   ├── anthropic.py             ← stream 实现
│   ├── thinking.json            ← 进 git：thinking effort 映射
│   ├── probe_thinking.py        ← Fetch 时自动运行的探测脚本
│   └── models.json              ← .gitignore：Fetch 生成的模型列表
├── deepseek/
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.json
├── google/
│   ├── google.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.json
├── openai_codex/
│   ├── openai_codex.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.json
├── github_copilot/
│   ├── thinking.json            ← wire_format: "none"（不支持 thinking）
│   └── ...
├── thinking_spec.py             ← 公共：加载 thinking.json + 翻译 + 推导
├── thinking_catalog.py          ← 公共：启动时填 Model 对象的 thinking 字段
└── _shared/                     ← 公共工具函数
```

`.gitignore` 里有一行 `openprogram/providers/*/models.json`，所有 provider 的 models.json 都不进 git。

### 2.1 没有文件夹的 provider

社区 provider（groq、mistral 等）可以没有文件夹。此时：
- **thinking.json**：`thinking_spec.get_thinking_spec()` 返回 OpenAI 兼容 fallback（`effort_string` + `low/medium/high`）
- **probe_thinking.py**：Fetch 时 `_load_probe()` catch ImportError，静默跳过
- **models.json**：Fetch 时 `_provider_dir()` 自动创建文件夹并写入

### 2.2 Provider alias

`claude-code` 和 `anthropic` 用同一套 API 和模型。`thinking_spec._THINKING_ALIASES = {"claude-code": "anthropic"}` 让 claude-code 共用 anthropic 的 thinking.json，不复制文件。

## 3. thinking.json 结构

声明该 provider 的 API 怎么接收 thinking/effort 参数。

### 3.1 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `wire_format` | `"effort_string"` / `"budget_tokens"` / `"none"` | API 接受字符串、token 数、还是不支持 thinking |
| `effort_map` | `{框架级别: API字符串}` | `wire_format="effort_string"` 时的映射表 |
| `budget_map` | `{框架级别: token数}` | `wire_format="budget_tokens"` 时的映射表 |
| `default_effort` | `string` / `null` | 用户不选时的默认级别 |
| `model_overrides` | `{model_id: {...}}` | 特定模型的覆盖配置 |
| `model_overrides.<id>.effort_map` | `{}` 空 dict | 表示该模型不支持 effort 调节 |

### 3.2 示例

**Anthropic**——effort 字符串，model_overrides 由 API capabilities 自动写入：

```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "low", "low": "low", "medium": "medium",
    "high": "high", "xhigh": "xhigh", "max": "max"
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

Opus 4.8 的 override 只有 5 个 key（没有 minimal），所以 UI 显示 5 档。Opus 4.5 只有 3 个 key，UI 显示 3 档。Provider 级别的 `effort_map` 有 6 个 key，作为没有 override 的模型的 fallback。

**DeepSeek**——V4 五档，R1 无 effort 控制：

```json
{
  "wire_format": "effort_string",
  "effort_map": {
    "minimal": "minimal", "low": "low", "medium": "medium",
    "high": "high", "max": "max"
  },
  "default_effort": "medium",
  "model_overrides": {
    "deepseek-reasoner": {"effort_map": {}},
    "deepseek-chat": {"effort_map": {}}
  }
}
```

`deepseek-reasoner` 的 `effort_map` 是空 dict `{}`——表示这个模型有 reasoning 能力但不支持 effort 调节（R1 永远全力推理）。`deepseek-chat` 的空 dict 表示 V3 不支持 reasoning。

**Google Gemini**——用 token 数而非字符串：

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

**GitHub Copilot**——不支持 thinking：

```json
{
  "wire_format": "none",
  "default_effort": null
}
```

### 3.3 thinking_levels 的推导规则

UI 显示几档不是手动维护的，而是从映射表的 key 自动推导：

1. `reasoning=false` → 空列表，UI 不显示滑块
2. 有 `model_overrides` 且 `effort_map` 不是 null → 用 override 的 key（空 `{}` → 空列表 → UI 不显示）
3. 无 override → 用 provider 级别的 `effort_map`/`budget_map` 的 key
4. 无 thinking.json → fallback 的 key（low/medium/high）

## 4. models.json 结构

Fetch Models 生成，存在 provider 文件夹里，不进 git：

```json
{
  "provider": "deepseek",
  "models": [
    {
      "id": "deepseek-v4-flash",
      "name": "deepseek-v4-flash",
      "reasoning": true,
      "thinking_levels": ["minimal", "low", "medium", "high", "max"],
      "default_thinking_level": "medium",
      "context_window": 1000000,
      "max_tokens": 384000,
      "input_cost": 0.14,
      "output_cost": 0.28
    }
  ]
}
```

Anthropic 的 models.json 还包含 `supports_adaptive` 字段（从 capabilities API 获取）。

## 5. Fetch 流程

用户在 Settings 里点"Fetch Models"触发：

```
1. fetchers/__init__.py: fetch_models_remote(provider_id)
       │
       ▼
2. 选 fetcher（_FETCHERS 表 或 通用 _fetch_openai_compat）
       │
       ▼
3. 调 provider 的 API（如 /v1/models）拿模型列表
       │
       ├── Anthropic: 对每个模型额外调 GET /v1/models/{id}
       │   → _extract_thinking_caps() 提取 effort/adaptive capabilities
       │
       ├── 其他: _load_probe(provider_id) 调 probe_thinking.probe()
       │   → 从 model id 推断 reasoning 能力
       │
       ▼
4. enrichment: 合并 models.dev 数据（pricing、reasoning 标记）
       │
       ▼
5. 写入 providers/<provider_dir>/models.json
       │
       ▼
6. 前端重新请求 /api/agent_settings → UI 刷新
```

## 6. 数据合并：三层叠加

`listing.py` 的 `list_models_for_provider()` 是模型数据的唯一出口。它把三层数据合并成最终结果：

```
第 1 层: combined_models(provider_id)
         = models.json (Fetch 数据) + models.dev (pricing/caps)
         → 得到每个模型的基础信息 (id, reasoning, context_window, ...)

第 2 层: thinking_spec.derive_thinking_fields()
         = 读 thinking.json → model_overrides → provider 级别映射 → fallback
         → 得到 thinking_levels / default / variant

第 3 层: Fetch 数据里的 thinking_levels（当第 2 层无结果时用）
         → models.dev 或 API capabilities 提供的 thinking_levels
```

合并后的结果被三个消费方使用：

| 消费方 | 用途 |
|---|---|
| `list_models_for_provider()` | Settings 页面的模型表格 |
| `list_enabled_models()` | Chat 页面的模型选择器（委托给上面） |
| `get_thinking_config_for_model()` | UI thinking picker 的选项列表（委托给上面） |

三个消费方走同一条路径，保证前端显示和后端数据完全一致。

## 7. models.dev 的角色

`models.dev` 是跨 provider 的通用数据源，提供：
- 没 Fetch 过的 provider 的模型列表（兜底）
- pricing（官方 API 通常不返回价格）
- reasoning 标记、能力细节

缓存在内存，TTL 24h，启动时惰性刷新。不存在 provider 文件夹里（它是跨 provider 的，不属于任何一个）。

## 8. 加新 provider 的步骤

### 8.1 有专属 API 的 provider（如 DeepSeek）

1. 创建 `providers/<name>/` 目录
2. 写 `thinking.json`（声明 wire_format、effort_map、必要的 model_overrides）
3. 写 `probe_thinking.py`（暴露 `probe()` 函数，从 API 或 model id 推断 reasoning）
4. 在 `fetchers/` 里加一个专属 fetcher（或走通用 `_fetch_openai_compat`）
5. 用户点 Fetch → models.json 自动生成 → UI 自动显示

### 8.2 社区 provider（如 groq、mistral）

什么都不用做。用户在 Settings 里填 API key + 点 Fetch：
- Fetch 走通用 `_fetch_openai_compat`
- thinking.json 缺失 → 自动用 fallback（low/medium/high）
- probe_thinking.py 缺失 → 静默跳过
- models.json 写入时自动创建文件夹
