# API key / 凭据解析

由一个模块回答"provider X 用的是哪个凭据，X 是否已配置？"。本文与
[credential-validation-unification](credential-validation-unification.md) 配套：
那篇讲"这个 key 是否*有效*"，本篇讲"这个 key *是什么*，以及 provider *是否已配置*"。

## 1. 为什么只留一个解析器

凭据解析有三个提问方：运行时 stream 路径、webui model-catalog 路径
（validate、fetcher、test），以及"是否已配置"的状态检查。如果各自维护一份
provider → env var 映射表和各自的查找顺序，同一个 provider 就会在一个界面读到
已配置、在另一个界面读到缺失：`google` 在不同路径下用不同的 env-var 名称解析，
`anthropic` 在运行时解析为 `ANTHROPIC_OAUTH_TOKEN`，在 webui 中却解析为
`ANTHROPIC_API_KEY`。

单一解析器同时决定凭据*可以来自哪里*。启动时没有任何代码把 config.json 的
`api_keys` 注入 `os.environ` —— 只有 `routes/config.py:87` 会做，且只在保存时、
只在该进程内生效。因此只读 env 的解析器会在 worker 重启后丢掉一个通过 web UI
保存的 key，而 config.json 里其实还有它；结果是 webui 的连通性检查通过、实际聊天
却失败。把 config.json 作为 env 之下的一层统一放在一处，就消除了这类分歧。

## 2. 模块位置

`providers/env_api_keys.py` 是规范模块。它位于 `providers/` 下，运行时和 webui
都能 import 且无循环依赖，并且它已经掌握着最广的特例知识（GitHub Copilot 的三个
token、Anthropic 的 OAuth 优先于 key、Amazon Bedrock 与 Google Vertex 的云凭据链）。

解析器是唯一知道任何 provider 凭据如何被找到的地方：分层（env → config →
云凭据链）、在热路径上带缓存、且可反向映射。新增一个 provider 只需一条表项。

## 3. API

```python
def env_vars_for(provider_id: str) -> list[str]:
    """该 provider 接受的 env-var 名称，按优先级排序。
    google -> [GEMINI_API_KEY, GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY]
    anthropic -> [ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_API_KEY]
    github-copilot -> [COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN]"""

def resolve_api_key(provider_id: str, *, allow_config: bool = True) -> str | None:
    """真实可用的 key/token，或 None。
    1. 按 env_vars_for() 中的每个 env var 逐一尝试，第一个命中者胜出；
    2. 若 allow_config：对每个名称查 config.json api_keys[<name>]（带缓存）；
    3. 云凭据 provider（bedrock/vertex）-> 这里返回 None（没有 bearer key）；
       它们的状态由 is_configured() 表达，而不是一个 key。"""

def is_configured(provider_id: str) -> bool:
    """当 provider 拥有可用凭据时为 True，包括云凭据链：
    resolve_api_key() 不为 None，或者 bedrock AWS 链 / vertex ADC 被满足。"""

def provider_id_for_env_var(env_var: str) -> str | None:
    """env_vars_for 的反向映射，供只知道 env-var 名称的 save-key verify 路径使用。"""
```

两个问题，两个函数。`resolve_api_key` 返回一个 key 或 `None`；`is_configured`
回答是否已配置，涵盖那些根本没有 bearer key 的云凭据 provider。若把两者合成一个
函数，就得为云 provider 返回一个占位字符串，而任何写 Bearer header 的代码都会把它
原样发出去 —— 所以它们保持分开。

一个模块级缓存（按 mtime 缓存的 config 字典）让 `resolve_api_key` 在每次 stream
的热路径上不去碰文件系统。用 mtime 而非 TTL，这样刚保存的 key 无需重启即可被立即
拾取。

config 兜底始终开启：config.json 里的 key 无论 env 如何都是用户的意图。
`allow_config=False` 留给那些确实只想按 env 解析的调用方。

## 4. 调用方

原有名称保留为规范函数之上的薄封装，因此约 30 处调用点不受影响：

- `get_env_api_key`（运行时）→ `resolve_api_key`，运行时路径由此获得 config.json
  兜底。
- `storage._resolve_api_key`（webui/model-catalog）→ 对已知 provider 走
  `resolve_api_key`；community 与 models.dev 类 provider 保留 env-var 兜底。
- `_model_catalog/providers.py:_is_configured` 与
  `providers/registry.py:check_providers` → `is_configured`。
- `credentials.provider_id_for_env_var` 重新导出规范实现。

`server.py` 的 provider 表与 `routes/providers.py:45` 保留 `_get_api_key`，
它以 env-var 名称为键，且本来就同时读 env 和 config。

## 5. 两张映射表不是同一件事

`_env_var_for` / `_ENV_API_KEYS` 存的是**展示首选**名称 —— key 表单里显示的那个，
`anthropic → ANTHROPIC_API_KEY`。`env_vars_for` 存的是**解析优先级**列表，其中
anthropic 把 OAuth 放在最前。它们回答的是不同的问题，因此 `_env_var_for` 并不是
`env_vars_for(pid)[0]`，两者无法机械合并。

同理，扁平的 `PROVIDER_ENV_VARS` 被 `auth/cli.py` 与 `auth/interactive.py` 当作
"带 env key 的 provider"列表使用，而该列表有意排除了 anthropic 和 copilot。
从规范表派生它会改变 auth-CLI 的登录流程，所以在 auth-CLI 语义理顺之前它保持原样。

## 6. 验证

- `tests/unit/test_api_key_resolution.py` 固定了优先级、config 兜底、无 key 时
  云凭据 `is_configured` 为 True、反向映射、Anthropic OAuth 优先于 key，以及
  Google 的三个名称。
- 跨界面：对每个已配置的 provider，运行时路径与 webui 路径解析结果一致。
- 重启行为：通过 `/api/config` POST 一个 key，重启 worker，在 env 已清空的情况下
  `resolve_api_key` 仍能从 config.json 找到它。

## 实现状态

上述规范函数与五处委托均已就位。遗留的扁平映射表（§5）按设计保留，直到 auth-CLI
的语义确定下来。

更长远的方向（不在本文范围内）：一个 `Provider` 元数据 dataclass（id、env_vars、
kind、base_url、default_api），把
[credential-validation-unification](credential-validation-unification.md) 的 KIND
表和 `_PROVIDER_DEFAULT_API` 折叠进去，做到每个 provider 一条注册项。
