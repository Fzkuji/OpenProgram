# Enabled-Models 迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。每任务 checkbox 跟踪。

**Goal:** 落地 `docs/design/providers/models/models.md` 的最终设计：系统只持久化用户启用的模型（完整规格存 `config.json` 的 `providers.<p>.models`），可选模型列表改为设置页实时查询（不落盘），删除 752 行手写 `models.json` 与 `models.fetched.json` 持久化机制，配置合一到 `provider.json`，最终注册表改名 `ENABLED_MODELS`。

**Architecture:** 设计文档 = `docs/design/providers/models/models.md`（目标态第 1–7 节，迁移即本计划）。核心：`get_model()` 查的、聊天页显示的、用户勾选的必须是物理上同一份数据（config 里的启用规格）。「启用」动作 = 浏览行 ⊕ provider.json 的 model_overrides ⊕ endpoints 解析的 api/base_url ⊕ thinking 推导 → 完整规格写入 config。

**Tech Stack:** Python 3, pydantic (Model), pytest（`python3 -m pytest`；若有 `.venv-test/bin/python` 优先用它）。前端 web/ 尽量零改动（保持 API 响应形状）。

## Global Constraints

- **接口不变**：`get_model` / `get_providers` / `get_models`（`openprogram/providers/models.py`）签名与返回类型不变；注册表 key 格式 `"<prefix>/<id>"` 不变；注册表必须保持**同一个可变 dict**（`_register_custom_model_in_registry` 与 `_claude_code_registry` 原地写入依赖此）。
- **alias 不变**：`get_model` 经 `openprogram/auth/aliases.py` 的回退逻辑保留。
- **分层单向**：`openprogram.providers` 不得 import `openprogram.webui`。providers 层需要读 config 时，使用（或建立）providers 层/公共层的 config 读取函数，不得从 webui 拿。
- **API 响应形状不变**：`GET /api/providers`、`GET /api/providers/<name>`（`routes/providers.py:155`）、`GET /api/models/enabled`（`routes/providers.py:191`）返回的 JSON 字段保持现状，前端 `web/` 零改动。
- **逐字段保真**：Model 行的 `cost`（嵌套 `{input,output,cache_read,cache_write}`）、`input`（多模态列表）、`headers`（github-copilot 依赖）、`compat`、`thinking_levels/default_thinking_level/thinking_variant` 在「启用规格」中必须齐全。
- **双 key 保留**：gemini-subscription 的 10 个 key（5 个 `google-gemini-cli/*` + 5 个 `gemini-subscription/*`，name 各异）不能丢；启用行各自携带完整 key 与 name。
- **claude-code 特殊路径保留**：`_claude_code_registry.py:36` 登录后动态写 3 个模型进注册表；thinking alias→anthropic；浏览数据借 anthropic（`_SUBSCRIPTION_BORROW`）。
- **回归测试**：`tests/unit/test_provider_wire_invariants.py`、`tests/unit/test_model_fetch_routing.py` 保持绿。当注册表语义改为「仅启用」导致测试前提失效时，**允许改写测试为 fixture 驱动**（自造含多 wire 组合的启用配置），但必须保留原断言意图（api/base_url/headers/compat 的 wire 不变式），不许删除或空壳化。
- 每任务 TDD：失败测试 → 红 → 最小实现 → 绿 → 提交。提交尾部：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 已知的既有失败（非回归，可忽略）：`tests/unit/test_context_route.py::test_context_endpoint_no_tools`。

## 文件地图（现状）

- 注册表：`openprogram/providers/models_generated.py`（`MODEL_REGISTRY`，由 `_catalog_new.load_new_catalog` 从各 `providers/<p>/models.json` 加载，755 = 752 + 3 动态 claude-code）
- 查询：`openprogram/providers/models.py`
- 浏览/合并（webui 侧）：`openprogram/webui/_model_catalog/`——`listing.py`（`list_providers` / `list_models_for_provider` / `list_enabled_models`）、`provider_models.py`（`save_fetched`/`load_fetched`/`combined_models`，落盘 `providers/<p>/models.fetched.json`）、`sources/models_dev.py`、`fetchers/`（`fetch_models_remote`）、`storage.py`（`_read_providers_cfg` 等 config 读写）、`providers.py`（`_default_api_for`/`_resolve_base_url`）
- 路由：`openprogram/webui/routes/providers.py`
- thinking：`providers/thinking_spec.py`（读 `providers/<p>/thinking.json`）、`providers/thinking_catalog.py`（`derive_thinking_fields`）
- cache：`providers/cache_spec.py`（读 `providers/<p>/cache.json`）
- 动态注册：`webui/_runtime_management.py`（`_register_custom_model_in_registry`）、`providers/anthropic/_claude_code_registry.py`
- provider 元数据：`providers/<p>/provider.json`（endpoints）、`providers/_provider_meta.py`

