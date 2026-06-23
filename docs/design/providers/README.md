# Providers

LLM provider 层的设计文档。providers 把框架内部的统一上下文(`Context`:system / messages / tools)翻译成各家 API 的请求,处理认证、缓存、错误与模型目录。

文档按职责分四组(三个已有子目录 + 一个待补的核心组):

## 翻译 + 缓存(核心,部分待补)

provider 无关的统一格式如何翻译成各家 wire 格式,以及 prompt 缓存如何按 provider 落地。**这是 providers 层的核心机制,目前散在代码里(`types.py` 的 `Context` + `stream.py` 分发 + 各 provider 的 `_build_*`),尚无总设计文档。**

- 已实现但仅有计划稿:[`cache-control-passthrough`](../../plans/cache-control-passthrough.md)(在 `docs/plans/`)—— Anthropic `cache_control` 逐块透传。
- 上下文如何分层组装(L0/L1/L2)见 [`context/context-composition.md`](../context/context-composition.md);providers 侧的翻译/缓存落地是它的下游接口。
- **待写**:统一格式 → 各家翻译的总设计、缓存能力声明层(cache_spec,类比 `models/` 的 thinking 声明)、Gemini out-of-band 缓存处理。

## [auth/](auth/) — 凭证 · 认证 · 账号

API key 与订阅 OAuth 的解析、校验、存储,以及多账号池与轮换。

- [`credential-validation-unification`](auth/credential-validation-unification.md) — 统一的凭证校验入口
- [`credential-status-redesign`](auth/credential-status-redesign.md) — 凭证状态("可用 / 停用",去掉 COOLING)
- [`api-key-resolution-unification`](auth/api-key-resolution-unification.md) — API key 解析链统一
- [`unified-auth-storage`](auth/unified-auth-storage.md) — 自包含的认证存储
- [`unified-account-management`](auth/unified-account-management.md) — 多账号管理 + 池轮换/回退
- [`claude-code-direct-oauth`](auth/claude-code-direct-oauth.md) — claude-code 订阅 OAuth 直连(砍 Meridian)

## [reliability/](reliability/) — 容错 · 错误 · 重试 · 超时

模型调用失败时的分类、重试、超时与错误向上传播。

- [`llm-fault-tolerance`](reliability/llm-fault-tolerance.md) — 容错与超时总设计
- [`error-retry`](reliability/error-retry.md) — 错误处理与重试决策
- [`error-taxonomy-propagation`](reliability/error-taxonomy-propagation.md) — 结构化错误一路传到 UI
- [`error-and-timeout-mechanism.html`](reliability/error-and-timeout-mechanism.html) — 错误/超时机制可视化

## [models/](models/) — 模型目录 · 能力

模型清单的数据布局、配置结构,以及 thinking/effort 等能力的声明式映射。每个模型都绑定在它所属的 provider 下,所以归在 providers 内。

- [`models`](models/models.md) — 模型目录与 provider 配置(数据布局、fetch、合并)
- [`thinking-effort`](models/thinking-effort.md) — thinking/effort 子系统(声明式 per-provider 映射)
