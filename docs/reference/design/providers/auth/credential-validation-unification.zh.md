# 凭证校验

每一个会问"这个 provider key 有效吗"的界面 —— 保存、验证按钮、连通性检查、CLI、
TUI 状态行、初始化向导 —— 都调用同一个入口。添加一次 provider，它就在所有地方
都能校验。

## 1. 两个问题，不是一个

`configured` 与 `valid` 是两件不同的事实。一把 key 可以存在于环境中却被 provider
拒绝；可以被接受却背后没有余额；可以本身没问题，而具体点名的那个模型暂时宕机。
状态行若对任何存在的 key 都显示绿点，就混淆了前两者；而只回答是/否的校验器
无法表达其余的差别。

设计把它们分开：一次廉价的离线存在性检查回答 `configured`，一次鉴权端点调用回答
`valid`，一套封闭的状态分类法承载"被拒绝"、"无余额"、"模型不可达"之间的区别。

**校验凭证永远不调用模型。** 一次鉴权探测只花一个 GET、零 token；为检查一把 key
而运行推理，等于花掉补全去获知鉴权端点早就知道的事。

这里有意不采用纯惰性校验。OpenClaw 与 opencode 在首次使用模型时才校验，没有保存时
探测；OpenProgram 保留一个保存时的绿/红指示器，因此保留一次显式的廉价鉴权探测 ——
这正是两个参考实现所描述的、用于实现该指示器的机制。

范围之外：这不是用量或配额面板。余额仅在 provider 能廉价暴露它的地方上报，
例如 OpenRouter 的 `/key`。

## 2. 既有方案

**OpenClaw** —— UI 从不校验。它调用一个 gateway RPC，`models.authStatus`
（`ui/src/ui/controllers/model-auth-status.ts`），返回 `{ts, providers[]}` 快照，
服务端缓存 60 秒，可用 `refresh: true` 绕过。服务端
（`src/gateway/server-methods/models-auth-status.ts`、`src/infra/provider-usage.*`）
基于用量端点校验而非调用模型：用量/配额端点返回 `401/403` 表示 token 过期，
其余任何 4xx/5xx 上报为 "HTTP n"。凭证健康度是一个独立汇总
（`src/agents/auth-health.ts`）：`ok | expiring | expired | missing | static`，
其中 OAuth profile 只要存在 refresh token，即使 access token 已过期也算健康。
结果做了密钥脱敏 —— 只有 `profileId/type/status/expiry`，绝不含 token。

**opencode** —— 在 `auth login` 时不做实时检查就存下 key；首次真实请求才暴露坏
key。Catalog 来自 models.dev，与凭证解耦。单个 `provider/error.ts` 把上游错误形态
映射为面向用户的补救字符串。

本设计采纳的部分：状态分类法、60 秒缓存加强制刷新、密钥脱敏、廉价存在性检查与
一次网络调用鉴权与模型可达性的分层，以及集中式的 status→message 映射器。

## 3. 统一入口

`openprogram/webui/_model_listing/credentials.py`，从 `_model_catalog/__init__.py`
重新导出。

```python
def validate_credential(
    provider_id: str,
    *,
    api_key: str | None = None,  # 显式传入（持久化前校验）；None => 从 env+config+CredentialProvider 解析
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
    via: str | None   # "GET /models"、"GET /key"、"CredentialProvider"、"POST /chat/completions(model)"
    http_status: int | None
    latency_ms: int | None
    model: str | None # 第 2 层运行时回显
    detail: str | None  # 人类可读、不含密钥的补救提示
    cached: bool
```

由薄封装委托给它，各自保留原有形态：

- `routes/config.py::_validate_api_key(env_var, value)` 把 env_var 映射为
  provider_id，调用 `validate_credential(pid, api_key=value)`，返回调用方期待的
  `error|None`。
- `test_provider.py::test_provider(pid, model)` 调用
  `validate_credential(pid, model=model)`，适配为 React `Connectivity` 组件读取的
  `{ok, latency_ms, model, note, error}` 形态。
- `provider_auth_status(provider_ids=None, refresh=False)` 是供状态行使用的批量
  辅助函数，对应 `models.authStatus`（60 秒缓存、可绕过刷新）。

## 4. 三层

