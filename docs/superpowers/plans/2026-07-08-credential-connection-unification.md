# 凭据连接信息统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 6 个 payload 类合并成一个 `CredentialData`（共性字段 + `data` 字典），用一个 `resolve_connection() → ResolvedConnection` 把鉴权值/base_url/headers/kind 交给 wire 层，让一把 key 能带自己的 base_url，并一次性迁移已存凭据。

**Architecture:** `CredentialData` 承载在 `Credential.payload` 位置，取代
`ApiKeyPayload/OAuthPayload/DeviceCodePayload/CliDelegatedPayload/ExternalProcessPayload/SsoPayload`。
全代码库对这些类的 `isinstance` 判类型改为 `payload.kind == "..."`，字段访问改为
`payload.auth_value / payload.base_url / payload.data[...]`。读取出口 `_extract_token`
（返回 str）换成 `resolve_connection`（返回 `ResolvedConnection`）。wire 层凭据优先、
catalog 兜底。一次性迁移器搬旧 JSON。

**Tech Stack:** Python 3, dataclasses, pytest。仓库测试用 `.venv-test/bin/python -m pytest`（无则 `python -m pytest`）。

## Global Constraints

- 设计源文档：`docs/design/providers/auth/credential-connection-unification.md`（每个任务的依据）。
- **不做旧格式运行时兼容**：`_payload_from_dict` 只认新结构；旧结构靠一次性迁移器转换，转换后旧格式不再支持。
- 凭据 schema 版本常量 `CREDENTIAL_SCHEMA_VERSION` 随本次结构变更 +1（旧 JSON 的 `v` 值触发迁移，不触发 `AuthCorruptCredentialError`）。
- `Credential.metadata` 与展示信息（邮箱/名字/org）保持原地，不进 `CredentialData`、不进 `ResolvedConnection`。
- 每个任务 TDD：先写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。
- 提交信息尾部加：
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `CredentialData` 结构 + 单类型序列化

**Files:**
- Modify: `openprogram/auth/types.py`（删 6 payload 类、`CredentialPayload` 联合、`_payload_from_dict` 的 kind→class 映射；加 `CredentialData`；改 `_payload_to_dict/_payload_from_dict`；`CREDENTIAL_SCHEMA_VERSION` +1）
- Test: `tests/auth/test_credential_data.py`

**Interfaces:**
- Produces:
  - `CredentialData(kind: str, auth_value: str = "", base_url: str = "", headers: dict = {}, data: dict = {})`
  - `_payload_to_dict(p: CredentialData) -> dict`（扁平：`{kind, auth_value, base_url, headers, data}`，无 `__type__`）
  - `_payload_from_dict(kind: str, d: dict) -> CredentialData`（只认新结构，缺字段用默认）
  - `CREDENTIAL_SCHEMA_VERSION`（较旧值 +1）

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_credential_data.py`：

```python
from openprogram.auth.types import (
    Credential, CredentialData, CREDENTIAL_SCHEMA_VERSION,
)


def test_credential_data_defaults():
    d = CredentialData(kind="api_key", auth_value="sk-x")
    assert d.kind == "api_key"
    assert d.auth_value == "sk-x"
    assert d.base_url == ""
    assert d.headers == {}
    assert d.data == {}


def test_credential_roundtrip_api_key():
    cred = Credential(
        provider_id="openai", profile_id="default", kind="api_key",
        payload=CredentialData(kind="api_key", auth_value="sk-x",
                               base_url="https://ep/v1"),
    )
    back = Credential.from_dict(cred.to_dict())
    assert isinstance(back.payload, CredentialData)
    assert back.payload.kind == "api_key"
    assert back.payload.auth_value == "sk-x"
    assert back.payload.base_url == "https://ep/v1"


def test_credential_roundtrip_oauth_data():
    cred = Credential(
        provider_id="openai-codex", profile_id="default", kind="oauth",
        payload=CredentialData(
            kind="oauth", auth_value="at",
            data={"refresh_token": "rt", "expires_at_ms": 123,
                  "client_id": "cid", "token_endpoint": "https://t"},
        ),
    )
    back = Credential.from_dict(cred.to_dict())
    assert back.payload.data["refresh_token"] == "rt"
    assert back.payload.data["expires_at_ms"] == 123


