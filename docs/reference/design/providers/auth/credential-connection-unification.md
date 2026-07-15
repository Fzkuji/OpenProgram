# 凭据连接信息统一（一个 Credential + 一个解析出口）

**目标（用户需求）：** 让「用一份凭据发一次请求所需的一切」集中到**一个**凭据结构里，
再由**一个**解析出口交给 wire 层——不再把凭据在中途压成一根 `api_key` 字符串、
把 `base_url` 丢在 catalog、把「是不是 OAuth」靠 token 前缀去猜。

直接的落地收益：一把 key 能带自己的 `base_url`（例：同一 `openai-completions`
协议，一把 key 打官方、一把 key 打阿里云百炼 `compatible-mode/v1`），无需为每个
兼容端点预建 catalog。这是本次改动的第一个真实使用场景。

本文是 [unified-auth-storage.md](./unified-auth-storage.md) 的一次具体化：那份文档
定的是「一个存储、一套登录、跨界面统一」的战略；本文只收敛其中的 **payload 结构层**，
不改动登录注册表 / 存储路径 / 刷新所有权等其它议题。

---

## 现在的运作逻辑（问题所在）

发一次 LLM 请求要两样东西：**打到哪个地址（base_url）** 和 **用哪个鉴权值（key/token）**。
今天这两样走**两条互不相交的线**：

- **base_url 线**：`_catalog/*.json` 每条模型写死一个 `base_url` → 加载成 `Model` →
  wire 层直接读 `model.base_url`。全程只跟「模型」绑定，**不经过凭据**。
- **鉴权值线**：AuthStore 的 `CredentialPool` 存凭据 → wire 层调
  `auth.usage.acquire_pooled(provider)` → 内部 `mgr.acquire_sync()` 拿到**完整
  `Credential` 对象**，却在最后一步 `_extract_token(cred)` 把它**压成一个 str** 扔出去。

`_extract_token` 是问题的浓缩：它按 6 种 payload 各自抽出一根字符串
（`ApiKeyPayload→api_key`、`OAuth/DeviceCode→access_token`、`CliDelegated→读外部文件`、
`external_process/sso→None`）。**凭据知道的 base_url、headers、以及自己的 kind，全在这一步丢失。**

后果：
1. 凭据里就算存了 `base_url`，wire 层也读不到它——它只会读 catalog 那个写死的地址。
2. anthropic wire 只能靠 `_is_oauth_token(api_key)` 判断 `"sk-ant-oat" in key` 来猜
   「这是不是 OAuth token」，因为凭据的 kind 信息在解析时也被丢掉了（`anthropic.py:144`）。

## 现存的 6 个 payload 类（仓库现状）

`openprogram/auth/types.py` 现有：`ApiKeyPayload` / `OAuthPayload` /
`DeviceCodePayload` / `CliDelegatedPayload` / `ExternalProcessPayload` / `SsoPayload`。
它们各带各的字段（api_key；access+refresh+expires+client_id+token_endpoint；
外部文件路径+key-path；argv；SSO 占位……）。差异是真实的、要保留的——
「简单验证信息少、复杂验证信息多」正是这些差异。

---

## 目标设计：一个 `Credential` 结构 + 一个 `ResolvedConnection` 出口

不为每种验证各建一个子类型。改为**一个凭据结构**，用「共性字段 + 一个 `data` 字典」
覆盖所有验证方式；再加**一个统一解析函数**把它翻译成 wire 层要用的连接信息。

### 1. 统一的凭据 payload

把 6 个 payload 类合并成**一个** `CredentialData`（承载在 `Credential.payload` 位置）：

```python
@dataclass
class CredentialData:
    # —— 共性字段：所有验证方式都在同一位置回答的「发请求要用什么」——
    kind: str                     # "api_key" | "oauth" | "device_code" |
                                  # "cli_delegated" | "external_process" | "sso"
    auth_value: str = ""          # 最终要放进 Authorization/x-api-key 的鉴权值：
                                  #   api_key 类 → key 本身
                                  #   oauth/device → access_token
                                  #   cli_delegated → 空（运行时从外部文件读，见 data）
    base_url: str = ""            # 该凭据指定的端点；空 ⇒ 用 catalog 默认（见解析规则）
    headers: dict = field(default_factory=dict)   # 该凭据附带的额外请求头；多数为空

    # —— 差异容器：某种验证特有的一切都进这里，无限可变 ——
    data: dict = field(default_factory=dict)
```

`data` 里按 kind 放各自私有的字段（不预留成正式字段，避免 api_key 凭据带一堆空的 oauth 字段）：