---

### Task 1: bailian → alibaba_token_plan_cn

`providers/bailian/{provider.json,models.json}` 移入已存在的空目录 `providers/alibaba_token_plan_cn/`，`provider.json` 的 `id` 改为 `"alibaba-token-plan-cn"`，模型行原样保留。删除 `providers/bailian/`。在 `openprogram/auth/aliases.py` 增加 alias `bailian → alibaba-token-plan-cn`（老 config 里的 key/enabled 引用不断链）。全仓 grep `bailian`，凡指向该 provider 的代码引用（label、setup hint 等）改为新名。

- [ ] 测试：`get_model("alibaba-token-plan-cn", <某模型id>)` 命中；`get_model("bailian", <同id>)` 经 alias 命中；注册表总数不变。
- [ ] 回归测试绿；提交。

### Task 2: 启用即复制规格（写路径 + 存量迁移）

**新写路径**：设置页勾选/取消模型的后端入口（`routes/providers.py` 中更新 `enabled_models` 的路由，顺着 `storage.py` 的 config 写函数找）在勾选时构造**完整规格行**写入 config `providers.<p>.models`（list[dict]），取消时删除该行。规格行 = 当前 `list_models_for_provider(p)` 输出的该模型完整字段（含 api/base_url/cost/input/context_window/max_tokens/headers/compat/thinking_levels/default_thinking_level/thinking_variant/name/id，若有 `key_prefix` 一并带上）。**过渡期双写**：`enabled_models`（旧 id 列表）继续同步维护，本任务不改任何读路径。

**存量迁移**：config 加载时（storage 层）一次性迁移——对每个 provider，若有 `enabled_models` id 而 `providers.<p>.models` 中无对应行，用当前注册表/listing 解析成完整规格补写；解析不到的 id 保留在 `enabled_models` 并记 warning，不丢弃。`custom_models` 里的行并入 `providers.<p>.models`（带标记字段 `"source": "manual"`），原 `custom_models` 键保留不动（读路径未切）。

- [ ] 测试：勾选→config 出现完整规格行（逐字段断言含嵌套 cost）；取消→行消失；存量 enabled_models 迁移后规格等价于注册表条目；解析不到的 id 不丢。
- [ ] 回归测试绿；提交。

### Task 3: 运行时注册表切换到 config

`models_generated._load()` 改为：读 config 各 provider 的 `models` 规格行（用 providers 层自己的 config 读取，禁止 import webui）→ 每行经 `provider.json` endpoints 补缺省 api/base_url（行内已有值优先）→ 构造 `Model` → key `"<key_prefix or provider>/<id>"`。不再读 `providers/<p>/models.json`。`_register_custom_model_in_registry` 与 claude-code 动态注册路径不变（原地写同一 dict）。

**语义变化**：注册表从「全量 755」变「仅启用」。约定：引用未启用模型 = 配置错误；`get_model` 返回 None 的现有错误路径不变。fresh 安装（空 config）注册表为空是合法状态。

**测试改写**：`test_provider_wire_invariants.py` / `test_model_fetch_routing.py` 若依赖全量注册表，改为 fixture 驱动：临时 config 注入覆盖多 wire 组合的启用行（openai-completions/anthropic-messages/google-generative-ai/openai-responses，含 headers、compat、key_prefix 双 key 案例），断言意图不变。

- [ ] 测试：fixture config → 注册表内容与 key 正确；空 config → 空注册表不崩；claude-code 动态 3 模型仍可注册；`get_model` alias 回退仍工作。
- [ ] 全量单测（排除既有失败）绿；提交。

### Task 4: 浏览改实时，读路径切换

