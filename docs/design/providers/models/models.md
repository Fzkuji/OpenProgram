# 模型目录与 Provider 配置（最终设计）

> 本文描述模型目录的**目标运行逻辑**：文件结构、文件与代码的交互、后端与前端各自怎么消费。
> 与当前代码的差距和迁移路径集中在第 8 节——第 1–7 节永远只写目标态，不写历史。
> Thinking effort 的参数细节见 [thinking-effort.md](thinking-effort.md)。

## 1. 一句话架构

每个 provider 一个自包含目录，目录里几份小文件；启动时经**唯一一条合并管线**汇成运行时注册表 `MODEL_REGISTRY`；后端 `get_model()` 和前端所有页面（设置页、聊天选择器、thinking 选择器）读的都是这同一份合并结果。

**核心不变式：前端能选到的模型，后端一定能解析** ——因为两边根本没有第二份数据。

## 2. 文件结构

```
openprogram/providers/
├── deepseek/                      ← 每个 provider 一个目录（下划线命名）
│   ├── provider.json              ← git：id + endpoint 分组（api/base_url）
│   ├── models.json                ← git：内置模型基线（只存外部拿不到的字段）
│   ├── models.cache.json          ← gitignore：Fetch 拉到的官方模型列表
│   ├── thinking.json              ← git：thinking effort 映射
│   ├── probe_thinking.py          ← Fetch 时自动运行的探测脚本（可选）
│   └── deepseek.py                ← wire/stream 实现（仅专属协议的 provider 有）
├── catalog/                       ← 合并管线（唯一数据出口）
│   ├── loader.py                  ← 读 provider.json + models.json + models.cache.json
│   ├── models_dev.py              ← models.dev 数据源（内存缓存，TTL 24h）
│   └── merge.py                   ← 分层叠加，产出 Model 对象
├── thinking_spec.py               ← 读 thinking.json + effort 翻译
├── thinking_catalog.py            ← derive_thinking_fields（thinking 字段推导）
├── models_generated.py            ← MODEL_REGISTRY 定义（调 catalog 管线填充）
└── models.py                      ← get_model / get_providers / get_models
```

每份文件的角色，一张表说清：

| 文件 | 进 git | 谁写 | 谁读 | 变化频率 |
|---|---|---|---|---|
| `provider.json` | ✅ | 人（加 provider 时一次） | 合并管线 | 几乎不变 |
| `models.json` | ✅ | 人（只写外部拿不到的字段） | 合并管线 | 很少 |
| `models.cache.json` | ❌ | Fetch（每次点击覆写） | 合并管线 | 每次 Fetch |
| `thinking.json` | ✅ | 人 + probe 自动回写 overrides | thinking_spec | API 格式变了才变 |
| models.dev（远端） | — | 第三方维护 | 合并管线（懒加载） | 上游持续更新 |
| `config.json` providers 节 | — | 用户在设置页操作 | 前端 listing + 运行时 | 用户随时改 |

**分工原则：每个字段只有一个权威来源。**

- `api` / `base_url` → `provider.json` 的 endpoints（成对绑定，见 2.1）
- 「有哪些模型」→ Fetch 过用 `models.cache.json`，没 Fetch 过用 `models.json` 基线，再兜底 models.dev
- 价格、context、能力 → models.dev（官方 Fetch 数据优先）
- thinking 档位 → `thinking.json` 推导（`thinking_catalog.derive_thinking_fields`）
- 「用户启用了哪些」→ `config.json`（`enabled` / `enabled_models`），是用户状态，永远不混进目录数据

`models.json` 因此很瘦：`id`、`name`、`endpoint`（非 default 时）、`key_prefix`（双 key 场景）、`headers`、`compat`，以及 models.dev 没收录时才内联的规格字段。**凡是 models.dev 或官方 API 能给的字段，一律不手写**——手写数据没有更新机制，必然腐烂。

### 2.1 provider.json：endpoint 分组

