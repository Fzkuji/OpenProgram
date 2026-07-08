# 模型目录与 Provider 配置（最终设计）

> 本文描述模型目录的**目标运行逻辑**：数据放哪、文件与代码怎么交互、后端和前端各自怎么消费。
> 与当前代码的差距和迁移路径集中在第 8 节——第 1–7 节永远只写目标态，不写历史。
> Thinking effort 的参数细节见 [thinking-effort.md](thinking-effort.md)。

## 1. 一句话架构

**系统只长期记住用户启用的模型。** 浏览「有哪些模型可选」是设置页的实时查询，不落盘；「启用」的动作 = 把该模型那一刻的完整规格写进 `config.json`。运行时注册表 `ENABLED_MODELS` 就是 config 里这几十行——`get_model()` 查的、聊天页显示的、用户勾选的，物理上是同一份数据。

**核心不变式：聊天页能选的 = 已启用的 = 后端能解析的。** 不是靠合并管线对齐两份清单，而是根本只有一份。

由此自动获得的性质：

- **没有大文件**：不存全量清单（models.dev 有 151 个 provider、上千个模型且大量重复），config 里只有用户启用的几个到几十个。
- **不会过期**：过期的前提是存储。可选列表实时查询，永远是最新的；已启用模型的规格由设置页「Refresh」按需覆写——只刷新用户真正在用的。
- **git 干净**：程序只写用户目录的 config；仓库里只有人写的 provider.json；安装包目录运行期只读。

## 2. 数据分布（按「谁写」分家）

| 谁写 | 放哪 | 是什么 | 大小 |
|---|---|---|---|
| **人**（进 git） | `providers/<p>/provider.json`（+ 专属协议时的 `<p>.py`） | endpoints、thinking、cache、模型级 override | 每份几行到几十行 |
| **程序**（用户机） | `config.json` → `providers.<p>.models` | 已启用模型的完整规格 + key、enabled 等用户状态 | 几十行 |
| 第三方（网络） | models.dev + 官方 `/v1/models` | 设置页浏览用的实时数据源 | 不落盘 |

```
openprogram/providers/                 ← 全部进 git，运行期只读
├── deepseek/
│   ├── provider.json                  ← 该 provider 全部手写配置（见第 3 节）
│   └── deepseek.py                    ← wire/stream 实现（仅专属协议的 provider 有）
├── enabled_models.py                  ← ENABLED_MODELS：从 config 加载 + endpoints 填充 + thinking 推导
└── models.py                          ← get_model / get_providers / get_models

~/.openprogram/
└── config.json                        ← 唯一的用户侧持久化
```

**命名规则：「catalog」一词整体退役**（历史上一词五用是命名混乱的根源）。`models_generated.py`、`thinking_catalog.py`、`_catalog_new.py`、webui `_model_catalog/`（→ `_model_listing/`）全部退役。**`ENABLED_MODELS` 这个名字成立的前提是它真的只装启用的模型**——语义先改，名字后改（见 8.2 迁移顺序）。

**没有目录的 provider**（fireworks、together 等）：models.dev 实时数据里有它们，用户填 key、浏览、启用即可，包里不需要任何文件。

## 3. provider.json：唯一的手写文件

一个 provider 的所有人工配置集中一份，全部字段可省略：

```json
{
  "id": "deepseek",
  "endpoints": {
    "default": {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}
  },
  "thinking": {
    "wire_format": "effort_string",
    "effort_map": {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "max": "max"},
    "default_effort": "medium"
  },
  "cache": {"mode": "none"},
  "model_overrides": {
    "some-model": {"headers": {"X-Foo": "1"}, "compat": {"no_stream_options": true}}
  },
  "models_from": null
}
```

| 字段 | 作用 | 缺省行为 |
|---|---|---|
| `endpoints` | api/base_url 分组，模型按组名引用（opencode 4 组、copilot 3 组；单 wire 只有 `default`） | models.dev 给的 base_url + OpenAI 兼容协议 |
| `thinking` | wire_format / effort 映射 / 模型级档位（原 thinking.json，详见 thinking-effort.md） | OpenAI 兼容 fallback（low/medium/high） |
| `cache` | prompt-caching 声明（原 cache.json） | 不做显式缓存控制 |
| `model_overrides` | 逐模型的 headers、compat、`endpoint` 引用、`key_prefix` 等机器拿不到的字段，**启用时叠进规格** | 无 override |
| `models_from` | 订阅型 provider 借用浏览数据源（claude-code → anthropic） | 不借用 |