- `list_models_for_provider(p)`（喂 `GET /api/providers/<name>`）改为「实时浏览 ⊕ 已启用标记」：浏览 = 现有 fetcher 逻辑（`fetch_models_remote` 的取数部分，有 key 时）⊕ `models.dev`（无 key 兜底与字段补充），**内存合并，不再落盘**；短 TTL（如 10 分钟）内存缓存可选。已启用标记来自 config `providers.<p>.models`。响应字段形状与现状一致。
- `list_enabled_models()`（喂 `GET /api/models/enabled`）改为直接读注册表（现在 = config 启用规格），不再经 `list_models_for_provider`。
- `save_fetched`/`load_fetched` 的磁盘落盘调用移除（函数留给 Task 5 删）；「Fetch Models」按钮语义 = 强制刷新浏览缓存 + 对已启用模型覆写 config 规格行（= 设计文档的 Refresh）。
- `_thinking.py` 的 thinking 选项路径跟随（读启用行的 thinking_levels）。

- [ ] 测试：无 key provider 浏览返回 models.dev 数据；启用标记正确；`/api/models/enabled` 返回 = config 启用行；Fetch 后已启用行的规格被更新；无网络时浏览优雅降级（空列表+错误信息，不崩）。
- [ ] 回归测试绿；提交。

### Task 5: 删除死数据与死代码

`git rm` 22 个 `providers/<p>/models.json`；删除磁盘上的 `providers/<p>/models.fetched.json`（4 份，gitignored）；删 `.gitignore` 对应行；删 `provider_models.py` 的落盘读写函数与 `_catalog_new.py` 中不再被调用的部分；删 config 中的过渡双写（`enabled_models` id 列表停止维护但读侧兼容保留一版）与 `custom_models` 旧读路径。全仓 grep 确认无引用残留。

- [ ] 全量单测绿；注册表行为与 Task 4 后一致（前后各跑一次冒烟：启用→聊天可解析）。提交。

### Task 6: 配置合一到 provider.json

`providers/<p>/thinking.json` 内容并入 `provider.json` 的 `"thinking"` 键；`providers/<p>/cache.json` 并入 `"cache"` 键；`thinking_spec.py` / `cache_spec.py` 改读新位置（保留对旧文件的回退读一版，打 DeprecationWarning）。`webui/_model_catalog/providers.py` 的 `_default_api_for`/`_resolve_base_url` 改读 `provider.json` endpoints（经 `_provider_meta.py`），删除对注册表的反向读取（providers→webui 循环解除）。迁移脚本一次性搬 22 个 provider 的文件并逐 provider 校验 `get_thinking_spec`/`load_cache_spec` 输出等价，然后 `git rm` 旧文件。

- [ ] 测试：迁移前后 thinking/cache spec 逐 provider 等价（含 anthropic model_overrides、github-copilot wire_format none、google budget_tokens）；循环 import 检查（providers 包 import 图中无 webui）。
- [ ] 全量单测绿；提交。

### Task 7: 命名收尾

- 注册表改名 `MODEL_REGISTRY` → `ENABLED_MODELS`，定义移入新文件 `openprogram/providers/enabled_models.py`；`models_generated.py` 退役（留一行 re-export + DeprecationWarning 一版，或直接删——若全仓无外部引用则直接删）。
- `thinking_catalog.py` 的 `derive_thinking_fields` 并入 `thinking_spec.py`，`thinking_catalog.py` 删除。
- `_catalog_new.py` 删除（Task 3 后应已无引用）。
- `webui/_model_catalog/` 目录改名 `webui/_model_listing/`，包内 import 全量更新。
- 全仓 grep：代码中不再出现 `catalog`（允许出现在 docs/ 历史文档与本计划中）。

- [ ] 全量单测绿；`rg -i catalog openprogram/ web/ --type py` 零命中；提交。

---

## 验证（最终审查前）

- 端到端冒烟：启动 webui →（模拟）启用一个模型 → config 出现规格行 → `get_model` 命中 → 构造请求参数含正确 api/base_url/headers。
- 多 wire 组合各 exec 一次构造（fixture 层面）：openai-completions / anthropic-messages / google-generative-ai / openai-responses。
- 设计文档 `docs/design/providers/models/models.md` 第 8.1/8.2 节更新为已完成状态。