| kind | `auth_value` | `data` 里装什么 |
|---|---|---|
| `api_key` | key 本身 | （通常空） |
| `oauth` | access_token | `refresh_token` / `expires_at_ms` / `client_id` / `token_endpoint` / `scope` / `id_token` |
| `device_code` | access_token | `refresh_token` / `expires_at_ms` / `device_code_flow_id` |
| `cli_delegated` | 空 | `store_path` / `access_key_path` / `refresh_key_path` / `expires_key_path` |
| `external_process` | 空 | `command` / `parses` / `json_key_path` / `cache_seconds` |
| `sso` | 空 | `broker` / `subject` |

**展示/出处信息不进 payload。** 账号邮箱、显示名、org id 等仍留在 `Credential.metadata`
（其注释已明确「UI 渲染它、manager 不解释它」）——它们不影响请求怎么发，是 UI / usage
统计的消费对象，混进连接信息只会重演当前的「要用的」和「给人看的」纠缠。

### 2. 统一的解析出口 `ResolvedConnection`

用一个函数取代 `_extract_token`，返回 wire 层真正要用的连接信息（而非裸 str）：

```python
@dataclass
class ResolvedConnection:
    kind: str                     # 凭据类型——wire 判 OAuth 不再靠 key 前缀猜
    auth_value: str               # 已解析好的鉴权值（cli_delegated 已现读外部文件填好）
    base_url: str | None          # 凭据指定的端点；None ⇒ 让 wire 回退到 model.base_url
    headers: dict                 # 凭据附带的额外请求头（默认空）

def resolve_connection(cred: Credential) -> ResolvedConnection | None:
    """把一份 Credential 翻译成一次请求的连接信息。
    cli_delegated 在此现读外部文件取 token（保持它「外部 CLI 权威」的语义）。
    external_process/sso 未落地 → 返回 None，调用方按当前逻辑向下一层回退。"""
```

### 3. `acquire_pooled` 带出整个对象

`auth.usage.acquire_pooled` 现返回 `(token: str, profile, cred_id)`，改为
`(conn: ResolvedConnection, profile, cred_id)`——凭据知道的连接信息不再半路丢。
（它内部本就已持有完整 `cred`，只是原先在末尾调 `_extract_token`；改成调
`resolve_connection`。）

### 4. wire 层：凭据优先，catalog 兜底

各 wire（`openai_completions` / `openai_responses` / `anthropic`）统一改为：

```python
conn = <来自 acquire_pooled 的 ResolvedConnection，或 None>
api_key  = conn.auth_value if conn else opts.api_key
base_url = (conn.base_url if conn and conn.base_url else None) or model.base_url
headers  = { **(model.headers or {}), **(conn.headers if conn else {}), **(opts.headers or {}) }
is_oauth = bool(conn and conn.kind in ("oauth", "device_code"))   # 不再 _is_oauth_token 猜前缀
```

**规则一句话：凭据带了 `base_url` 就用凭据的，没带就用 catalog 默认。** 于是：
- 官方 openai / deepseek / anthropic **零改动**照常工作（凭据不填 base_url → 用 catalog）。
- 接百炼 = 存 key 时填上 `base_url = https://…maas.aliyuncs.com/compatible-mode/v1`，
  该凭据的请求就打到百炼，其余不受影响。

---

## 数据流（改动前后对照）

```
改动前：
  catalog.base_url ──────────────────────────► model.base_url ─┐
  Credential ─(_extract_token 压成 str)─► token ───────────────┤─► AsyncClient(api_key, base_url)
                                              ▲ base_url/kind 在此丢失

改动后：
  Credential(CredentialData{auth_value, base_url, headers, kind, data})
        │
        └─(resolve_connection)─► ResolvedConnection{kind, auth_value, base_url, headers}
              │
              └─ wire: base_url = conn.base_url or model.base_url  ─► AsyncClient(...)
                       catalog.base_url 仅作 conn.base_url 为空时的兜底默认
```

---

## 影响面（明确边界，避免外溢）

**改：**
- `openprogram/auth/types.py`：6 个 payload 类 → 一个 `CredentialData`；
  `_payload_to_dict/_payload_from_dict` 随之简化为单类型序列化（`kind` + 扁平字段 + `data`）。
- `openprogram/auth/resolver.py`：`_extract_token` → `resolve_connection`，返回
  `ResolvedConnection`。旧的「返回裸 str」路径完全取代。
- `openprogram/auth/usage.py`：`acquire_pooled` 返回 `ResolvedConnection` 三元组。
- 各 wire（`openai_completions.py` / `openai_responses/*` / `anthropic/anthropic.py`）：
  按上面「凭据优先、catalog 兜底」改鉴权/base_url/headers/oauth 判定的取值处。