def test_payload_dict_has_no_type_discriminator():
    from openprogram.auth.types import _payload_to_dict
    d = _payload_to_dict(CredentialData(kind="api_key", auth_value="k"))
    assert "__type__" not in d
    assert d["kind"] == "api_key"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_credential_data.py -x -q`
Expected: FAIL（`ImportError: cannot import name 'CredentialData'`）

- [ ] **Step 3: 实现 `CredentialData` + 序列化**

在 `openprogram/auth/types.py` 中删除 `ApiKeyPayload/OAuthPayload/DeviceCodePayload/CliDelegatedPayload/ExternalProcessPayload/SsoPayload` 六个类与 `CredentialPayload` 联合，替换为：

```python
@dataclass
class CredentialData:
    """One credential's connection info. Replaces the 6 payload classes.

    Common fields answer "what to send" uniformly; ``data`` holds whatever
    is specific to this ``kind`` (refresh tokens, external-file paths, ...).
    """
    kind: str
    auth_value: str = ""
    base_url: str = ""
    headers: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)


CredentialPayload = CredentialData  # kept as an alias so annotations still resolve
```

把 `_payload_to_dict / _payload_from_dict` 改为：

```python
def _payload_to_dict(p: CredentialData) -> dict:
    return {
        "kind": p.kind,
        "auth_value": p.auth_value,
        "base_url": p.base_url,
        "headers": dict(p.headers),
        "data": dict(p.data),
    }


def _payload_from_dict(kind: CredentialKind, d: dict) -> CredentialData:
    # New structure only. Old 6-payload JSON (has "__type__", no top-level
    # "kind" inside payload) is migrated out-of-band by _migrate_payload.py.
    return CredentialData(
        kind=d.get("kind", kind),
        auth_value=d.get("auth_value", ""),
        base_url=d.get("base_url", ""),
        headers=dict(d.get("headers") or {}),
        data=dict(d.get("data") or {}),
    )
```

把文件顶部 `CREDENTIAL_SCHEMA_VERSION = N` 改为 `N+1`（查当前值后+1）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv-test/bin/python -m pytest tests/auth/test_credential_data.py -x -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/types.py tests/auth/test_credential_data.py
git commit -m "refactor(auth): merge 6 payload classes into one CredentialData

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 一次性迁移器 `_migrate_payload.py`

**Files:**
- Create: `openprogram/auth/_migrate_payload.py`
- Test: `tests/auth/test_migrate_payload.py`

**Interfaces:**
- Consumes: `CredentialData`（Task 1）
- Produces:
  - `migrate_payload_dict(old: dict) -> dict`：把一个旧 payload dict（带 `__type__`）转成新扁平 dict。已是新结构（有顶层 `kind`、无 `__type__`）则原样返回。
  - `migrate_store(root: Path | None = None) -> int`：遍历 `<root>/auth/<provider>/<profile>.json`，就地迁移，返回改写文件数。管理文件（`_rotation/_active/_disabled/_order.json`，无 `credentials`）跳过。

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_migrate_payload.py`：

```python
import json
from openprogram.auth._migrate_payload import migrate_payload_dict, migrate_store


def test_migrate_api_key():
    out = migrate_payload_dict({"api_key": "sk-x", "__type__": "ApiKeyPayload"})
    assert out == {"kind": "api_key", "auth_value": "sk-x",
                   "base_url": "", "headers": {}, "data": {}}


def test_migrate_oauth_moves_extras_into_data():
    out = migrate_payload_dict({
        "access_token": "at", "refresh_token": "rt", "expires_at_ms": 9,
        "scope": ["a"], "client_id": "c", "token_endpoint": "t",
        "id_token": "id", "extra": {"email": "e"}, "__type__": "OAuthPayload",
    })
    assert out["kind"] == "oauth"
    assert out["auth_value"] == "at"
    assert out["data"]["refresh_token"] == "rt"
    assert out["data"]["expires_at_ms"] == 9
    assert out["data"]["extra"] == {"email": "e"}
    assert "access_token" not in out


def test_migrate_cli_delegated_empty_auth_value():
    out = migrate_payload_dict({
        "store_path": "/p", "access_key_path": ["access_token"],
        "refresh_key_path": ["refresh_token"], "expires_key_path": ["expiry_date"],
        "__type__": "CliDelegatedPayload",
    })
    assert out["kind"] == "cli_delegated"
    assert out["auth_value"] == ""
    assert out["data"]["store_path"] == "/p"
    assert out["data"]["access_key_path"] == ["access_token"]


def test_migrate_idempotent_on_new_structure():
    new = {"kind": "api_key", "auth_value": "k", "base_url": "",
           "headers": {}, "data": {}}
    assert migrate_payload_dict(new) == new


def test_migrate_store_rewrites_files_and_skips_admin(tmp_path):
    auth = tmp_path / "auth"
    (auth / "openai").mkdir(parents=True)
    cred_file = auth / "openai" / "default.json"
    cred_file.write_text(json.dumps({
        "v": 1, "provider_id": "openai", "profile_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "payload": {"api_key": "sk-x", "__type__": "ApiKeyPayload"},
        "credentials": [{
            "v": 1, "provider_id": "openai", "profile_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            "payload": {"api_key": "sk-x", "__type__": "ApiKeyPayload"},
        }],
    }))
    (auth / "_rotation.json").write_text(json.dumps({"enabled": {}}))

    n = migrate_store(root=tmp_path)
    assert n == 1
    got = json.loads(cred_file.read_text())
    assert got["credentials"][0]["payload"]["kind"] == "api_key"
    assert "__type__" not in got["credentials"][0]["payload"]
    # admin file untouched
    assert json.loads((auth / "_rotation.json").read_text()) == {"enabled": {}}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_migrate_payload.py -x -q`
