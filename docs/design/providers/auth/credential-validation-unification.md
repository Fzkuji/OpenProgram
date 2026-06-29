# 凭证校验统一

状态：**进行中** · 负责人：providers/webui · 最后更新：2026-06-03

## 1. 问题

“这个 provider key 有效吗？”在代码库里有五种不同的回答方式，而其中大多数都是错的：

| 表面入口 | 文件 · 符号 | 它做了什么 | 评判 |
| --- | --- | --- | --- |
| 连通性按钮 | `webui/_model_catalog/test_provider.py::test_provider` | 仅鉴权的 `GET /key`（OpenRouter）/ `GET /models`，并以推理 ping 兜底 | 唯一正确的一个——但只能从一个入口触达 |
| 保存 key 时的校验 | `webui/routes/config.py::_validate_api_key` | 仅对 **OpenAI / Anthropic / Google** 有 per-provider 分支；对其余约 17 个直接 `return None`（空操作） | 对大多数 provider 静默放过无效 key |
| 保存 key | `webui/routes/config.py::save_config` | 仅做字符检查，然后持久化 | **完全没有校验** |
| 拉取模型 | `_model_catalog/fetchers/*` | 每个 fetcher 各自重新实现自己的 key 检查 + 401 处理 | 重复、不一致 |
| 状态行 | `providers/registry.py::check_providers`、`_model_catalog/providers.py::_is_configured` | 环境变量 / 文件**是否存在** | 把 *configured*（存在）与 *valid*（被接受）混为一谈 |

由此引出三个具体缺陷：

1. **约 17 个 provider 的静默空操作。** 粘贴一个垃圾的 OpenRouter / DeepSeek /
   xAI / Groq / Mistral / … key，`_validate_api_key` 会返回 `None` →
   “有效”。校验只对 OpenAI/Anthropic/Google 存在。
2. **校验会消耗补全（completion）。** Anthropic 分支调用了
   `client.messages.create(...)`，Google 分支则在三个模型上循环调用
   `generate_content`——也就是说，它为了检查一个 *key* 而运行了 *推理*。
   连通性按钮在被重构去打鉴权端点之前也是这么做的。校验一个 key 永远不应调用模型。
3. **`configured` ≠ `valid`。** TUI/web 的状态行对任何存在的 key 都显示绿点，无论有效与否，
   而且 TUI 根本没有办法真正去测试某个 key。

## 2. 目标与非目标

**目标**

- 在不调用 *模型* 的前提下校验一个 *凭证*。
- 一个入口，让每个表面入口（保存、验证按钮、连通性检查、CLI、TUI 状态行、初始化向导）都调用它——
  添加一次 provider，它就在所有地方都能校验。
- 一套封闭的状态分类法，把“key 被拒绝”、“key 没问题但没余额”、“key 没问题但那个模型当前宕机”区分开。

**非目标**

- 不是用量/配额面板（余额仅在 provider 能廉价暴露的地方才上报，例如 OpenRouter 的 `/key`）。
- 不是只做惰性校验。OpenClaw 与 opencode 在首次使用模型时才惰性校验，没有保存时探测；
  OpenProgram 保留一个保存时的绿/红指示器，所以我们保留一个显式的廉价鉴权探测——
  这正是两个参考实现都说**如果**你想要这个指示器就该构建的东西。

## 3. 既有方案

**OpenClaw**（`/Users/fzkuji/Documents/Agent-Infrastructure/references/openclaw`）

- UI 从不校验。它调用一个 gateway RPC，`models.authStatus`
  （`ui/src/ui/controllers/model-auth-status.ts`），返回一个
  `{ts, providers[]}` 快照，并且**在服务端缓存 60 秒**，用户主动刷新后可用
  `refresh: true` 绕过。
- 服务端（`src/gateway/server-methods/models-auth-status.ts`、
  `src/infra/provider-usage.*`）通过**寄生在用量端点上**进行校验，而非调用模型：
  provider 的用量/配额端点返回 `401/403` = “token 过期”，其余任何 4xx/5xx = “HTTP n”。
