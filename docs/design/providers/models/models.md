# 模型目录与 Provider 配置（最终设计）

> 本文描述模型目录的**目标运行逻辑**：文件结构、文件与代码的交互、后端与前端各自怎么消费。
> 与当前代码的差距和迁移路径集中在第 8 节——第 1–7 节永远只写目标态，不写历史。
> Thinking effort 的参数细节见 [thinking-effort.md](thinking-effort.md)。

## 1. 设计原则：按「谁写」分家

模型数据只有三个作者，每个作者一个地盘，互不越界：

| 谁写 | 放哪 | 是什么 |
|---|---|---|
| **人**（进 git） | `providers/<p>/provider.json`（+ 专属协议时的 `<p>.py`） | 该 provider 的全部手写配置：endpoints、thinking、cache、少量模型 override |
| **机器**（进 git） | `providers/models_dev_snapshot.json`（一份，全局） | models.dev 快照，脚本刷新——出厂默认 + 离线兜底 |
| **程序**（用户机） | `~/.openprogram/fetched/<p>.json` | Fetch + probe 结果。**包目录零写入** |
| 第三方（网络） | models.dev live（内存缓存，TTL 24h） | 可选刷新，断网无影响 |

启动时一次合并 → 运行时注册表 `MODEL_REGISTRY` → 后端 `get_model()` 和前端所有页面读同一份结果。

**核心不变式：前端能选到的模型，后端一定能解析**——因为根本没有第二份数据。

由此自动获得的性质：

- **手写数据不会腐烂**：人只写机器拿不到的东西（endpoints、thinking 映射、headers 这类 override）。模型清单、价格、context 全部来自快照/Fetch，机器自己更新自己。
- **git 干净**：程序只写用户目录，永远不碰仓库和安装包（pip 装的包目录可能只读）。probe 结果同理——写进 `fetched/<p>.json`，不回写 git。
- **离线可跑**：快照进 git 随包分发，断网装机也有全量出厂模型；fetched 是本地文件；models.dev live 只是锦上添花。

## 2. 文件结构

```
openprogram/providers/                     ← 全部进 git，运行期只读
├── models_dev_snapshot.json               ← 机器生成：models.dev 快照（唯一的「全量清单」）
├── deepseek/
│   ├── provider.json                      ← 该 provider 全部手写配置（见第 3 节）
│   └── deepseek.py                        ← wire/stream 实现（仅专属协议的 provider 有）
├── model_registry/                        ← 合并管线，定义 MODEL_REGISTRY
│   ├── loader.py                          ← 读快照 + provider.json + fetched
│   ├── models_dev.py                      ← live 源 + 快照刷新脚本
│   └── merge.py                           ← 合并 + thinking 推导
└── models.py                              ← get_model / get_providers / get_models

~/.openprogram/                            ← 用户机器状态，程序写
├── config.json                            ← enabled / enabled_models / keys / custom_models
└── fetched/
    └── deepseek.json                      ← Fetch 拉的官方列表 + probe 探测结果
```

**命名规则：「catalog」一词整体退役**（历史上一词五用是命名混乱的根源）。合并管线叫 `model_registry`（产物就是 `MODEL_REGISTRY`），webui 展示层叫 `_model_listing/`。`models_generated.py`、`thinking_catalog.py`、`_catalog_new.py` 全部退役。

**没有目录的 provider**（fireworks、together 等社区 provider）：快照里有它们的模型和 base_url，用户填 key 即用；Fetch 结果写用户目录，**不需要在包里建任何目录**。加一个内置 provider = 写一份 `provider.json`，仅当它需要 override 时才需要。

## 3. provider.json：唯一的手写文件