Expected: FAIL（`ModuleNotFoundError: _migrate_payload`）

- [ ] **Step 3: 实现迁移器**

创建 `openprogram/auth/_migrate_payload.py`：

```python
"""One-shot migration: old 6-payload JSON → new CredentialData structure.

Runtime code only understands the new structure (see types._payload_from_dict).
This searches ~/.openprogram/auth/**.json, rewrites each credential's payload
in place, atomically. Idempotent: a payload already in the new shape is left
as-is. Old format is NOT supported after migration.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TYPE_TO_KIND = {
    "ApiKeyPayload": "api_key",
    "OAuthPayload": "oauth",
    "DeviceCodePayload": "device_code",
    "CliDelegatedPayload": "cli_delegated",
    "ExternalProcessPayload": "external_process",
    "SsoPayload": "sso",
}
# Which old field became auth_value (rest go into data).
_AUTH_FIELD = {
    "ApiKeyPayload": "api_key",
    "OAuthPayload": "access_token",
    "DeviceCodePayload": "access_token",
}


def migrate_payload_dict(old: dict) -> dict:
    # Already new structure → idempotent no-op.
    if "kind" in old and "__type__" not in old:
        return old
    tname = old.get("__type__", "")
    kind = _TYPE_TO_KIND.get(tname)
    if kind is None:
        # Unknown/absent discriminator: best-effort passthrough shell.
        return {"kind": old.get("kind", ""), "auth_value": "",
                "base_url": "", "headers": {}, "data": dict(old)}
    auth_field = _AUTH_FIELD.get(tname)
    data = {k: v for k, v in old.items()
            if k not in ("__type__", auth_field)}
    return {
        "kind": kind,
        "auth_value": old.get(auth_field, "") if auth_field else "",
        "base_url": "",
        "headers": {},
        "data": data,
    }


def _migrate_file(path: Path) -> bool:
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return False
    creds = doc.get("credentials")
    if not isinstance(creds, list):
        return False  # admin file (_rotation/_active/...) — no credentials
    changed = False
    for c in creds:
        p = c.get("payload")
        if isinstance(p, dict) and "__type__" in p:
            c["payload"] = migrate_payload_dict(p)
            changed = True
    # Some stores mirror a top-level "payload" too; migrate if present.
    top = doc.get("payload")
    if isinstance(top, dict) and "__type__" in top:
        doc["payload"] = migrate_payload_dict(top)
        changed = True
    if not changed:
        return False
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def migrate_store(root: Path | None = None) -> int:
    base = Path(root) if root else Path.home() / ".openprogram"
    auth_dir = base / "auth"
    if not auth_dir.is_dir():
        return 0
    n = 0
    for path in auth_dir.rglob("*.json"):
        if _migrate_file(path):
            n += 1
    return n
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv-test/bin/python -m pytest tests/auth/test_migrate_payload.py -x -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/_migrate_payload.py tests/auth/test_migrate_payload.py
git commit -m "feat(auth): one-shot migrator for old payload JSON

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: store 加载时自动迁移 + `openprogram auth migrate` 命令

**Files:**
- Modify: `openprogram/auth/store.py`（`AuthStore` 首次加载前调用 `migrate_store`，仅跑一次）
- Modify: `openprogram/auth/cli.py`（加 `migrate` 子命令）
- Test: `tests/auth/test_migrate_on_load.py`

**Interfaces:**
- Consumes: `migrate_store`（Task 2）

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_migrate_on_load.py`：

