# 凭据连接信息统一（一个 Credential + 一个解析出口）

发一次请求所需的一切都集中在**一个**凭据结构里，再由**一个**解析出口交给 wire
层。凭据不会在中途被压成一根 `api_key` 字符串，`base_url` 不再只属于模型，
"是不是 OAuth"也不靠 token 前缀去猜。

由此，一把 key 可以带自己的 `base_url`：同一个 `openai-completions` 协议下，
一把 key 打官方端点、另一把 key 打阿里云百炼 `compatible-mode/v1`，不需要为每个
兼容端点预建一份模型清单。

本文是 [unified-auth-storage.md](./unified-auth-storage.md) 的一次具体化：那份
文档定的是"一个存储、一套登录、跨界面统一"的整体方向；本文只收敛其中的
**payload 结构层**，不涉及登录注册表、存储路径、刷新所有权等议题。

---

## 一次请求需要两样东西

发一次 LLM 请求要知道**打到哪个地址（base_url）**和**用哪个鉴权值（key/token）**。
如果这两样各走各的线，凭据就只能贡献其中一半：

- **base_url 线**：模型清单里每条模型写死一个 `base_url` → 加载成 `Model` →
  wire 层直接读 `model.base_url`。全程只跟模型绑定，不经过凭据。
- **鉴权值线**：AuthStore 的 `CredentialPool` 存凭据 → wire 层调
  `auth.usage.acquire_pooled(provider)` → 内部 `mgr.acquire_sync()` 拿到完整的
  `Credential` 对象。

如果最后一步把 `Credential` 压成一个 str 再交出去（按 6 种 payload 各自抽出一根
字符串：`ApiKeyPayload→api_key`、`OAuth/DeviceCode→access_token`、
`CliDelegated→读外部文件`、`credential_process→执行辅助命令`、`sso→不支持`），那么凭据知道的
base_url、headers 以及自己的 kind 就全部丢失。后果有两个：凭据里即使存了
`base_url`，wire 层也读不到；anthropic wire 只能靠 `"sk-ant-oat" in key`
猜这是不是 OAuth token，因为 kind 信息也一并没了。

所以解析出口交出的不是一根字符串，而是一次请求所需的完整连接信息。

## 一个凭据结构

`openprogram/auth/types.py` 用**一个** `CredentialData`（承载在
`Credential.payload` 位置）覆盖所有验证方式，而不是每种验证各建一个子类型。
不同验证方式的信息量差异是真实的 —— 简单验证信息少、复杂验证信息多 —— 因此
共性字段固定，差异部分放进一个 `data` 字典：

```python
@dataclass
class CredentialData:
    # —— 共性字段：所有验证方式都在同一位置回答的「发请求要用什么」——
    kind: str                     # "api_key" | "oauth" | "device_code" |
                                  # "cli_delegated" | "credential_process" | "sso"
    auth_value: str = ""          # 最终要放进 Authorization/x-api-key 的鉴权值：
                                  #   api_key 类 → key 本身
                                  #   oauth/device → access_token
                                  #   cli_delegated → 空（运行时从外部文件读，见 data）
    base_url: str = ""            # 该凭据指定的端点；空 ⇒ 用清单里的默认值（见解析规则）
    headers: dict = field(default_factory=dict)   # 该凭据附带的额外请求头；多数为空

    # —— 差异容器：某种验证特有的一切都进这里 ——
    data: dict = field(default_factory=dict)
```

`data` 里按 kind 放各自私有的字段，不预留成正式字段，这样一份 api_key 凭据不会
带着一堆空的 oauth 字段：

| kind | `auth_value` | `data` 里装什么 |
|---|---|---|
| `api_key` | key 本身 | （通常空） |
| `oauth` | access_token | `refresh_token` / `expires_at_ms` / `client_id` / `token_endpoint` / `scope` / `id_token` |
| `device_code` | access_token | `refresh_token` / `expires_at_ms` / `device_code_flow_id` |
| `cli_delegated` | 空 | `store_path` / `access_key_path` / `refresh_key_path` / `expires_key_path` |
| `credential_process` | 空 | `command` / `parses` / `json_key_path` / `cache_seconds` |
| `sso` | 空 | `broker` / `subject` |

**展示信息不进 payload。** 账号邮箱、显示名、org id 等留在 `Credential.metadata`
（UI 渲染它、manager 不解释它）。它们不影响请求怎么发，是 UI 与用量统计的消费
对象；混进连接信息只会让"要用的"和"给人看的"再次纠缠。

## 一个解析出口