一个 provider 内部可以有多个 wire（实测：opencode 30 个模型分属 4 个 `(api, base_url)` 组合，github-copilot 19 个模型分属 3 个 api）。但组合数很少且成对绑定，所以集中声明成 endpoint 组，模型按名引用：

```json
{
  "id": "opencode",
  "endpoints": {
    "default":   {"api": "openai-completions",   "base_url": "https://opencode.ai/zen/v1"},
    "anthropic": {"api": "anthropic-messages",   "base_url": "https://opencode.ai/zen"},
    "google":    {"api": "google-generative-ai", "base_url": "https://opencode.ai/zen/v1"},
    "responses": {"api": "openai-responses",     "base_url": "https://opencode.ai/zen/v1"}
  }
}
```

- 单 wire provider（deepseek、openai…）只有一个 `default`，退化成最简形式。
- `models.json` 里模型写 `"endpoint": "anthropic"` 引用组名，缺省走 `default`。
- 目录名用下划线（`amazon_bedrock/`），`id` 字段存连字符原名（`amazon-bedrock`）——注册表 key 用连字符。
- 「同服务多协议」天然覆盖：百炼的 OpenAI 兼容端点与 Anthropic 兼容端点就是同一 provider 下两个 endpoint，不拆成两个 provider。

### 2.2 没有目录的 provider

社区 provider（fireworks、together 等）可以完全没有目录：

- 模型列表 → models.dev 兜底；用户填 key 点 Fetch 后 `_provider_dir()` 自动建目录写 `models.cache.json`
- `api`/`base_url` → models.dev 的 provider 条目提供缺省
- thinking → OpenAI 兼容 fallback（low/medium/high）
- probe → 静默跳过

加一个内置 provider = 建目录 + 写 `provider.json`（几行）+ 可选 `thinking.json`。不需要手抄模型清单。

## 3. 合并管线（唯一数据出口）

`catalog.merge` 对每个 provider 做分层叠加，从下往上、高层的非空字段覆盖低层：

```
第 4 层  thinking.json 推导        → thinking_levels / default / variant
第 3 层  models.cache.json（Fetch） → 官方权威：有哪些模型、context、能力
第 2 层  models.json（git 基线）    → 手写覆盖：name、headers、compat…
第 1 层  models.dev（懒加载）       → 兜底：模型列表、价格、能力
底座    provider.json endpoints    → api / base_url（每模型按 endpoint 名取）
```

规则：

1. **「有哪些模型」以最高的存在层为准。** Fetch 过 → cache 的列表是权威（基线里不在 cache 的行对 UI 隐藏，但仍保留在注册表里，旧会话引用不断链）；没 Fetch 过 → 基线 ∪ models.dev。
2. **字段级合并**：cache（官方）> models.json（手写）> models.dev（兜底）。手写即 override，官方即事实。
3. **thinking 字段永远推导，不存储**：`thinking.json` model_overrides > Fetch capabilities > provider 级映射 > fallback（优先级细节见 thinking-effort.md）。
4. **离线可用**：models.dev 拉不到就跳过该层，价格等字段缺省；`provider.json` + `models.json` + cache 全在本地，运行不依赖网络。
5. 输出 `Model` 对象，key = `"<prefix>/<id>"`，prefix 默认取 `provider.json` 的 `id`，逐行可用 `key_prefix` 覆盖（gemini-subscription 双 key 场景）。

管线跑完的结果就是：

```python
# openprogram/providers/models_generated.py
MODEL_REGISTRY: dict[str, Model]   # 唯一的运行时模型注册表
```

命名说明：不叫 `ENABLED_MODELS`——注册表装的是**全部已知模型**，「启用」是 `config.json` 里的用户状态，两个概念不能共用一个名字。`MODELS` 旧名太泛，弃用。

## 4. 后端怎么用

### 4.1 查询接口（`providers/models.py`）

```python
get_model("deepseek", "deepseek-v4-flash")  # → Model | None，带 alias 回退
get_providers()                              # → 有模型的 provider id 列表
get_models("deepseek")                       # → 该 provider 的全部 Model
```