| 层 | 问题 | 成本 | 何时 |
| --- | --- | --- | --- |
| 0 — 存在性/格式 | 是否有凭证、它是否不是被遮掩的占位符、OAuth token 在结构上是否未过期？ | 离线，微秒级 | 始终（驱动廉价的状态行） |
| 1 — 鉴权接受 | provider 的鉴权端点是否接受了该 key？ | 一次 GET，0 token | 标准的绿/红检查 |
| 2 — 模型可达性 | 我现在能否触达 *这个具名* 模型？ | 一次推理 ping | 仅当传入 `model` 时 |

第 2 层存在的理由是"key 没问题但这个模型宕机"是一个真实且独立的结果：
`429/5xx` 或 OpenRouter 的 "no endpoints" 解析为 `valid_model_unavailable`，
而真正的坏请求是错误。

## 5. 按 provider KIND 的探测

| KIND | Providers | 第 1 层探测 |
| --- | --- | --- |
| `openai_bearer` | openai、deepseek、groq、cerebras、mistral、huggingface、kimi-coding、vercel-ai-gateway、xai、zai、opencode-api | `GET {base}/models`，`Authorization: Bearer` |
| `openrouter_key` | openrouter | `GET {base}/key`（那里的 `/models` 是**公开**的）—— body 还会暴露余额 |
| `anthropic_native` | anthropic | `GET https://api.anthropic.com/v1/models`，`x-api-key` + `anthropic-version: 2023-06-01`（Bearer 会被忽略） |
| `anthropic_compat` | minimax、minimax-cn（任何 registry 中 `api='anthropic-messages'` 且不是原生 `anthropic` 的 provider） | `GET {base}/v1/models`，`x-api-key` + `anthropic-version` —— 与原生相同的探测，但打向 provider 自己的 base_url（例如 `https://api.minimaxi.com/anthropic`）。`openai_bearer` 的 `GET {base}/models` 在这些主机上会 404，从而把一个好 key 误判为 `invalid_credential`。 |
| `google_query` | google | `GET https://generativelanguage.googleapis.com/v1beta/models?key=…&pageSize=1` |
| `oauth` | openai-codex、gemini-subscription、github-copilot、claude-code、opencode | `CredentialProvider.acquire_sync(pid).status`（`fresh`→valid，`needs_reauth`→invalid）；除一次可选的 token 刷新外无网络调用 |
| `cloud` | amazon-bedrock、google-vertex、azure-openai-responses | 在加入原生 list 调用之前，通用探测返回 `not_applicable`（SigV4 / ADC / 按 deployment 加 key） |

## 6. 状态码解释

由一个解释器把结果映射为状态：

```
200                                          -> valid
401 / 403                                    -> invalid_credential
402 / body~insufficient.?quota|balance       -> valid_no_balance
429 / 5xx / "no endpoints" / "data policy"   -> valid_model_unavailable   (layer 2 only)
transport error / ambiguous                  -> unknown
no credential resolvable                      -> missing
provider has no key concept                  -> not_applicable
```

`valid_no_balance` 只对 OpenRouter（通过 `/key`）以及通过第 2 层的 `402` 才可廉价
检测。在其他地方，`200` 证明了鉴权但不证明余额，所以结果就是朴素的 `valid`，
直到首次真实调用暴露出 `insufficient_quota`。

## 7. 缓存

进程内 60 秒 TTL，以 `provider_id` 加上是否指定了某个模型为键。
`use_cache=False` / `refresh=True` 可绕过。结果携带 `cached: bool`。
密钥既不存储也不返回。

## 8. 各界面如何使用

- **保存**（`POST /api/config`）：先持久化，这样一个缓慢或离线的 provider 绝不会
  阻塞保存；然后触发 `validate_credential(pid, api_key=val)`，让状态行从
  `Checking…` 翻转到绿/琥珀/红/灰。仅第 1 层 —— 保存一把 key 绝不消耗补全。
- **验证按钮**（`POST /api/config/verify`）：相同的调用，显式传 `api_key`，
  同步执行，展示 status 与 `detail`。
- **连通性检查**（`/test` → `/validate` 背后的 React 组件）：默认第 1 层；
  "Test a model" 入口会传入 `{model}` 以触发第 2 层。"Model X is unavailable right
  now" 提示就是 `valid_model_unavailable` 的渲染方式。