**判断标准：机器拿得到的字段，人不写。** provider.json 里没有模型清单——清单是浏览时实时查的，规格是启用时复制的。

目录名用下划线（`amazon_bedrock/`），`id` 存连字符原名（`amazon-bedrock`）。同服务多协议（百炼的 OpenAI 兼容 + Anthropic 兼容端点）= 同一 provider 两个 endpoint，不拆两个 provider。

## 4. 两个动作：浏览、启用

### 4.1 浏览（实时，不落盘）

用户打开设置页某个 provider 的模型列表：

```
list_available_models(provider_id)
  = 官方 /v1/models（有 key 时；Anthropic 额外逐模型拉 capabilities，probe 推断 reasoning）
  ⊕ models.dev（补价格/能力；无 key 时的完整兜底）
  → 内存合并，直接返回给前端渲染
```

结果只进内存（可带一个短 TTL 缓存避免反复请求），关掉页面就没了。断网时浏览不可用——**发现新模型本来就需要网络**，这不是缺陷是事实。

### 4.2 启用（复制规格进 config）

用户在浏览列表里勾选一个模型：

```
enable_model(provider_id, row)
  → 规格 = 浏览行 ⊕ provider.json.model_overrides[id] ⊕ endpoints 解析的 api/base_url
  → thinking 档位由 provider.json.thinking 推导后一并写入
  → 追加到 config.json providers.<p>.models
  → ENABLED_MODELS 重载
```

- **取消启用** = 从 config 删除该行。
- **Refresh** = 对已启用模型重新执行浏览 + 覆写规格（治「规格随时间变旧」，且只刷新用户在用的）。
- **手工添加模型**（provider 没列出的）= 用户在同一张表单里手填一行——和「启用」写的是同一个列表，原 `custom_models` 概念消失。
- **订阅 provider 的动态注册**（如 claude-code 登录后自动出现 3 个模型）= 程序代替用户执行一次 enable，写的还是同一个列表。

## 5. 后端怎么用

```python
# openprogram/providers/enabled_models.py
ENABLED_MODELS: dict[str, Model]   # key = "<prefix>/<id>"，内容 = config 规格 + 推导字段
```

启动时从 config 加载（几十行，瞬时），config 变更后重载。`get_model` / `get_providers` / `get_models` 三个查询函数接口不变，20+ 个运行时调用方（agent、runtime、failover…）零改动。`get_model` miss 时经 `auth.aliases` 试等价 provider 名。

**约定：系统只认启用的模型。** failover 链、agent 配置引用的模型必须在启用集里；引用未启用的模型 = 配置错误，报错信息提示去设置页启用。旧会话引用已删除的模型时正常显示历史，仅不能继续用该模型发消息。

## 6. 前端怎么用

| 前端位置 | API 路由 | 数据来源 |
|---|---|---|
| 设置页 provider 列表 | `GET /api/providers` | provider.json 有的 + models.dev 实时列出的（社区 provider 可直接配置） |
| 设置页浏览/勾选模型 | `GET /api/providers/<id>/available` | **实时**：4.1 的浏览结果 + 已启用标记 |
| 聊天页模型选择器 | `GET /api/models/enabled` | **config**：ENABLED_MODELS 原样返回 |
| thinking 档位选择器 | （`_thinking.py`） | ENABLED_MODELS 行里的 thinking_levels |

webui 展示层（`_model_listing/`）不做任何合并推导——浏览合并在 4.1 一个函数里，规格合并发生在启用那一刻。webui import providers，providers 永远不 import webui。

**端到端**：填 key → 浏览（实时列表出现 `deepseek-v4-flash`）→ 勾选（完整规格写进 config，`ENABLED_MODELS["deepseek/deepseek-v4-flash"]` 出现）→ 聊天页选中发消息（`get_model` 命中同一条 config 记录）。任何时刻系统里都只有一份模型数据。

## 7. 不变式（改代码前先对照）

1. **只存启用的**：唯一持久化的模型数据是 config 里的启用规格。出现第二份持久化清单（全量快照、fetch 缓存文件、手写清单）即违约。
2. **浏览不落盘**：可选列表是实时查询 + 内存缓存，永不写文件。
3. **按谁写分家**：人写的进 git（provider.json）；程序写的进用户 config；包目录运行期只读。
4. **手写最小化**：provider.json 只存机器拿不到的字段，且没有模型清单。
5. **分层单向**：`openprogram.providers` 不 import `openprogram.webui`。
6. **key 兼容**：`"<prefix>/<id>"`、alias 回退、`key_prefix`（gemini-subscription 双 key）保留；注册表是同一个可变 dict。

