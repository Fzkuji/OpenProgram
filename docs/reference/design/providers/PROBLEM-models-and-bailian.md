# 当前问题：模型清单（MODELS）与百炼 provider

> 这份文档只描述**现状和问题**，不含解决方案。给接手的人看，不需要本对话上下文。
> 记录日期：2026-07-08

---

## 一、名词（用大白话）

- **provider**：一个模型供应商，比如 OpenAI、DeepSeek、百炼。代码里每个 provider 一个文件夹：`openprogram/providers/<名字>/`。
- **MODELS**：代码运行时手里的「我们目前启用的所有模型」的总清单。程序任何地方要用某个模型，都来这里查（`get_model("deepseek", "xxx")`）。被 20+ 个运行文件依赖（runtime、agent、failover 等）。
  - ⚠️ **这个名字 `MODELS` 太泛，看不出功能，用户要求改名**（例如 `ENABLED_MODELS` / 「启用模型清单」之类）。定义在 `openprogram/providers/models_generated.py`。
- **models.json**（每个 provider 文件夹里一份，进 git）：这个 provider「启用了哪些模型」的规格清单。`MODELS` 就是把 22 个 provider 的 `models.json` 拼起来的。
  - 历史命名混乱：这个文件一度叫 `catalog.json`，已按设计文档改回 `models.json`（commit `20a76b54`）。
- **models.fetched.json**（每个 provider 文件夹里，**不进 git**）：用户在设置页点「Fetch Models」时，从 provider 官方 API 拉下来的模型列表缓存。只有 4 个 provider 拉过。
- **models.dev**：一个第三方公开网站（`https://models.dev/api.json`），收录全世界 151 个 provider 的模型规格（context 长度、价格、能力）。是个「参考手册」。

---

## 二、核心问题：两份模型清单对不上

系统里实际有**两条独立的模型数据链**，互不同步：

| | 链 A：MODELS（代码运行用） | 链 B：设置页选模型用 |
|---|---|---|
| 数据来源 | 每个 provider 的 `models.json`（手写、进 git） | `models.fetched.json`（Fetch 缓存）+ models.dev |
| 谁用它 | 所有后端代码 `get_model()` | webui 设置页的模型选择器 |
| 实现位置 | `models_generated._load` → `_catalog_new.load_new_catalog` | `provider_models.combined_models` |

**它们的数据不一样。实测 DeepSeek：**

- 链 A（MODELS / 代码用）：`deepseek-chat`、`deepseek-reasoner` — **旧型号**
- models.dev：`deepseek-v4-flash`、`deepseek-v4-pro`、`deepseek-reasoner`、`deepseek-chat` — 4 个
- 链 B（Fetch 缓存 / 设置页用）：`deepseek-v4-flash`、`deepseek-v4-pro` — **新型号**

**后果**：用户在设置页选了个新型号（`deepseek-v4-flash`），但后端代码 `get_model("deepseek","deepseek-v4-flash")` 查不到它 —— 因为 MODELS 里根本没有。**设置页能选、代码不认识。**

**根因**：`models.json`（喂给 MODELS）是手写死的，没有任何机制自动更新它；而 Fetch/models.dev 是活的、会更新，但它们的结果进不了 MODELS。

**注**：`models_generated.py` 顶部注释写着原设计意图是「Fetch 直接改写这个文件，无需手维护」，但当前实现没做到 —— Fetch 写的是 `models.fetched.json`，和 MODELS 读的 `models.json` 是两个文件。设计文档 `docs/design/providers/models/models.md` 描述的「models.dev 为主数据源 + 分层叠加」只在链 B 实现了，链 A（MODELS）没接上。

---

## 三、百炼（bailian）provider 的具体问题

1. **命名可能不标准。** 本项目里这个 provider 叫 `bailian`（本对话中手工创建：`providers/bailian/`，14 个模型，走 OpenAI 兼容格式）。但 models.dev 里同一个东西（同一个 base_url `token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`）叫 **`alibaba-token-plan-cn`**，收录 18 个模型。
   - 项目里**早就有一个空文件夹** `providers/alibaba_token_plan_cn/`，疑似本来就是给这个 provider 预留的位置。
   - 用户原话诉求：「用他们定义好的 alibaba 那个，删除我们自己创建的 bailian」。

2. **待澄清的点**（对话未收敛）：用户到底要的是
   - (a) 只是把 provider 从 `bailian` 改名成标准的 `alibaba-token-plan-cn`（模型内容照旧）；还是
   - (b) 借百炼这个例子，解决第二节那个「MODELS 手写、不自动」的大问题（让模型清单不再手工维护）。
   - 这两个诉求在对话里缠在一起没分开，助手反复没理解对，用户已让「找别人」。

---

## 四、设计文档说的目标状态（供参考）

`docs/design/providers/models/models.md`（最终设计已重写，含目标运行逻辑 + 迁移路径）：
- 第 1 节：每个 provider 自包含（配置都在 `providers/<p>/` 下）。
- 第 6 节「三层叠加」、第 7 节「models.dev 的角色」：models.dev 是主数据源，提供模型列表 + 价格 + 能力；`thinking.json` 补充思考档位（这套已有 `thinking_catalog` 自动推导）；MODELS schema 必填字段只有 `id/name/api/provider/base_url`，其中 `api`+`base_url` 来自 `provider.json`。
- 第 9 节自己承认：「当前代码并非如此，MODELS 只从静态文件加载」，即上面第二节的裂缝。

**也就是说：设计文档已经定了方向（models.dev 为主 + 单独文件补它给不了的字段），但这个方向只在链 B 落地，链 A（MODELS）没做。**

---

## 五、已完成 / 未完成

**已完成（已推送到 origin/main）：**
- 大重构：模型目录从中央 `_catalog/` 迁移到每个 provider 自包含的 `providers/<p>/{provider.json, models.json}`（9 提交）。
- 文件命名对齐设计文档：git 源 = `models.json`，Fetch 缓存 = `models.fetched.json`（commit `20a76b54`）。
- 当前 `MODELS` = 755 个模型（752 来自 22 个 provider 的 models.json + 3 个 claude-code 动态注册）。

**未完成 / 卡住：**
- ~~`MODELS` 改名~~ 已完成：2026-07-08 改为 `MODEL_REGISTRY`（不用 ENABLED_MODELS，因与 config 的 `enabled_models`「用户启用子集」语义冲突）。
- 两条数据链合一 / MODELS 自动化（第二节的核心问题，方向有争议，未做）。
- 百炼 → alibaba-token-plan-cn（第三节，诉求未最终确认，未做）。