- **状态行**（`config_schema.get_settings`、TUI、web Providers 标签页）：两列 ——
  `Configured`（第 0 层存在性，即时）与 `Validated`（缓存的第 1 层，60 秒）。
  每一行都带一个 `/test` 操作，使 TUI 能触达 web 按钮所用的同一探测。
  OAuth 行会分别渲染 `fresh` / `expiring` / `needs_reauth`。

补救文案是集中的，风格参照 opencode 的 `error.ts`：
`valid_no_balance` → "Key works — account has no balance. Add funds at <doc>."；
`invalid_credential` → "Key rejected (401). Re-check the key or re-login."；
`unknown` → "Couldn't reach <provider> to verify. Saved anyway; will validate
on first use."；OAuth `needs_reauth` → "Login expired — run `openprogram
providers login <pid>`."

## 9. 添加 provider

在 `credentials.py::_kind_for` 中声明它的探测 KIND；默认的 `openai_bearer`
无需任何声明。这一行就把该 provider 同时接入保存校验、连通性按钮、状态行以及
CLI/TUI。

**走 Anthropic 协议的第三方**（MiniMax 及同类）会被自动检测：对任何 registry 中
`api` 为 `anthropic-messages` 且非原生 `anthropic` 的 provider，`_kind_for` 返回
`anthropic_compat`。三个地方必须一致，否则该 provider 只会半工作：

- `_kind_for` → `anthropic_compat`，使凭证探测打向 `{base}/v1/models`；
- `_model_catalog/providers.py::_PROVIDER_DEFAULT_API` 必须打上
  `anthropic-messages` 标记，使拉取的和自定义的行路由到正确的 stream 函数，
  而不是 `POST /chat/completions` —— 与 `models_generated` 一致；
- `_model_catalog/fetchers` 把 `anthropic-messages` 的 provider 路由到感知
  base_url 的 `_fetch_anthropic`，因为 OpenAI 兼容的 `GET {base}/models` 在
  `/anthropic` 主机上会 404。

`test_model_fetch_routing.py` 把 api 标记与 `models_generated` 绑定校验，
防止三者产生分歧。

## 10. 测试矩阵

outcome × KIND：`200→valid`、`401→invalid_credential`、
`402/insufficient_quota→valid_no_balance`、OpenRouter 公开的 `/models` **不**
被误判为 valid（探测必须用 `/key`）、缺少 `anthropic-version` 的 Anthropic、
OAuth `needs_reauth`、第 2 层 `429→valid_model_unavailable`、离线→`unknown`、
无 key→`missing`。

## 实现状态

`credentials.py` 中已有 `CredentialResult`、status 枚举和按 KIND 的探测注册表，
`validate_credential()` 执行第 0→1→（指定 model 时到 2）层，并带 60 秒缓存和
`provider_auth_status()`。`test_provider()` 与 `_validate_api_key()` 委托给它，
这也正是那些原本没有任何探测的 provider 得以补上校验的原因；
`POST /api/providers/{name}/validate` 与 `GET /api/providers/auth-status` 已提供，
`/test` 作为 `/validate` 的别名。

尚未落地的部分：fetcher 在分发前调用一次 `validate_credential(pid)`，取代各自
重复实现的 key 存在性检查；`check_providers()` 与 `_is_configured()` 在廉价存在性
之外再暴露一个缓存的 `validated`，由 `config_schema.get_settings()` 同时读取；
以及 bedrock/vertex 报告 `not_applicable`，而不是由占位值造成的假绿。

待定的点：

- 单 key 保存时自动跑第 1 层，还是在批量保存时延后到显式点击 Verify —— 后者需要
  节流以避免探测突发。
- Anthropic OAuth（`ANTHROPIC_OAUTH_TOKEN`）在同一个 `/v1/models` 探测上需要
  `Authorization: Bearer` 加 `anthropic-beta: oauth-…`；要么确认这个 beta 值，
  要么把它路由到 CredentialProvider 路径。
- openai-codex 没有仅鉴权的列表端点（ChatGPT 后端会 403），所以它唯一的端到端
  探测是第 2 层的 `/responses` ping。默认检查依赖 CredentialProvider 的
  `Credential.status`，那是结构性的而非端到端的。