`get_model` 先查 `"<provider>/<id>"`，miss 时经 `auth.aliases` 试等价 provider 名。20+ 个运行时调用方（agent、runtime、failover、registry…）只认这三个函数，不关心数据从哪来。

### 4.2 Fetch 写路径

用户在设置页点「Fetch Models」：

```
fetchers.fetch_models_remote(provider_id)
  → 调官方 API（/v1/models 等；Anthropic 额外逐模型拉 capabilities）
  → probe_thinking.probe() 推断 reasoning / 回写 thinking.json overrides
  → save_fetched() 原子覆写 providers/<p>/models.cache.json
  → 注册表失效重载（合并管线重跑该 provider）
  → 前端重新请求 → UI 刷新
```

Fetch 只写 cache 文件，永远不碰 git 内的 `models.json`——内置基线是仓库维护的，用户操作不产生 git 脏状态。但 Fetch 的结果**立即进注册表**：设置页看到的新模型，`get_model` 同一时刻就能解析。

### 4.3 自定义模型

用户手工添加的模型存 `config.json` 的 `custom_models`，经 `_register_custom_model_in_registry` 构造 `Model` 原地写入 `MODEL_REGISTRY`（注册表是同一个可变 dict，这条路径依赖此语义）。api/base_url 缺省从 `provider.json` endpoints 取，不再反向查注册表。

## 5. 前端怎么用

前端没有自己的模型数据，全部经三个 listing 函数读**同一份注册表**（`webui/_model_catalog/listing.py`，纯展示层：加 label、enabled 标记、setup hint）：

| 前端位置 | API 路由 | listing 函数 | 内容 |
|---|---|---|---|
| 设置页左侧 provider 列表 | `GET /api/providers` | `list_providers()` | 注册表 provider ∪ models.dev 社区 provider（可配置未内置的） |
| 设置页右侧模型表 | `GET /api/providers/<id>` | `list_models_for_provider()` | 该 provider 合并结果 + enabled 标记 |
| 聊天页模型选择器 | `GET /api/models/enabled` | `list_enabled_models()` | 委托上一行，按 config 的 enabled 过滤 |
| thinking 档位选择器 | （`_thinking.py`） | 委托 `list_models_for_provider` | 同一行数据里的 thinking_levels |

- **enabled 状态**只存 `config.json`（`providers.<id>.enabled` / `enabled_models`），listing 读出来打标记；勾选/取消只改 config，不动目录数据。
- `list_providers` 的社区层（models.dev 全量 provider）让用户不等代码发版就能启用 fireworks/together 这类 provider——填 key、点 Fetch，走 2.2 的自动建目录路径。
- listing 是薄壳：**不做字段合并、不做 thinking 推导**，那些全在 `providers/catalog` 管线里做完了。webui 只 import providers，providers 永远不 import webui。

## 6. 端到端时序（一次典型交互）

```
用户在设置页启用 deepseek、点 Fetch
  → models.cache.json 写入（官方新型号 deepseek-v4-flash 进来）
  → 注册表重载：MODEL_REGISTRY["deepseek/deepseek-v4-flash"] 出现
用户在模型表勾选 deepseek-v4-flash
  → config.json enabled_models 追加
用户在聊天页选中它发消息
  → 后端 get_model("deepseek", "deepseek-v4-flash") 命中同一条注册表记录
  → api/base_url 来自 provider.json endpoints，thinking 档位来自 thinking.json 推导
  → 请求发出
```

任何一步都不存在「另一份清单」，所以不存在对不上。

## 7. 不变式（改代码前先对照）

1. **单一出口**：显示给用户的模型数据和运行时解析的模型数据出自同一个合并函数。出现第二条数据链即违约。
2. **手写最小化**：`models.json` 只存 models.dev / 官方 API 给不了的字段。往里加可自动获得的字段即违约。
3. **分层单向**：`openprogram.providers` 不 import `openprogram.webui`。
4. **离线可跑**：断网时基线 + cache 足够运行，只缺价格等装饰字段。
5. **key 兼容**：`"<prefix>/<id>"` 格式、alias 回退、`key_prefix` 双 key（gemini-subscription 的 10 个 key）全部保留；注册表是同一个可变 dict（自定义模型原地写）。
6. **Fetch 不碰 git**：Fetch 只写 `models.cache.json`；`models.json` 只由人（或仓库迁移脚本）改。