- 凭证健康度是一个独立汇总（`src/agents/auth-health.ts`）：
  `ok | expiring | expired | missing | static`。即使 access token 已过期，
  只要存在 refresh token，OAuth profile 仍算健康。
- 结果**脱敏**——只有 `profileId/type/status/expiry`，绝不含 token。

**opencode**（sst/opencode）

- 在 `auth login` 时**不做**实时检查就存下 key（先存、再惰性失败）；
  首次真实请求才暴露坏 key。Catalog 来自 models.dev，与凭证解耦。
  单个 `provider/error.ts` 把上游错误形态映射 → 面向用户的补救字符串。

**我们采纳的**：状态分类法、60 秒缓存 + 强制刷新、密钥脱敏、廉价存在性检查 vs
一次网络调用鉴权 vs 模型可达性的分层，以及集中式的 status→message 映射器。
**我们与之不同的**：我们保留一个显式的保存时鉴权探测（第 1 层），因为我们想要一个两个参考实现都没有的指示器。

## 4. 统一入口

新模块 `openprogram/webui/_model_catalog/credentials.py`，从
`_model_catalog/__init__.py` 重新导出。

```python
def validate_credential(
    provider_id: str,
    *,
    api_key: str | None = None,  # 显式传入（持久化前校验）；None => 从 env+config+AuthManager 解析
    model: str | None = None,    # 仅在需要额外检查第 2 层模型可达性时设置
    timeout: float = 15.0,
    use_cache: bool = True,      # 60 秒 TTL，类似 OpenClaw 的 models.authStatus
) -> CredentialResult
```

```python
@dataclass
class CredentialResult:
    provider_id: str
    status: Literal["valid", "invalid_credential", "valid_no_balance",
                    "valid_model_unavailable", "missing", "not_applicable", "unknown"]
    ok: bool          # status 属于 {valid, valid_no_balance, valid_model_unavailable}
    kind: str         # 实际运行的探测：openai_bearer | openrouter_key | anthropic_native | anthropic_compat | google_query | oauth | cloud | none
    via: str | None   # "GET /models"、"GET /key"、"AuthManager"、"POST /chat/completions(model)"
    http_status: int | None
    latency_ms: int | None
    model: str | None # 第 2 层运行时回显
    detail: str | None  # 人类可读、不含密钥的补救提示
    cached: bool
```

由薄封装委托给它（保留向后兼容的形态）：

- `routes/config.py::_validate_api_key(env_var, value)` → 将 env_var 映射为
  provider_id，调用 `validate_credential(pid, api_key=value)`，返回旧的
  `error|None`。
- `test_provider.py::test_provider(pid, model)` →
  `validate_credential(pid, model=model)`，适配为 React `Connectivity` 组件读取的旧式
  `{ok, latency_ms, model, note, error}`。
- `provider_auth_status(provider_ids=None, refresh=False)` —— 供状态行使用的批量辅助函数，
  对应 `models.authStatus`（60 秒缓存、可绕过刷新）。

## 5. 分层校验

| 层 | 问题 | 成本 | 何时 |
| --- | --- | --- | --- |
| 0 — 存在性/格式 | 是否有凭证、它是否不是被遮掩的占位符、OAuth token 在结构上是否未过期？ | 离线，微秒级 | 始终（驱动廉价的状态行） |
| 1 — 鉴权接受 | provider 的鉴权端点是否接受了该 key？ | 一次 GET，0 token | 标准的绿/红检查 |
| 2 — 模型可达性 | 我现在能否触达 *这个具名* 模型？ | 一次推理 ping | 仅当传入 `model` 时 |

第 2 层正是今天的行为：`429/5xx` / OpenRouter “no endpoints” →
`valid_model_unavailable`（key 已证明良好，模型宕机），真正的坏请求 →
错误。

## 6. 按 provider-KIND 的探测表