- 读 payload 具体字段的地方（manager 的 OAuth 刷新读 `refresh_token/expires`、
  delegated 读外部文件路径等）：改为从 `CredentialData.data[...]` 取。

**新增：**
- `openprogram/auth/_migrate_payload.py`：一次性迁移器（见下节），首次 store 加载时
  自动运行，`openprogram auth migrate` 可手动触发。

**不改（保留）：**
- catalog 的 `base_url` 仍在——作为「凭据未指定时」的默认值。内置 provider 开箱即用不变。
- `Credential.metadata` / OAuth 的邮箱等展示信息——原地不动，UI/usage 照旧读。
- 登录注册表、存储路径、刷新所有权、跨界面统一（unified-auth-storage 的 P1–P5）——本文不触碰。
- `claude-code`（Meridian 守护进程）独立子系统——不走 pool，不受影响。

**删（死代码，顺带清理）：**
- `openprogram/providers/anthropic/_claude_max_proxy_registry.py`
- `openprogram/providers/anthropic/_max_proxy_runtime.py`

  这两个文件已无任何注册/引用（仅 `_claude_code_direct_runtime.py` 一句注释提及「取代了旧的
  max_proxy」）。它们正是「base_url 运行时另算」这类历史包袱的残留，清掉让新设计里
  base_url 的来源只剩「凭据 + catalog 默认」两个，更清晰。

---

## 旧格式：一次性迁移，之后完全不认（明确决策）

**不做运行时双读兼容**，但**提供一次性迁移**把已存凭据搬到新结构——用户无需重新登录。

- 运行时（`_payload_from_dict` / `resolve_connection`）**只认新结构**。读到旧的
  6-payload JSON（带 `__type__` 判别符）时，是错误，不是回退路径。
- 一个迁移器 `openprogram/auth/_migrate_payload.py` 把每个
  `~/.openprogram/auth/<provider>/<profile>.json` 里的旧 `payload` 就地转成新
  `CredentialData`，原子写回（沿用 store 的 write→fsync→replace）。转换规则：

  | 旧 `__type__` | → 新 `kind` | `auth_value` | `data`（其余字段整体搬入） |
  |---|---|---|---|
  | `ApiKeyPayload` | `api_key` | `api_key` | `{}`（`base_url`/`headers` 若旧无则空） |
  | `OAuthPayload` | `oauth` | `access_token` | `refresh_token` `expires_at_ms` `scope` `client_id` `token_endpoint` `id_token` `extra` |
  | `DeviceCodePayload` | `device_code` | `access_token` | `refresh_token` `expires_at_ms` `device_code_flow_id` `extra` |
  | `CliDelegatedPayload` | `cli_delegated` | `""` | `store_path` `access_key_path` `refresh_key_path` `expires_key_path` |
  | `ExternalProcessPayload` | `external_process` | `""` | `command` `parses` `json_key_path` `cache_seconds` |
  | `SsoPayload` | `sso` | `""` | `broker` `subject` |

- 迁移器**幂等**：payload 已是新结构（有 `kind` 顶层字段、无 `__type__`）则跳过。
  首次 `AuthStore` 加载时自动跑一遍；也可 `openprogram auth migrate` 手动触发。
- 迁移后旧格式**完全不再支持**——不留读回分支。
- 当前实机存量（迁移器必须覆盖）：`api_key`×6（deepseek/openai/openrouter/
  minimax-cn-coding-plan/bailian…）、`oauth`×1（openai-codex）、`cli_delegated`×2
  （gemini-subscription/google-gemini-cli）。`_rotation/_active/_disabled/_order.json`
  等无 `credentials` 的管理文件不含 payload，迁移器跳过。

## 测试

- `resolve_connection`：每种 kind 各一条——api_key 带/不带 base_url、oauth 出 access_token、
  cli_delegated 现读外部文件、external_process/sso 返回 None。
- 序列化往返：`CredentialData` → dict → `CredentialData` 字段一致（含 `data`）。
- wire 取值规则：凭据带 base_url → 用凭据的；不带 → 用 `model.base_url`；
  `kind=oauth` → `is_oauth` 为真且不再依赖前缀。
- 端到端（百炼场景）：存一把 `api_key` 凭据带 `base_url=百炼`，跑 `openai-completions`
  验证客户端 base_url 指向百炼、官方 openai 凭据仍指向 catalog 默认。
- 迁移器：旧 `ApiKeyPayload/OAuthPayload/CliDelegatedPayload` JSON 各一条 → 迁移后
  `kind/auth_value/data` 正确、无 `__type__`；对已是新结构的文件幂等跳过；管理文件
  （`_rotation.json` 等）不被误改。
```