一个 provider 的所有人工配置集中在一份文件，全部字段可省略：

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
  "models": [
    {"id": "some-model-not-on-models-dev", "context_window": 128000}
  ],
  "models_from": null
}
```

| 字段 | 作用 | 缺省行为 |
|---|---|---|
| `endpoints` | api/base_url 分组，模型按组名引用（多 wire provider 如 opencode 有 4 组、copilot 3 组；单 wire 只有 `default`） | 用快照里 models.dev 的 base_url + OpenAI 兼容协议 |
| `thinking` | wire_format / effort 映射 / model_overrides（原 thinking.json，详见 thinking-effort.md） | OpenAI 兼容 fallback（low/medium/high） |
| `cache` | prompt-caching 声明（原 cache.json） | 不做显式缓存控制 |
| `models` | 模型 override 或补录：headers、compat、`key_prefix`（双 key）、`endpoint` 引用，以及快照没收录的模型全规格 | 模型清单完全来自快照/Fetch |
| `models_from` | 订阅型 provider 借用数据源（如 claude-code → anthropic） | 不借用 |

**判断标准：机器拿得到的字段，人不写。** 写了可自动获得的字段（价格、context、清单）即违反设计。多数 provider 的 provider.json 只有几行 endpoints，甚至整个文件不存在。

目录名用下划线（`amazon_bedrock/`），`id` 存连字符原名（`amazon-bedrock`）——注册表 key 用连字符。同服务多协议（百炼的 OpenAI 兼容 + Anthropic 兼容端点）= 同一 provider 两个 endpoint，不拆两个 provider。

## 4. 合并（唯一数据出口）

```
MODEL_REGISTRY = 快照 ∪ live models.dev ∪ fetched ∪ provider.json.models
                 （右边的覆盖左边的同名字段；模型存在性以 fetched 为权威——Fetch 过的 provider，
                   不在 fetched 里的行对 UI 隐藏但保留在注册表里，旧会话不断链）
                 + api/base_url 按模型的 endpoint 名从 endpoints 取
                 + thinking 字段由 provider.json.thinking 推导（永远推导，不存储）
