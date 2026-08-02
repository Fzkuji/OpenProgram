# 模型清单与百炼 provider

本文描述模型清单机制中的一处结构性问题，以及百炼 provider 命名上的一处不一致。
目标状态与解析逻辑见 [`models/overview.md`](models/overview.md)。

## 一、涉及的概念

- **provider**：一个模型供应商，比如 OpenAI、DeepSeek、百炼。代码里每个 provider
  一个文件夹：`openprogram/providers/<名字>/`。
- **模型注册表**：运行时"目前启用的所有模型"的总清单。程序任何地方要用某个模型
  都来这里查（`get_model("deepseek", "xxx")`），被 20+ 个运行文件依赖
  （runtime、agent、failover 等）。
- **models.json**（每个 provider 文件夹里一份，进 git）：该 provider"启用了哪些
  模型"的规格清单。注册表就是把各 provider 的 `models.json` 拼起来的结果。
- **models.fetched.json**（每个 provider 文件夹里，不进 git）：用户在设置页点
  「Fetch Models」时，从 provider 官方 API 拉下来的模型列表缓存。只有 4 个
  provider 拉过。
- **models.dev**：第三方公开站点（`https://models.dev/api.json`），收录 151 个
  provider 的模型规格（context 长度、价格、能力），作为参考手册使用。

## 二、两份模型清单对不上

系统里有两条互不同步的模型数据链：

| | 链 A：注册表（代码运行用） | 链 B：设置页选模型用 |
|---|---|---|
| 数据来源 | 每个 provider 的 `models.json`（手写、进 git） | `models.fetched.json`（Fetch 缓存）+ models.dev |
| 谁用它 | 所有后端代码 `get_model()` | webui 设置页的模型选择器 |
| 实现位置 | `models_generated._load` → `_catalog_new.load_new_catalog` | `provider_models.combined_models` |

两条链的数据不一样。以 DeepSeek 为例：链 A 里是 `deepseek-chat`、
`deepseek-reasoner` 这两个旧型号；models.dev 里有 `deepseek-v4-flash`、
`deepseek-v4-pro`、`deepseek-reasoner`、`deepseek-chat` 共 4 个；链 B 的 Fetch
缓存里是 `deepseek-v4-flash`、`deepseek-v4-pro` 这两个新型号。

结果是用户在设置页选了 `deepseek-v4-flash`，后端 `get_model("deepseek",
"deepseek-v4-flash")` 却查不到它 —— 设置页能选，代码不认识。

根因在于喂给注册表的 `models.json` 是手写的，没有任何机制自动更新它；而
Fetch 与 models.dev 是活的、会更新，但它们的结果进不了注册表。`models_generated.py`
顶部注释写着原本的设计意图是"Fetch 直接改写这个文件，无需手维护"，但实现没有做到
—— Fetch 写的是 `models.fetched.json`，而注册表读的是 `models.json`，是两个文件。

[`models/overview.md`](models/overview.md) 描述的"models.dev 为主数据源 + 分层叠加"
只在链 B 实现了，链 A 没有接上。

## 三、百炼 provider 的命名

项目里这个 provider 叫 `bailian`（`providers/bailian/`，14 个模型，走 OpenAI
兼容格式）。models.dev 里同一个东西（同一个 base_url
`token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`）叫
`alibaba-token-plan-cn`，收录 18 个模型。项目里另有一个空文件夹
`providers/alibaba_token_plan_cn/`，是给这个 provider 预留的位置。

统一到 models.dev 的标准名 `alibaba-token-plan-cn` 是与命名规则一致的方向；
这与第二节的清单机制问题是两件独立的事，可以分开处理。

## 四、目标状态

[`models/overview.md`](models/overview.md) 定下的方向是：每个 provider 自包含
（配置都在 `providers/<p>/` 下）；models.dev 作为主数据源提供模型列表、价格与
能力；`thinking` 声明补充思考档位；注册表 schema 的必填字段只有
`id/name/api/provider/base_url`，其中 `api` 与 `base_url` 来自 `provider.json`。

按该设计，模型清单不再手工维护，两条数据链合一，链 A 与链 B 的分歧随之消失。