## 8. 现状偏离与迁移

> 记录日期 2026-07-08。问题现象的完整描述见 [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md)。

### 8.1 偏离

1. **持久化了不该持久化的**：752 行手写 `models.json`（22 个 provider，已腐烂）+ `models.fetched.json`（Fetch 落盘在安装包目录，靠 .gitignore 遮掩）。目标态里两者都不存在。
2. **两条数据链**：设置页走 webui 的 `combined_models`（fetched + models.dev），运行时注册表只读手写 `models.json`——设置页能选 `deepseek-v4-flash`，`get_model` 查不到。目标态里连合并管线都不需要：只有 config 一份。
3. **每 provider 5 类文件**（provider.json / models.json / thinking.json / cache.json / models.fetched.json）。目标态收敛为 1 份 provider.json。
4. **probe 结果回写 git 里的 thinking.json**——程序写版本控制文件。目标态 probe 只影响浏览结果和启用时写入 config 的规格。
5. **层次倒置**：models.dev 源和合并逻辑在 `webui/_model_catalog/`，providers 层反过来读注册表补 api/base_url，循环依赖。
6. **注册表装了 755 个模型**，名字先后叫过 `MODELS` / `MODEL_REGISTRY`。目标态只装启用的，改名 `ENABLED_MODELS`——**名字跟着语义走，语义没改完之前不换名**。
7. **bailian 命名不标准**：models.dev 同 base_url 的 provider 叫 `alibaba-token-plan-cn`，已有预留空目录；用户已要求改标准名、删 `bailian/`。

### 8.2 迁移顺序（每步独立提交、系统不瘫）

1. **bailian → alibaba_token_plan_cn**：目录改名 + id 改 `alibaba-token-plan-cn`。独立小改，先做。
2. **启用即复制规格**：设置页勾选模型时把完整规格（浏览行 ⊕ override ⊕ endpoints ⊕ thinking 推导）写进 config `providers.<p>.models`，与 `custom_models` 统一为一个列表。存量用户的 `enabled_models` id 列表一次性迁移：按当前注册表把 id 解析成完整规格写入。
3. **运行时切换到 config**：注册表加载源改为「config 规格 + provider.json 填充」，`get_model` 语义不变。**两条链在这一步合一**——此后 752 行 `models.json` 和 `models.fetched.json` 机制成为死代码。
4. **浏览改实时**：`list_models_for_provider` 拆成「available（实时浏览）」和「enabled（读 config）」两条路；Fetch 落盘逻辑删除（可留短 TTL 内存缓存）。
5. **删除死数据**：`git rm` 22 份 `models.json`、fetched 文件机制、`.gitignore` 相关行。
6. **配置合一**：`thinking.json`、`cache.json` 并入 `provider.json`；`thinking_spec`/`cache_spec` 改读新位置；`_default_api_for`/`_resolve_base_url` 改读 endpoints（循环依赖解除）。
7. **命名收尾**：注册表改名 `ENABLED_MODELS`（此时语义已成立），定义移入 `enabled_models.py`；退役 `models_generated.py`、`thinking_catalog.py`、`_catalog_new.py`、`_model_catalog/`（→ `_model_listing/`）。至此代码里不再有「catalog」。

### 8.3 迁移必须保住的点（历史审查所得）

- **存量启用不能丢**：步骤 2 的 id → 规格迁移必须覆盖 22 个 provider 的现有 enabled_models 与 custom_models；迁移后逐条校验 `get_model` 结果与迁移前等价。
- **alias / 双 key**：gemini-subscription 的 `google-gemini-cli/*` + `gemini-subscription/*` 共 10 key、name 各异——启用行各自携带完整 key 与 name，天然保留；alias 回退逻辑不动。
- **claude-code 借用链**：浏览数据借 anthropic（`models_from`）、登录后自动 enable 3 个模型、fetcher 特殊——迁移时逐一保留。
- **逐字段保真**：`cost` 嵌套对象、`input` 多模态、`headers`（copilot 依赖）、`compat`——启用时写入 config 的规格必须含全这些字段；删除手写清单前先确认每个启用行拿得到等价值。
- **验证粒度**：多 wire provider 按每个 `(api, base_url, headers, compat)` 组合各 exec 一个模型。
- 核心回归测试保持绿：`tests/unit/test_provider_wire_invariants.py`、`tests/unit/test_model_fetch_routing.py`。