一个函数把凭据翻译成 wire 层真正要用的连接信息：

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
    credential_process 在这里执行辅助命令，命中 cache_seconds 缓存窗口则复用；
    命令失败抛 AuthCredentialProcessError，resolver 各层原样上抛不再回退，
    因为用户显式配置的取值方式不该被别处的凭据悄悄顶替。
    sso 抛 AuthConfigError：该类型是预留的，没有任何流程能产出或使用它。"""
```

`auth.usage.acquire_pooled` 返回 `(conn: ResolvedConnection, profile, cred_id)`
而不是 `(token: str, profile, cred_id)`，这样凭据知道的连接信息不会在半路丢失。
它内部本就持有完整的 `cred`，只是把末尾的字符串抽取换成 `resolve_connection`。

## wire 层：凭据优先，清单兜底

各 wire（`openai_completions` / `openai_responses` / `anthropic`）统一按这套规则
取值：

```python
conn = <来自 acquire_pooled 的 ResolvedConnection，或 None>
api_key  = conn.auth_value if conn else opts.api_key
base_url = (conn.base_url if conn and conn.base_url else None) or model.base_url
headers  = { **(model.headers or {}), **(conn.headers if conn else {}), **(opts.headers or {}) }
is_oauth = bool(conn and conn.kind in ("oauth", "device_code"))
```

**规则一句话：凭据带了 `base_url` 就用凭据的，没带就用模型的默认值。** 于是官方
openai / deepseek / anthropic 照常工作（凭据不填 base_url），而接入百炼只需在存
key 时填上 `base_url = https://…maas.aliyuncs.com/compatible-mode/v1`，该凭据的
请求就打到百炼，其余不受影响。

```
Credential(CredentialData{auth_value, base_url, headers, kind, data})
      │
      └─(resolve_connection)─► ResolvedConnection{kind, auth_value, base_url, headers}
            │
            └─ wire: base_url = conn.base_url or model.base_url  ─► AsyncClient(...)
                     模型清单里的 base_url 仅作 conn.base_url 为空时的兜底默认
```

## 影响范围

**改动：**
- `openprogram/auth/types.py`：6 个 payload 类合并为一个 `CredentialData`；
  `_payload_to_dict`/`_payload_from_dict` 随之简化为单类型序列化
  （`kind` + 扁平字段 + `data`）。
- `openprogram/auth/resolver.py`：`resolve_connection` 取代返回裸 str 的路径。
- `openprogram/auth/usage.py`：`acquire_pooled` 返回 `ResolvedConnection` 三元组。
- 各 wire（`openai_completions.py` / `openai_responses/*` /
  `anthropic/anthropic.py`）：按上面的取值规则处理鉴权、base_url、headers 与
  oauth 判定。
- 读 payload 具体字段的地方（manager 的 OAuth 刷新读 `refresh_token`/`expires`、
  delegated 读外部文件路径等）：改为从 `CredentialData.data[...]` 取。

**保留：**
- 模型清单里的 `base_url` 仍在，作为凭据未指定时的默认值。内置 provider 开箱
  即用不变。
- `Credential.metadata` 与 OAuth 的邮箱等展示信息，UI 与用量统计照旧读取。
- 登录注册表、存储路径、刷新所有权、跨界面统一等议题不在本文范围内。

## 旧格式：一次性迁移

运行时（`_payload_from_dict` / `resolve_connection`）只认新结构。读到旧的
6-payload JSON（带 `__type__` 判别符）是错误，不是回退路径。既有凭据由一个
一次性迁移器搬到新结构，用户无需重新登录。

`openprogram/auth/_migrate_payload.py` 把每个
`~/.openprogram/auth/<provider>/<profile>.json` 里的旧 `payload` 就地转成新的
`CredentialData`，原子写回（沿用 store 的 write→fsync→replace）。转换规则：

| 旧 `__type__` | → 新 `kind` | `auth_value` | `data`（其余字段整体搬入） |
|---|---|---|---|
| `ApiKeyPayload` | `api_key` | `api_key` | `{}`（`base_url`/`headers` 若旧无则空） |
| `OAuthPayload` | `oauth` | `access_token` | `refresh_token` `expires_at_ms` `scope` `client_id` `token_endpoint` `id_token` `extra` |
| `DeviceCodePayload` | `device_code` | `access_token` | `refresh_token` `expires_at_ms` `device_code_flow_id` |
| `CliDelegatedPayload` | `cli_delegated` | `""` | `store_path` `access_key_path` `refresh_key_path` `expires_key_path` |
| `ExternalProcessPayload` | `credential_process` | `""` | `command` `parses` `json_key_path` `cache_seconds` |
| `SsoPayload` | `sso` | `""` | `broker` `subject` |

迁移器是幂等的：payload 已是新结构（有 `kind` 顶层字段、无 `__type__`）则跳过。
首次 `AuthStore` 加载时自动运行，也可用 `openprogram auth migrate` 手动触发。
`_rotation/_active/_disabled/_order.json` 等不含 `credentials` 的管理文件没有
payload，迁移器跳过它们。

## 测试

- `resolve_connection`：每种 kind 各一条 —— api_key 带/不带 base_url、oauth 出
  access_token、cli_delegated 现读外部文件、credential_process 跑假辅助脚本
  （json 与 text 两种解析、缓存窗口内不重复 fork、各类失败一律报错不回落）、
  sso 报错。
- 序列化往返：`CredentialData` → dict → `CredentialData` 字段一致（含 `data`）。
- wire 取值规则：凭据带 base_url 用凭据的，不带用 `model.base_url`；
  `kind=oauth` 时 `is_oauth` 为真且不依赖前缀。
- 端到端：存一把带 `base_url=百炼` 的 `api_key` 凭据，跑 `openai-completions`
  验证客户端 base_url 指向百炼，而官方 openai 凭据仍指向默认端点。
- 迁移器：旧 `ApiKeyPayload/OAuthPayload/CliDelegatedPayload` JSON 各一条，
  迁移后 `kind`/`auth_value`/`data` 正确且无 `__type__`；对已是新结构的文件
  幂等跳过；管理文件（`_rotation.json` 等）不被改动。
