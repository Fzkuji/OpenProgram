# 模型目录与 Provider 配置

> Thinking effort 的控制逻辑见 [thinking-effort.md](thinking-effort.md)。本文讲模型目录的数据布局、配置结构、Fetch 流程和数据合并。

## 1. 核心原则

**每个 provider 自包含。** 所有配置（thinking 映射、探测脚本、Fetch 数据）都在 `providers/<provider>/` 下。加一个新 provider = 加一个文件夹；什么都不加也行，走 OpenAI 兼容 fallback。

两类数据分开存：

| | thinking.json | models.cache.json |
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
│   └── models.cache.json        ← .gitignore：Fetch 生成的模型列表
├── deepseek/
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.cache.json
├── google/
│   ├── google.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.cache.json
├── openai_codex/
│   ├── openai_codex.py
│   ├── thinking.json
│   ├── probe_thinking.py
│   └── models.cache.json
├── github_copilot/
│   ├── thinking.json            ← wire_format: "none"（不支持 thinking）
│   └── ...
├── thinking_spec.py             ← 公共：加载 thinking.json + 翻译 + 推导
├── thinking_catalog.py          ← 公共：启动时填 Model 对象的 thinking 字段
└── _shared/                     ← 公共工具函数
```

`.gitignore` 里有一行 `openprogram/providers/*/models.cache.json`，所有 provider 的 models.cache.json 都不进 git。

### 2.1 没有文件夹的 provider

社区 provider（groq、mistral 等）可以没有文件夹。此时：
- **thinking.json**：`thinking_spec.get_thinking_spec()` 返回 OpenAI 兼容 fallback（`effort_string` + `low/medium/high`）
- **probe_thinking.py**：Fetch 时 `_load_probe()` catch ImportError，静默跳过
- **models.cache.json**：Fetch 时 `_provider_dir()` 自动创建文件夹并写入

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

## 4. models.cache.json 结构

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

Anthropic 的 models.cache.json 还包含 `supports_adaptive` 字段（从 capabilities API 获取）。

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
5. 写入 providers/<provider_dir>/models.cache.json
       │
       ▼
6. 前端重新请求 /api/agent_settings → UI 刷新
```

## 6. 数据合并：三层叠加

`listing.py` 的 `list_models_for_provider()` 是模型数据的唯一出口。它把三层数据合并成最终结果：

```
第 1 层: combined_models(provider_id)
         = models.cache.json (Fetch 数据) + models.dev (pricing/caps)
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
5. 用户点 Fetch → models.cache.json 自动生成 → UI 自动显示

### 8.2 社区 provider（如 groq、mistral）

什么都不用做。用户在 Settings 里填 API key + 点 Fetch：
- Fetch 走通用 `_fetch_openai_compat`
- thinking.json 缺失 → 自动用 fallback（low/medium/high）
- probe_thinking.py 缺失 → 静默跳过
- models.cache.json 写入时自动创建文件夹

## 9. 现状偏离与迁移：干掉 `_catalog/`

> 本节记录代码与上面设计的偏离，以及回归到「provider 自包含」的迁移路径。

### 9.1 偏离

上面第 1–8 节是**目标设计**：模型数据在 `providers/<p>/`，provider 自包含。
但当前代码并非如此——`MODELS` 主字典（`providers/models_generated.py:_load`）
**只从 `openprogram/providers/_catalog/*.json` 加载**（22 个文件、752 条模型，进 git）。
`providers/<p>/models.cache.json` 只是 Fetch 的本地缓存、**gitignore、且不带 `api`/`base_url`**，
从未喂给 `MODELS`。`_catalog/` 是 `a449a431`（把两万行 `models_generated.json` 拆成
per-provider 小文件）时引入的中间层，偏离了本文的自包含设计。

**为什么不能直接删 `_catalog/`：** 它是 752 条模型 `api`+`base_url` 的**唯一 git 源**。
删掉 `MODELS` 会静默变空 → 所有 `get_model()` 返回 None → 每个 provider「Unknown model」。
`providers/<p>/models.cache.json` 顶不上，因为它缺 `api`/`base_url` 且不进 git。
补 `api`/`base_url` 的回填逻辑（`_default_api_for`/`_resolve_base_url`）自身也读 `MODELS`，
构成循环——`_catalog/` 一空，回填也塌。

### 9.2 provider 级配置：`provider.json`（endpoint 分组）

**关键约束（实测）：一个 provider 内部可以有多个 wire。** 不是理论——`opencode` 的 30 个
模型分属 4 个 `(api, base_url)` 组合（`openai-completions@/zen/v1`、`anthropic-messages@/zen`、
`google-generative-ai@/zen/v1`、`openai-responses@/zen/v1`）；`github-copilot` 的 19 个
模型分属 3 个 `api`（同一 base_url）。见 `_catalog/opencode.json`、`_catalog/github-copilot.json`。
所以 `api`/`base_url` **不能是纯 provider 级单值**。

但规律是：`api` 与 `base_url` **成对绑定，且组合数很少**（opencode 4 个、copilot 3 个）——
不是每模型随机各异，而是「几个固定 endpoint 分组，每组挂一批模型」。据此设计：

```
providers/<provider>/provider.json   ← 进 git
{
  "id": "opencode",
  "no_proxy": false,                        // provider 级默认；凭据可 per-key 覆盖
  "endpoints": {
    "default":   {"api": "openai-completions",   "base_url": "https://opencode.ai/zen/v1"},
    "anthropic": {"api": "anthropic-messages",   "base_url": "https://opencode.ai/zen"},
    "google":    {"api": "google-generative-ai", "base_url": "https://opencode.ai/zen/v1"},
    "responses": {"api": "openai-responses",     "base_url": "https://opencode.ai/zen/v1"}
  }
}
```
- **单 wire provider**（deepseek、bailian、openai…）只有一个 `default` endpoint，退化成最简形式。
- **多 wire provider**（opencode、copilot）声明多个 endpoint；每个 `(api, base_url)` 成对、去重、集中，不再逐模型重复。
- `models.json` 里模型用 `"endpoint": "anthropic"` 引用组名，缺省走 `default`。
  加载器据此把模型的 `api`/`base_url` 从对应 endpoint 取出，填进 `Model`。

这个模型也天然覆盖「同服务多协议」：百炼的 OpenAI 兼容端点与 Anthropic 兼容端点
（`.../compatible-mode/v1` 与 `.../apps/anthropic`）就是同一 provider 下两个 endpoint，
不必拆成两个 provider。

`no_proxy` 仍是 provider 级（endpoint 通常同网络域，代理策略一致）；如需 per-endpoint
再细化留待后续。凭据级 per-key 覆盖 `base_url`（并新增 `no_proxy`，见 P1）优先级：
凭据 > provider.json 对应 endpoint > 兜底。

### 9.2b 迁移必须逐字段保留（否则丢数据）

`_catalog/<p>.json` 每条模型带的字段不止 id/context——拆分脚本与新 loader 必须全部保留：

| 字段 | 风险 | 证据 |
|---|---|---|
| `cost`（**嵌套对象** `{input,output,cache_read,cache_write}`） | 若按旧文档扁平 `input_cost/output_cost` 映射会丢 → `Model.cost` 退回 0 | `_catalog/openai.json` cost 块；`types.py` Model.cost 默认 0 |
| `input`（多模态 `["text","image"]`） | 丢则退回 `["text"]`，视觉能力消失 | `types.py` Model.input 默认 `["text"]` |
| `headers`（静态请求头） | GitHub Copilot 依赖；请求层会 merge `model.headers` | `_catalog/github-copilot.json` headers 块；`openai_responses.py` header merge |
| `compat` | 请求兼容开关，不能丢 | `_catalog/*.json` compat 字段 |
| `api` + `base_url` | 由 endpoint 分组提供（见 9.2） | 见上 |

### 9.3 迁移顺序（留旧、建新、验证、删旧——每步系统不瘫、数据不丢）

1. **建新加载源**：`models.json` 进 git（去掉 `.gitignore` 那行），新增 `provider.json`。
   把 22 个 `_catalog/<p>.json` 拆成各 provider 的 `provider.json`（抽公共 api/base_url）
   + `models.json`（逐模型规格）。一次性迁移脚本，逐条等价校验。
2. **加载器双源**：`_load()` 同时读 `_catalog/`（旧）和 `providers/<p>/provider.json`+`models.json`（新），
   新源优先、key 仍 `<provider>/<id>`，公共接口不变（下游零改动）。解决 9.1 的循环：
   `api`/`base_url` 现在有 provider.json 这个非 `MODELS` 来源，回填逻辑不再自我依赖。
3. **验证**：全部 22 provider 从新源解析出的 Model 与旧 `_catalog/` 逐条等价；
   端到端 `exec` 每个 provider 至少一个模型。
4. **删旧**：确认新源覆盖全部、双源加载只剩新源命中后，`git rm -r _catalog/`，
   `_load()` 去掉旧分支。

阶段间可回滚；第 4 步前系统始终有 `_catalog/` 兜底。

### 9.4 其余待解点（codex 审出，实现时必须覆盖）

1. **循环依赖的完整解法。** 光加 `provider.json` 不够——`_default_api_for`/`_resolve_base_url`
   （`webui/_model_catalog/providers.py`、`storage.py`）自身读 `MODELS` 来补 api/base_url，
   构成 providers→webui→providers 循环。迁移时须把这两个函数改为读 provider metadata
   （provider.json 的 endpoints），或把该逻辑下沉到 `openprogram.providers` 层，
   使 `models_generated.py` 不 import `webui`。
2. **`no_proxy` 尚未存在。** 当前 `CredentialData`（`auth/types.py`）只有 `base_url/headers/data`，
   **没有 `no_proxy` 字段**。9.2 引用的凭据级 no_proxy 覆盖依赖 **P1** 先落地：
   `CredentialData` + `ResolvedConnection` + `Model` 加 `no_proxy`，三 wire 解析。
3. **alias / 双 key 不能丢。** `get_model()` 依赖 `<provider>/<id>` key + alias 回退
   （`models.py`）。`gemini-subscription` 同时有 `google-gemini-cli/<id>` 和
   `gemini-subscription/<id>` 两套 key（`_catalog/gemini-subscription.json`）；按
   `(provider,id)` 去重会把 752 → 747，丢 5 个 legacy key。迁移须保留全部 key 别名。
4. **`claude-code` 特殊路径。** thinking 走 alias→anthropic（`thinking_spec.py`）；
   models.dev 数据借 anthropic（`provider_models.py`）；fetch 特殊处理（`fetchers/anthropic.py`）。
   它不是普通 provider 目录，迁移须保留这套借用关系。
5. **用户自定义 provider 的动态注册路径未覆盖。** `_register_custom_model_in_registry`
   （`webui/_runtime_management.py`）从 `custom_models` 构造 `Model` 写入全局 `MODELS`，
   仍调 `_default_api_for`/`_resolve_base_url`。新 loader 换源后，这条路径也要跟着改到新的
   api/base_url 来源，否则自定义 provider 会拿错 api/base_url。
6. **Fetch 覆写 vs git 源冲突。** Fetch 现在覆写 `providers/<p>/models.cache.json`（`provider_models.py`），
   而该文件被 gitignore。若把 models.cache.json 改成 git 源，用户 Fetch 会改动版本控制内的内置数据。
   迁移须区分「内置模型（git，手维护/迁移生成）」与「Fetch 缓存（gitignore）」两个文件——
   内置模型是 `models.json`（进 git），Fetch 写到独立的 `models.cache.json`，
   加载时合并（内置为准 or 缓存补充，需定优先级）。
7. **验证粒度。** 「每 provider 一个模型 exec」不够——多 wire provider 须按每个
   `(api, base_url, headers, compat)` 组合各验证一个模型，否则漏掉 provider 内部差异。