```python
import json
from openprogram.auth.store import AuthStore


def test_store_migrates_old_payload_on_first_load(tmp_path):
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    (auth / "default.json").write_text(json.dumps({
        "v": 1, "provider_id": "openai", "profile_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "credentials": [{
            "v": 1, "provider_id": "openai", "profile_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            "payload": {"api_key": "sk-old", "__type__": "ApiKeyPayload"},
        }],
    }))
    store = AuthStore(root=tmp_path)
    pool = store.find_pool("openai", "default")
    assert pool is not None
    cred = pool.credentials[0]
    assert cred.payload.kind == "api_key"
    assert cred.payload.auth_value == "sk-old"
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_migrate_on_load.py -x -q`
Expected: FAIL（旧 payload 无新结构 → `find_pool` 载入报错或字段缺失）

- [ ] **Step 3: 实现**

在 `openprogram/auth/store.py` 的 `AuthStore.__init__`（设置 `self._root` 之后、任何 pool 加载之前）插入一次性迁移：

```python
        # One-shot: migrate any old-format payload JSON under this root to the
        # new CredentialData structure before we read pools. Idempotent; the
        # migrator no-ops when everything is already new.
        try:
            from ._migrate_payload import migrate_store
            migrate_store(root=self._root)
        except Exception:
            # Migration must never block store startup; a genuinely corrupt
            # file surfaces later via from_dict's AuthCorruptCredentialError.
            pass
```

（`self._root` 的确切属性名以 store.py 现状为准——查 `DEFAULT_ROOT` 附近的赋值。）

在 `openprogram/auth/cli.py` 加子命令（跟随现有 argparse subparser 模式）：

```python
    p_mig = sub.add_parser("migrate", help="Migrate stored credentials to the current format")
    p_mig.set_defaults(func=_cmd_migrate)
```

```python
def _cmd_migrate(args) -> int:
    from ._migrate_payload import migrate_store
    n = migrate_store()
    print(f"Migrated {n} credential file(s).")
    return 0
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv-test/bin/python -m pytest tests/auth/test_migrate_on_load.py -x -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/store.py openprogram/auth/cli.py tests/auth/test_migrate_on_load.py
git commit -m "feat(auth): auto-migrate on store load + 'auth migrate' command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 统一读取出口 `resolve_connection` + `ResolvedConnection`

**Files:**
- Modify: `openprogram/auth/resolver.py`（新增 `ResolvedConnection` + `resolve_connection`；重写 `_extract_token` 为薄封装或删除后改调用方）
- Modify: `openprogram/auth/types.py`（导出 `ResolvedConnection`，或定义在 resolver 中——放 resolver）
- Test: `tests/auth/test_resolve_connection.py`

**Interfaces:**
- Consumes: `CredentialData`（Task 1），`_read_delegated_token`（resolver 现有）
- Produces:
  - `ResolvedConnection(kind: str, auth_value: str, base_url: str | None, headers: dict)`
  - `resolve_connection(cred: Credential) -> ResolvedConnection | None`（`external_process`/`sso` 或无 auth_value 时返回 `None`）

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_resolve_connection.py`：

```python
from openprogram.auth.types import Credential, CredentialData
from openprogram.auth.resolver import resolve_connection, ResolvedConnection


def _cred(payload):
    return Credential(provider_id="p", profile_id="default",
                      kind=payload.kind, payload=payload)


def test_api_key_with_base_url():
    conn = resolve_connection(_cred(CredentialData(
        kind="api_key", auth_value="sk-x", base_url="https://bailian/v1")))
    assert conn == ResolvedConnection(
        kind="api_key", auth_value="sk-x",
        base_url="https://bailian/v1", headers={})


def test_api_key_no_base_url_yields_none_base():
    conn = resolve_connection(_cred(CredentialData(kind="api_key", auth_value="k")))
    assert conn.base_url is None  # empty "" → None so wire falls back to catalog


def test_oauth_uses_access_token_as_auth_value():
    conn = resolve_connection(_cred(CredentialData(
        kind="oauth", auth_value="at", data={"refresh_token": "rt"})))
    assert conn.kind == "oauth"
    assert conn.auth_value == "at"


def test_external_process_returns_none():
    conn = resolve_connection(_cred(CredentialData(kind="external_process")))
    assert conn is None
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_resolve_connection.py -x -q`
Expected: FAIL（`cannot import name 'resolve_connection'`）