| KIND | Providers | 第 1 层探测 |
| --- | --- | --- |
| `openai_bearer` | openai、deepseek、groq、cerebras、mistral、huggingface、kimi-coding、vercel-ai-gateway、xai、zai、opencode-api | `GET {base}/models`，`Authorization: Bearer` |
| `openrouter_key` | openrouter | `GET {base}/key`（那里的 `/models` 是**公开**的）—— body 还会暴露余额 |
| `anthropic_native` | anthropic | `GET https://api.anthropic.com/v1/models`，`x-api-key` + `anthropic-version: 2023-06-01`（Bearer 会被忽略） |
| `anthropic_compat` | minimax、minimax-cn（任何 registry 中 `api='anthropic-messages'` 且不是原生 `anthropic` 的 provider） | `GET {base}/v1/models`，`x-api-key` + `anthropic-version` —— 与原生相同的探测，但打向 provider 自己的 base_url（例如 `https://api.minimaxi.com/anthropic`）。`openai_bearer` 的 `GET {base}/models` 在这些主机上会 404，从而把一个好 key 误判为 `invalid_credential`。 |
| `google_query` | google | `GET https://generativelanguage.googleapis.com/v1beta/models?key=…&pageSize=1` |
| `oauth` | openai-codex、gemini-subscription、github-copilot、claude-code、opencode | `AuthManager.acquire_sync(pid).status`（`fresh`→valid，`needs_reauth`→invalid）；除一次可选的 token 刷新外无网络调用 |
| `cloud` | amazon-bedrock、google-vertex、azure-openai-responses | 在加入原生 list 调用之前，通用探测返回 `not_applicable`（SigV4 / ADC / 按 deployment 加 key） |

## 7. 状态码 → 状态（唯一解释器）

```
200                                          -> valid
401 / 403                                    -> invalid_credential
402 / body~insufficient.?quota|balance       -> valid_no_balance
429 / 5xx / "no endpoints" / "data policy"   -> valid_model_unavailable   (layer 2 only)
transport error / ambiguous                  -> unknown
no credential resolvable                      -> missing
provider has no key concept                  -> not_applicable
```

## 8. 缓存与刷新

进程内 60 秒 TTL，以 `provider_id`（+ 是否指定了某个模型）为键。
`use_cache=False` / `refresh=True` 可绕过。结果携带 `cached: bool`。
绝不存储或返回密钥。

## 9. 表面入口集成

- **保存**（`POST /api/config`）：*先* 持久化（一个缓慢/离线的 provider 绝不能阻塞保存），
  然后触发 `validate_credential(pid, api_key=val)`，让状态行从
  `Checking…` → 绿/琥珀/红/灰 翻转。仅第 1 层——绝不消耗补全。
- **验证按钮**（`POST /api/config/verify`）：相同的调用，显式传 `api_key`，
  同步执行，展示 status + `detail`。
- **连通性检查**（既有 React 组件 → `/test`→`/validate`）：
  默认 = 第 1 层；一个“Test a model”的入口会传入 `{model}` 以触发第 2 层。
  既有的“Model X is unavailable right now”提示就是
  `valid_model_unavailable` 的渲染。
- **状态行**（`config_schema.get_settings` + TUI + web Providers 标签页）：两列——
  `Configured`（第 0 层存在性，即时）和 `Validated`（缓存的第 1 层，60 秒）。
  每一行新增一个 `/test` 操作，使 TUI 能触达 web 按钮所用的同一探测。
  OAuth 行会清晰地渲染 `fresh/expiring/needs_reauth`。

模糊状态的文案（opencode `error.ts` 风格）：
`valid_no_balance` → “Key works — account has no balance. Add funds at <doc>.”；
`invalid_credential` → “Key rejected (401). Re-check the key or re-login.”；
`unknown` → “Couldn't reach <provider> to verify. Saved anyway; will validate
on first use.”；OAuth `needs_reauth` → “Login expired — run `openprogram
providers login <pid>`.”

## 10. 迁移计划