## 8. 现状偏离与迁移

> 本节记录当前代码（2026-07-08）与上文目标的差距。问题现象的完整描述见
> [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md)。

### 8.1 偏离

1. **两条数据链。** 合并管线只在 webui 实现了一半：`webui/_model_catalog/provider_models.combined_models`（cache + models.dev）喂设置页；而运行时 `MODELS`（`models_generated._load` → `_catalog_new.load_new_catalog`）只读 git 的 `models.json`，从不看 cache 和 models.dev。结果：设置页能选 `deepseek-v4-flash`，`get_model` 查不到。
2. **models.json 是全量富规格**，含 `thinking_levels`、`cost`、`context_window` 等派生/可获取字段（22 个 provider、752 条），没有更新机制，已经腐烂（deepseek 只剩旧型号）。
3. **层次倒置**：models.dev 数据源和合并逻辑长在 `webui/_model_catalog/`，providers 层的 `_default_api_for`/`_resolve_base_url` 反过来读 `MODELS` 补 api/base_url，构成 providers→webui→providers 循环。
4. **`MODELS` 名字太泛**（用户已要求改名）。
5. **bailian 命名不标准**：models.dev 里同一 base_url 的 provider 叫 `alibaba-token-plan-cn`，项目里已有预留空目录 `alibaba_token_plan_cn/`；用户已明确要求改用标准名、删 `bailian/`。

### 8.2 迁移顺序（每步可独立提交、系统不瘫）

1. **bailian → alibaba_token_plan_cn**：目录改名 + `provider.json` 的 id 改 `alibaba-token-plan-cn`，模型内容照搬；管线打通后自动对齐 models.dev 的 18 个模型。独立小改，先做。
2. **下沉数据层**：把 `provider_models.py`（cache 读写 + combined 合并）和 `sources/models_dev.py` 从 `webui/_model_catalog/` 移到 `openprogram/providers/catalog/`；`_default_api_for`/`_resolve_base_url` 改读 `provider.json` endpoints。循环依赖至此解除。
3. **注册表接管线**：`_load()` 改为跑完整合并（第 3 节的五层），cache 和 models.dev 进注册表。此时两条链事实合一。
4. **listing 改薄**：`list_models_for_provider` 删掉自己那套 combined + thinking 合并，直接读注册表加展示字段。
5. **models.json 瘦身**：脚本删除全部可派生/可获取字段，逐 provider 校验合并结果与瘦身前等价（字段级 diff）。
6. **改名 `MODELS` → `MODEL_REGISTRY`**：纯机械替换 20+ 调用点，放最后一步。

### 8.3 迁移必须保住的点（历史审查所得）

- **alias / 双 key**：`gemini-subscription` 的 `google-gemini-cli/*` + `gemini-subscription/*` 共 10 个 key、name 各异，靠逐行 `key_prefix`，按 `(provider,id)` 去重会丢 5 个。
- **claude-code 借用链**：thinking 走 alias→anthropic，models.dev 数据借 anthropic（`_SUBSCRIPTION_BORROW`），fetcher 特殊——不是普通目录，借用关系要跟着搬。
- **逐字段保真**：`cost` 是嵌套对象、`input` 多模态、`headers`（copilot 依赖）、`compat`——瘦身脚本删字段前先确认该字段确实能从 overlay 层拿回来。
- **验证粒度**：多 wire provider 按每个 `(api, base_url, headers, compat)` 组合各 exec 一个模型，不是每 provider 一个。
- 核心回归测试保持绿：`tests/unit/test_provider_wire_invariants.py`、`tests/unit/test_model_fetch_routing.py`。