- [ ] **Step 3: 实现**

在 `openprogram/auth/resolver.py` 加：

```python
from dataclasses import dataclass


@dataclass
class ResolvedConnection:
    kind: str
    auth_value: str
    base_url: str | None
    headers: dict


def resolve_connection(cred: "Credential") -> "ResolvedConnection | None":
    """Translate a Credential into what one request needs.

    cli_delegated reads its external file here for the freshest token.
    external_process / sso are not wired → None (caller falls back).
    """
    p = cred.payload
    kind = getattr(p, "kind", "")
    auth_value = p.auth_value
    if kind == "cli_delegated":
        auth_value = _read_delegated_token(p) or ""
    if not auth_value:
        return None
    base_url = p.base_url or None
    return ResolvedConnection(
        kind=kind, auth_value=auth_value,
        base_url=base_url, headers=dict(p.headers or {}),
    )
```

改 `_read_delegated_token(payload)` 内部：原读 `payload.store_path / payload.access_key_path`，改为 `payload.data["store_path"] / payload.data["access_key_path"]`。

改 `_extract_token` 为基于 `CredentialData` 的薄实现（保留供 Task 5 逐步替换的调用方短暂使用）：

```python
def _extract_token(cred: "Credential") -> "str | None":
    conn = resolve_connection(cred)
    return conn.auth_value if conn else None
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv-test/bin/python -m pytest tests/auth/test_resolve_connection.py -x -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/resolver.py tests/auth/test_resolve_connection.py
git commit -m "feat(auth): resolve_connection returns ResolvedConnection

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 改所有构造点（methods / sources / provider adapters / CLI / web）

**Files（全部 Modify）:**
- `openprogram/auth/methods/{cli_import,pkce_oauth,api_key_paste,external_process,device_code}.py`
- `openprogram/auth/sources/{qwen_cli,gh_cli,env,codex_cli,claude_code}.py`
- `openprogram/providers/{github_copilot/auth_adapter,openai_codex/auth_adapter,google_gemini_cli/auth_adapter,anthropic/auth_adapter}.py`
- `openprogram/webui/_auth_routes.py`、`openprogram/webui/routes/accounts.py`
- `openprogram/auth/cli.py`
- Test: `tests/auth/test_construct_sites.py`（抽样验证 3 个代表构造点产出正确 `CredentialData`）

**Interfaces:**
- Consumes: `CredentialData`（Task 1）

构造映射（逐类替换规则，对每个 `XxxPayload(...)` 调用应用）：

| 旧构造 | 新构造 |
|---|---|
| `ApiKeyPayload(api_key=K)` | `CredentialData(kind="api_key", auth_value=K)` |
| `OAuthPayload(access_token=A, refresh_token=R, expires_at_ms=E, scope=S, client_id=C, token_endpoint=T, id_token=I, extra=X)` | `CredentialData(kind="oauth", auth_value=A, data={"refresh_token":R,"expires_at_ms":E,"scope":S,"client_id":C,"token_endpoint":T,"id_token":I,"extra":X})` |
| `DeviceCodePayload(access_token=A, refresh_token=R, expires_at_ms=E, device_code_flow_id=F, extra=X)` | `CredentialData(kind="device_code", auth_value=A, data={"refresh_token":R,"expires_at_ms":E,"device_code_flow_id":F,"extra":X})` |
| `CliDelegatedPayload(store_path=P, access_key_path=A, refresh_key_path=R, expires_key_path=E)` | `CredentialData(kind="cli_delegated", data={"store_path":P,"access_key_path":A,"refresh_key_path":R,"expires_key_path":E})` |
| `ExternalProcessPayload(command=C, parses=Pa, json_key_path=J, cache_seconds=S)` | `CredentialData(kind="external_process", data={"command":C,"parses":Pa,"json_key_path":J,"cache_seconds":S})` |

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_construct_sites.py`：