1. **（已完成）** 创建 `credentials.py`：`CredentialResult`、status 枚举、按 KIND 的探测注册表；
   把 `_credential_check` / `_is_model_unavailable` /
   `_MODEL_DOWN_STATUSES` / `_CREDENTIAL_PROBE_PATHS` 迁到这里；加入 Anthropic、
   Google 和 OAuth 探测以及 `402`/无余额分支；实现
   `validate_credential()` 的第 0→1→（有 model 则到 2）层 + 60 秒缓存 +
   `provider_auth_status()`。
2. **（已完成）** `test_provider()` 委托给 `validate_credential()` 并适配为旧式 dict。
3. **（已完成）** `_validate_api_key()` 变成一个 shim → 堵上约 17 个 provider 的缺口。
   加入 `POST /api/providers/{name}/validate` + `GET
   /api/providers/auth-status`；`/test` 作为 `/validate` 的别名。
4. **（已排期）** Fetcher 在分发前调用一次 `validate_credential(pid)`；
   删除每个 fetcher 各自的 key 存在性重复实现。
5. **（已排期）** `check_providers()` / `_is_configured()` 保留廉价存在性作为
   `configured`；新增缓存的 `validated`。`config_schema.get_settings()` 读取两者
   并设置 `action:'/test'`。
6. **（已排期）** 修复 bedrock / vertex 的 `<authenticated>` 哨兵——返回
   `None` + KIND `cloud`，使它们报告 `not_applicable`，而不是一个假绿。
7. **（已排期）** 测试：outcome × KIND 矩阵。

## 11. 测试矩阵

outcome × KIND：`200→valid`、`401→invalid_credential`、
`402/insufficient_quota→valid_no_balance`、OpenRouter 公开的 `/models` **不**
被误判为 valid（必须用 `/key`）、缺少 `anthropic-version` 的 Anthropic、
OAuth `needs_reauth`、第 2 层 `429→valid_model_unavailable`、离线→`unknown`、
无 key→`missing`。

## 12. 添加新 provider

在 `credentials.py::_kind_for` 中声明它的探测 KIND（默认 `openai_bearer`
无需任何额外操作）。这一行就把它同时接入保存校验、连通性按钮、状态行以及 CLI/TUI。

**走 Anthropic 协议的第三方**（MiniMax 及同类）会被自动检测：
对任何 registry 中 `api` 为 `anthropic-messages`（且非原生 `anthropic`）的 provider，
`_kind_for` 返回 `anthropic_compat`。要在三个地方保持一致，否则该 provider 只会半工作：
- `_kind_for` → `anthropic_compat`（凭证探测打 `{base}/v1/models`）；
- `_model_catalog/providers.py::_PROVIDER_DEFAULT_API` 必须打上
  `anthropic-messages` 标记（这样拉取的/自定义的行会路由到正确的 stream fn，
  而不是 `POST /chat/completions`）—— 与 `models_generated` 一致；
- `_model_catalog/fetchers` 把 `anthropic-messages` 的 provider 路由到
  感知 base_url 的 `_fetch_anthropic`（OpenAI 兼容的 `GET {base}/models`
  在 `/anthropic` 主机上会 404）。
一个漂移守护测试（`test_model_fetch_routing.py`）把 api 标记钉死在
`models_generated` 上。

## 13. 待解问题

- `valid_no_balance` 只对 OpenRouter（`/key`）以及通过第 2 层的 `402` 才可廉价检测；
  在其他地方，`200` 证明了鉴权但不证明余额——接受朴素的
  `valid`，直到首次真实调用暴露出 `insufficient_quota`。
- 在每次单 key 保存时自动跑第 1 层，还是在批量保存时延后到显式点击 Verify
  （做节流以避免探测突发）。
- Anthropic OAuth（`ANTHROPIC_OAUTH_TOKEN`）在同一个 `/v1/models` 探测上需要
  `Authorization: Bearer` + `anthropic-beta: oauth-…`——确认这个 beta
  值，或者把它路由到 AuthManager 路径。
- openai-codex 没有仅鉴权的列表端点（ChatGPT 后端会 403），所以
  它唯一的端到端探测是第 2 层的 `/responses` ping——默认情况下依赖 AuthManager 的
  `Credential.status` 做（结构性而非端到端的）检查。