```

优先级的道理：手写 = 人的显式 override，最大；fetched = 官方 API 的事实，次之；live 比快照新。四个来源里**三个是机器维护的**，人只负责最小的那份。

输出 `Model` 对象，key = `"<prefix>/<id>"`，prefix 默认取 `id`，逐行可用 `key_prefix` 覆盖（gemini-subscription 双 key 场景）。

```python
# openprogram/providers/model_registry/__init__.py
MODEL_REGISTRY: dict[str, Model]   # 唯一的运行时模型注册表
```

命名说明：不叫 `ENABLED_MODELS`——注册表装的是**全部已知模型**，「启用」是 `config.json` 里的用户状态（一串指向注册表的 id 书签），两个概念不能共用一个名字。

## 5. 后端怎么用

```python
get_model("deepseek", "deepseek-v4-flash")  # → Model | None，带 alias 回退
get_providers()                              # → 有模型的 provider id 列表
get_models("deepseek")                       # → 该 provider 的全部 Model
```

20+ 个运行时调用方（agent、runtime、failover…）只认这三个函数。`get_model` miss 时经 `auth.aliases` 试等价 provider 名。

**Fetch 写路径**：设置页点「Fetch Models」→ 调官方 API（Anthropic 额外逐模型拉 capabilities）→ probe 推断 reasoning →「模型列表 + probe 结果」原子写入 `~/.openprogram/fetched/<p>.json` → 注册表重载 → 前端刷新。**新模型进设置页的同一时刻，`get_model` 就能解析。**

**快照刷新**：维护者跑 `model_registry/models_dev.py` 的刷新脚本，重新生成 `models_dev_snapshot.json`，走正常 git 提交——这是唯一「机器写、进 git」的文件，且只在仓库侧发生，用户机器上它只读。

**自定义模型**：config 的 `custom_models` 经注册函数原地写入 `MODEL_REGISTRY`（注册表是同一个可变 dict）；api/base_url 缺省从 endpoints 取。

## 6. 前端怎么用

前端没有自己的模型数据，三个 listing 函数（`webui/_model_listing/`，纯展示层）读同一份注册表：

| 前端位置 | API 路由 | listing 函数 | 内容 |
|---|---|---|---|
| 设置页 provider 列表 | `GET /api/providers` | `list_providers()` | 注册表全部 provider（含快照带来的社区 provider） |
| 设置页模型表 | `GET /api/providers/<id>` | `list_models_for_provider()` | 该 provider 的注册表条目 + enabled 标记 |
| 聊天页模型选择器 | `GET /api/models/enabled` | `list_enabled_models()` | 委托上一行，按 config 过滤 |
| thinking 档位选择器 | （`_thinking.py`） | 委托 `list_models_for_provider` | 同一行数据里的 thinking_levels |

enabled 状态只存 `config.json`；勾选/取消只改 config。listing 不做任何合并和推导——那些在 `model_registry` 里做完了。webui import providers，providers 永远不 import webui。

**端到端**：启用 deepseek → 点 Fetch（`fetched/deepseek.json` 写入，注册表出现 `deepseek/deepseek-v4-flash`）→ 勾选（config 记 id）→ 聊天页选中发消息（`get_model` 命中同一条注册表记录，endpoints 给 base_url，thinking 推导给档位）。任何一步都不存在第二份清单。

## 7. 不变式（改代码前先对照）

1. **单一出口**：前端显示和运行时解析出自同一个合并结果。出现第二条数据链即违约。
2. **按谁写分家**：人写的进 git；程序写的进用户目录；包目录运行期只读。程序写 git 文件或包目录即违约。
3. **手写最小化**：provider.json 只存机器拿不到的字段。
4. **分层单向**：`openprogram.providers` 不 import `openprogram.webui`。
5. **离线可跑**：快照 + fetched 足够运行，models.dev live 只是增强。
6. **key 兼容**：`"<prefix>/<id>"`、alias 回退、`key_prefix` 双 key（gemini-subscription 10 个 key）保留；注册表是同一个可变 dict。

## 8. 现状偏离与迁移

> 记录日期 2026-07-08。问题现象的完整描述见 [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md)。

### 8.1 偏离

1. **两条数据链**：合并逻辑只在 webui 实现（`provider_models.combined_models` 喂设置页），运行时注册表只读 git 里手写的 `models.json`——设置页能选 `deepseek-v4-flash`，`get_model` 查不到。
2. **752 行手写模型清单**（22 个 provider 的 `models.json`），与 models.dev 大面积重复，无更新机制，已腐烂。目标态里被快照替代，只剩 override。
3. **每 provider 5 类文件**（provider.json / models.json / thinking.json / cache.json / models.fetched.json），按内容分家而非按作者分家。目标态收敛为 1 份 provider.json。
4. **程序写错地方**：Fetch 写安装包目录（靠 .gitignore 遮掩）；probe 结果回写 git 里的 thinking.json。目标态全部改写 `~/.openprogram/fetched/`。
5. **层次倒置**：models.dev 源和合并逻辑在 `webui/_model_catalog/`，providers 层反过来读注册表补 api/base_url，构成循环依赖。
6. ~~`MODELS` 名字太泛~~ 已改名 `MODEL_REGISTRY`（2026-07-08，20 个文件）。定义暂留 `models_generated.py`。
7. **bailian 命名不标准**：models.dev 同 base_url 的 provider 叫 `alibaba-token-plan-cn`，已有预留空目录；用户已要求改用标准名、删 `bailian/`。

### 8.2 迁移顺序（每步独立提交、系统不瘫）

1. **bailian → alibaba_token_plan_cn**：目录改名 + id 改 `alibaba-token-plan-cn`，模型照搬。独立小改，先做。
2. **快照落地**：写刷新脚本，拉 models.dev 生成 `models_dev_snapshot.json` 进 git。
3. **fetched 挪到用户目录**：`provider_models` 读写路径改 `~/.openprogram/fetched/<p>.json`；迁移包内现存 4 份 fetch 结果；删 `.gitignore` 对应行。probe 回写从 thinking.json 改到 fetched 文件。
4. **配置合一**：`thinking.json`、`cache.json`、`models.json`（瘦身后的 override 部分）并入 `provider.json`；`thinking_spec`/`cache_spec` 改读新位置。迁移脚本逐 provider 校验等价。
5. **合并管线下沉并接管**：合并逻辑移入 `providers/model_registry/`，`_load()` 跑完整合并（第 4 节），fetched 与快照进注册表。**两条链在这一步合一。**`_default_api_for`/`_resolve_base_url` 改读 endpoints，循环依赖解除。
6. **listing 改薄**：删掉 webui 自己那套合并，直接读注册表；包改名 `_model_catalog/` → `_model_listing/`。
7. **命名收尾**：`MODEL_REGISTRY` 定义移入 `model_registry/__init__.py`；退役 `models_generated.py`、`thinking_catalog.py`、`_catalog_new.py`。至此代码里不再有「catalog」。

### 8.3 迁移必须保住的点（历史审查所得）

- **alias / 双 key**：gemini-subscription 的 `google-gemini-cli/*` + `gemini-subscription/*` 共 10 key、name 各异，靠逐行 `key_prefix`；按 `(provider,id)` 去重会丢 5 个。
- **claude-code 借用链**：thinking alias→anthropic、models.dev 数据借 anthropic、fetcher 特殊——在 provider.json 用 `models_from` 显式声明，借用关系不能丢。
- **逐字段保真**：`cost` 嵌套对象、`input` 多模态、`headers`（copilot 依赖）、`compat`——删任何手写字段前，先确认合并层确实能拿回等价值。
- **验证粒度**：多 wire provider 按每个 `(api, base_url, headers, compat)` 组合各 exec 一个模型。
- 核心回归测试保持绿：`tests/unit/test_provider_wire_invariants.py`、`tests/unit/test_model_fetch_routing.py`。