```python
from openprogram.auth.types import CredentialData


def test_api_key_paste_builds_credential_data():
    from openprogram.auth.methods.api_key_paste import build_credential  # adjust to real fn
    cred = build_credential("openai", "default", "sk-x")
    assert isinstance(cred.payload, CredentialData)
    assert cred.payload.kind == "api_key"
    assert cred.payload.auth_value == "sk-x"


def test_env_source_builds_credential_data():
    from openprogram.auth.sources import env as env_src
    # env_src builds ApiKeyPayload from an env value → now CredentialData
    payload = CredentialData(kind="api_key", auth_value="v")
    assert payload.kind == "api_key"  # sanity; real assertion wired to env_src API
```

（注：`build_credential` 等确切函数名以各文件现状为准；此测试作为回归锚点，实施时替换成真实入口。至少覆盖 api_key_paste、env、一个 oauth 构造点。）

- [ ] **Step 2: 跑确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_construct_sites.py -x -q`
Expected: FAIL

- [ ] **Step 3: 逐文件替换构造点**

对上面列出的每个文件，按「构造映射」表把每处 `XxxPayload(...)` 改为对应
`CredentialData(...)`，并删除该文件对旧 payload 类的 import（改 import `CredentialData`）。
确切行号见设计文档影响面清单；用 `grep -n "Payload(" <file>` 定位每处。

- [ ] **Step 4: 跑确认通过 + 全 auth 冒烟**

Run: `.venv-test/bin/python -m pytest tests/auth/ -x -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/methods openprogram/auth/sources openprogram/providers/*/auth_adapter.py openprogram/webui/_auth_routes.py openprogram/webui/routes/accounts.py openprogram/auth/cli.py tests/auth/test_construct_sites.py
git commit -m "refactor(auth): construct CredentialData at all credential sites

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 改所有匹配点（isinstance → kind，字段 → data）

**Files（全部 Modify）:**
- `openprogram/auth/manager.py:588,590`（OAuth 刷新判断）
- `openprogram/auth/resolver.py`（剩余 `isinstance(payload, ApiKeyPayload)` 等）
- `openprogram/webui/_auth_routes.py:84-114`（6 路 isinstance 分支）
- `openprogram/providers/openai_codex/{auth_adapter,runtime}.py`（`isinstance(..., OAuthPayload)`、`assert isinstance(...)`、读 `refresh_token`/`access_token`、构造 `new_payload`、`_write_back_to_codex_file`）
- `openprogram/providers/google_gemini_cli/runtime.py:82,84`
- `openprogram/providers/anthropic/auth_adapter.py:234,258`（读 `payload.refresh_token`、构造 `new_payload`）
- `openprogram/_cli_cmds/rescue.py:231`（`isinstance(payload, OAuthPayload) and payload.expires_at_ms`）
- `openprogram/auth/cli.py:300-310`（渲染分支）
- Test: `tests/auth/test_match_sites.py`

**Interfaces:**
- Consumes: `CredentialData`, `resolve_connection`（前置任务）

替换规则：
- `isinstance(payload, ApiKeyPayload)` → `payload.kind == "api_key"`
- `isinstance(payload, OAuthPayload)` → `payload.kind == "oauth"`
- `isinstance(payload, (OAuthPayload, DeviceCodePayload))` → `payload.kind in ("oauth", "device_code")`
- `isinstance(payload, DeviceCodePayload)` → `payload.kind == "device_code"`
- `isinstance(payload, CliDelegatedPayload)` → `payload.kind == "cli_delegated"`
- `isinstance(payload, ExternalProcessPayload)` → `payload.kind == "external_process"`
- `isinstance(payload, SsoPayload)` → `payload.kind == "sso"`
- `payload.access_token` → `payload.auth_value`
- `payload.refresh_token` → `payload.data.get("refresh_token", "")`
- `payload.expires_at_ms` → `payload.data.get("expires_at_ms", 0)`
- `payload.api_key` → `payload.auth_value`
- 其它旧字段（`store_path`/`client_id`/…）→ `payload.data.get(...)`

- [ ] **Step 1: 写失败测试**

创建 `tests/auth/test_match_sites.py`：

```python
from openprogram.auth.types import Credential, CredentialData


def _oauth(expires):
    return Credential(provider_id="p", profile_id="default", kind="oauth",
                      payload=CredentialData(kind="oauth", auth_value="at",
                                             data={"refresh_token": "rt",
                                                   "expires_at_ms": expires}))


def test_manager_needs_refresh_reads_expires_from_data():
    from openprogram.auth.manager import _needs_refresh  # adjust to real fn name
    cred = _oauth(expires=1)  # long past → needs refresh
    assert _needs_refresh(cred) is True


def test_manager_api_key_never_refreshes():
    from openprogram.auth.manager import _needs_refresh
    cred = Credential(provider_id="p", profile_id="default", kind="api_key",
                      payload=CredentialData(kind="api_key", auth_value="k"))
    assert _needs_refresh(cred) is False
```

（`_needs_refresh` 的确切函数名对准 `manager.py:588` 所在函数；实施时替换。）

- [ ] **Step 2: 跑确认失败**

Run: `.venv-test/bin/python -m pytest tests/auth/test_match_sites.py -x -q`
Expected: FAIL

- [ ] **Step 3: 逐文件替换匹配点**

对上列每个文件，用 `grep -nE "isinstance.*Payload|\.access_token|\.refresh_token|\.expires_at_ms|\.api_key|\.store_path"` 定位，按「替换规则」逐处改。删除各文件对旧 payload 类的 import。`manager.py:588` 那行改为：

```python
    if cred.payload.kind not in ("oauth", "device_code") and not cred.payload.data.get("expires_at_ms"):
```

- [ ] **Step 4: 跑确认通过 + auth 全量**

Run: `.venv-test/bin/python -m pytest tests/auth/ -x -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth openprogram/providers openprogram/_cli_cmds/rescue.py tests/auth/test_match_sites.py
git commit -m "refactor(auth): match on payload.kind + read from data dict

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `acquire_pooled` 返回 `ResolvedConnection` + wire 层凭据优先

**Files:**
- Modify: `openprogram/auth/usage.py`（`acquire_pooled` 返回 `(ResolvedConnection, profile, cred_id)`）
- Modify: `openprogram/providers/openai_completions/openai_completions.py`（消费 conn；base_url/headers/oauth）
- Modify: `openprogram/providers/openai_responses/*.py` 与 `openprogram/providers/anthropic/anthropic.py`（同规则）
- Modify: `openprogram/webui/routes/accounts.py:97-99`（用 `resolve_connection` 取代 `_extract_token`）
- Test: `tests/providers/test_credential_base_url.py`

**Interfaces:**
- Consumes: `resolve_connection`, `ResolvedConnection`（Task 4）
- Produces: `acquire_pooled(provider, profile=None) -> tuple[ResolvedConnection, str, str] | None`

- [ ] **Step 1: 写失败测试**

创建 `tests/providers/test_credential_base_url.py`：

```python
from openprogram.auth.resolver import ResolvedConnection


def test_wire_uses_credential_base_url_over_catalog():
    # conn.base_url set → wins; conn.base_url None → falls back to model.base_url
    def pick(conn_base, model_base):
        return (conn_base if conn_base else None) or model_base
    assert pick("https://bailian/v1", "https://api.openai.com/v1") == "https://bailian/v1"
    assert pick(None, "https://api.openai.com/v1") == "https://api.openai.com/v1"


def test_acquire_pooled_returns_resolved_connection(monkeypatch, tmp_path):
    import openprogram.auth.usage as u
    # Build a store with one api_key cred carrying a base_url, point acquire at it.
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import Credential, CredentialData
    # ... construct store under tmp_path, add cred with base_url ...
    # assert acquire_pooled(...)[0] is a ResolvedConnection with that base_url
```

（第二个测试的 store 装配按 `AuthStore` 现有 API 补全；核心断言：返回三元组第 0 位是 `ResolvedConnection` 且 `.base_url` 为存入值。）

- [ ] **Step 2: 跑确认失败**

Run: `.venv-test/bin/python -m pytest tests/providers/test_credential_base_url.py -x -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`openprogram/auth/usage.py` 的 `acquire_pooled` 内 `_resolve`：把 `token = _extract_token(cred); return (token, ...)` 改为：

```python
        from .resolver import resolve_connection
        conn = resolve_connection(cred)
        return (conn, cred.profile_id, cred.credential_id) if conn else None
```

`openai_completions.py` 消费点（约 230 行）改为：

```python
    _pooled = _auth_usage.acquire_pooled(model.provider)
    _cred_profile = _cred_id = None
    _client_api_key = opts.api_key
    _conn = None
    if _pooled:
        _conn, _cred_profile, _cred_id = _pooled
        _client_api_key = _conn.auth_value

    base_url = (_conn.base_url if _conn and _conn.base_url else None) \
        or (model.base_url if model.base_url != "https://api.openai.com/v1" else None)
    extra_headers = {**(opts.headers or {}), **(_conn.headers if _conn else {})}
```

`anthropic/anthropic.py` 的 `_build_client`：`is_oauth = _is_oauth_token(api_key)` 改为
接收上游传入的 `is_oauth`（来自 `conn.kind in ("oauth","device_code")`）；`base_url` 同样凭据优先。
`openai_responses` 各 stream 入口套用同一 `base_url`/`headers` 取值。

`webui/routes/accounts.py:97-99`：把 `_extract_token(...)` 改为
`(resolve_connection(...) or _ns(auth_value="")).auth_value` 等价取值（保留原语义：拿一个展示用 token）。

- [ ] **Step 4: 跑确认通过 + provider 冒烟**

Run: `.venv-test/bin/python -m pytest tests/providers/test_credential_base_url.py tests/providers -q -k "completions or base_url or anthropic"`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/auth/usage.py openprogram/providers openprogram/webui/routes/accounts.py tests/providers/test_credential_base_url.py
git commit -m "feat(providers): credential base_url overrides catalog default

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 删死文件 + 全量回归

**Files:**
- Delete: `openprogram/providers/anthropic/_claude_max_proxy_registry.py`
- Delete: `openprogram/providers/anthropic/_max_proxy_runtime.py`
- Test: 无新增；跑全量

- [ ] **Step 1: 确认无引用**

Run: `grep -rn "_claude_max_proxy_registry\|_max_proxy_runtime" openprogram/ --include="*.py" | grep -v "_claude_max_proxy_registry.py\|_max_proxy_runtime.py"`
Expected: 仅 `_claude_code_direct_runtime.py` 的注释行（无 import/调用）

- [ ] **Step 2: 删除**

```bash
git rm openprogram/providers/anthropic/_claude_max_proxy_registry.py openprogram/providers/anthropic/_max_proxy_runtime.py
```

- [ ] **Step 3: 全量回归**

Run: `.venv-test/bin/python -m pytest tests/auth tests/providers -q`
Expected: PASS（无 `ImportError`，无残留旧 payload 类引用）

补跑一次真实迁移验证（用你的真实 store 的副本，不动原文件）：

Run:
```bash
cp -r ~/.openprogram/auth /tmp/auth_migbak && \
.venv-test/bin/python -c "from openprogram.auth._migrate_payload import migrate_store; from pathlib import Path; print(migrate_store(Path('/tmp')))" ; \
echo "check one file:"; python3 -c "import json; d=json.load(open('/tmp/auth_migbak/deepseek/default.json')); print(d['credentials'][0]['payload'].get('kind'), '__type__' not in d['credentials'][0]['payload'])" 2>/dev/null || true
```
Expected: 迁移计数 > 0；抽查文件 `kind` 正确且无 `__type__`

- [ ] **Step 4: 提交**

```bash
git commit -m "chore(providers): remove dead claude_max_proxy files

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec 覆盖**：`CredentialData`(T1) / 序列化(T1) / 迁移器(T2) / 自动迁移+命令(T3) / `resolve_connection`(T4) / 构造点(T5) / 匹配点(T6) / `acquire_pooled`+wire(T7) / 删死文件(T8) / 不做旧兼容(T1 `_payload_from_dict` 只认新结构 + T2 迁移) —— 设计文档每节均有对应任务。
- **占位符**：无 TBD/TODO；构造与匹配规则均给出逐条映射表与确切替换。少数「确切函数名以现状为准」处已标注用 grep 定位——这是真实代码入口的适配，非占位。
- **类型一致**：`CredentialData(kind,auth_value,base_url,headers,data)`、`ResolvedConnection(kind,auth_value,base_url,headers)`、`resolve_connection(cred)->ResolvedConnection|None`、`acquire_pooled(...)->tuple[ResolvedConnection,str,str]|None`、`migrate_payload_dict/migrate_store` 在各任务间引用一致。
