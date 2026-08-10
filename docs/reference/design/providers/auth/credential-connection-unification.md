# Credential connection unification (one Credential, one resolution point)

Everything a request needs lives in **one** credential structure, and **one**
resolution point hands it to the wire layer. A credential is never squeezed down
to a bare `api_key` string along the way, `base_url` no longer belongs to the
model alone, and "is this OAuth?" is not guessed from a token prefix.

As a result, a single key can carry its own `base_url`: under the same
`openai-completions` protocol, one key hits the official endpoint while another
hits Alibaba Cloud Bailian's `compatible-mode/v1`, with no need to pre-build a
model list for every compatible endpoint.

This document makes [unified-auth-storage.md](./unified-auth-storage.md)
concrete. That document sets the overall direction of "one store, one login
flow, unified across interfaces"; this one narrows in on the **payload
structure layer** only, and does not touch the login registry, storage paths, or
refresh ownership.

---

## A request needs two things

Sending an LLM request requires knowing **which address to hit (base_url)** and
**which auth value to use (key/token)**. When these two travel on separate
tracks, a credential can only contribute half of the answer:

- **The base_url track**: every model in the model list hardcodes a `base_url` →
  loaded into `Model` → the wire layer reads `model.base_url` directly. It is
  bound to the model throughout and never passes through the credential.
- **The auth value track**: AuthStore's `CredentialPool` stores credentials →
  the wire layer calls `auth.usage.acquire_pooled(provider)` → internally
  `mgr.acquire_sync()` obtains the complete `Credential` object.

If the last step squeezes `Credential` into a str before handing it over
(pulling one string out of each of the 6 payloads: `ApiKeyPayload→api_key`,
`OAuth/DeviceCode→access_token`, `CliDelegated→read external file`,
`credential_process→run the helper`, `sso→unsupported`), then the base_url, headers, and kind the
credential knows about are all lost. Two consequences follow: even when a
credential stores a `base_url`, the wire layer cannot read it; and the anthropic
wire can only guess whether a token is OAuth via `"sk-ant-oat" in key`, because
the kind information is gone too.

So the resolution point hands over not a string, but the complete connection
information a request needs.

## One credential structure

`openprogram/auth/types.py` uses **one** `CredentialData` (carried in the
`Credential.payload` slot) to cover every authentication method, instead of a
separate subtype per method. The differences in how much information each method
carries are real — simple authentication carries little, complex authentication
carries more — so the shared fields are fixed and the varying part goes into a
`data` dictionary:

```python
@dataclass
class CredentialData:
    # -- Shared fields: every auth method answers "what does a request use"
    #    in the same place --
    kind: str                     # "api_key" | "oauth" | "device_code" |
                                  # "cli_delegated" | "credential_process" | "sso"
    auth_value: str = ""          # the auth value that ends up in Authorization/x-api-key:
                                  #   api_key kinds -> the key itself
                                  #   oauth/device -> access_token
                                  #   cli_delegated -> empty (read from an external file at
                                  #                    runtime, see data)
    base_url: str = ""            # endpoint specified by this credential; empty => use the
                                  # list default (see resolution rules)
    headers: dict = field(default_factory=dict)   # extra request headers carried by this
                                                  # credential; usually empty

    # -- Variation container: everything specific to one auth method goes here --
    data: dict = field(default_factory=dict)
```

`data` holds the fields private to each kind rather than reserving them as
formal fields, so an api_key credential does not carry a pile of empty oauth
fields:

| kind | `auth_value` | what goes in `data` |
|---|---|---|
| `api_key` | the key itself | (usually empty) |
| `oauth` | access_token | `refresh_token` / `expires_at_ms` / `client_id` / `token_endpoint` / `scope` / `id_token` |
| `device_code` | access_token | `refresh_token` / `expires_at_ms` / `device_code_flow_id` |
| `cli_delegated` | empty | `store_path` / `access_key_path` / `refresh_key_path` / `expires_key_path` |
| `credential_process` | empty | `command` / `parses` / `json_key_path` / `cache_seconds` |
| `sso` | empty | `broker` / `subject` |

**Display information stays out of the payload.** Account email, display name,
org id and the like remain in `Credential.metadata` (the UI renders it, the
manager does not interpret it). They do not affect how a request is sent; they
are consumed by the UI and usage statistics. Mixing them into connection
information only re-entangles "what to use" with "what to show".

## One resolution point

A single function translates a credential into the connection information the
wire layer actually uses:

```python
@dataclass
class ResolvedConnection:
    kind: str                     # credential type -- the wire no longer guesses OAuth
                                  # from a key prefix
    auth_value: str               # the resolved auth value (cli_delegated is already
                                  # filled in by reading the external file)
    base_url: str | None          # endpoint specified by the credential; None => let the
                                  # wire fall back to model.base_url
    headers: dict                 # extra request headers carried by the credential
                                  # (empty by default)

def resolve_connection(cred: Credential) -> ResolvedConnection | None:
    """Translate one Credential into the connection information for a request.
    cli_delegated reads the external file here to obtain the token (preserving its
    "the external CLI is authoritative" semantics).
    credential_process runs its helper here, reusing a token cached for
    cache_seconds; a helper failure raises AuthCredentialProcessError, which the
    resolver ladder re-raises instead of falling through, because a helper the
    user configured deliberately must not be silently replaced by another layer.
    sso raises AuthConfigError -- the kind is reserved and no flow implements it."""
```

`auth.usage.acquire_pooled` returns `(conn: ResolvedConnection, profile,
cred_id)` instead of `(token: str, profile, cred_id)`, so the connection
information the credential knows about is not lost midway. It already holds the
complete `cred` internally; only the final string extraction is replaced by
`resolve_connection`.

## Wire layer: credential first, model list as fallback

Every wire (`openai_completions` / `openai_responses` / `anthropic`) reads
values by the same rules:

```python
conn = <ResolvedConnection from acquire_pooled, or None>
api_key  = conn.auth_value if conn else opts.api_key
base_url = (conn.base_url if conn and conn.base_url else None) or model.base_url
headers  = { **(model.headers or {}), **(conn.headers if conn else {}), **(opts.headers or {}) }
is_oauth = bool(conn and conn.kind in ("oauth", "device_code"))
```

**The rule in one sentence: if the credential carries a `base_url`, use the
credential's; otherwise use the model's default.** So official openai / deepseek
/ anthropic keep working as before (their credentials leave base_url empty),
while connecting to Bailian only requires filling in
`base_url = https://…maas.aliyuncs.com/compatible-mode/v1` when storing the key.
That credential's requests then go to Bailian, and nothing else is affected.

```
Credential(CredentialData{auth_value, base_url, headers, kind, data})
      │
      └─(resolve_connection)─► ResolvedConnection{kind, auth_value, base_url, headers}
            │
            └─ wire: base_url = conn.base_url or model.base_url  ─► AsyncClient(...)
                     the model list's base_url is only the fallback default
                     when conn.base_url is empty
```

## Scope of impact

**Changes:**
- `openprogram/auth/types.py`: the 6 payload classes merge into one
  `CredentialData`; `_payload_to_dict`/`_payload_from_dict` simplify accordingly
  into single-type serialization (`kind` + flat fields + `data`).
- `openprogram/auth/resolver.py`: `resolve_connection` replaces the path that
  returned a bare str.
- `openprogram/auth/usage.py`: `acquire_pooled` returns a `ResolvedConnection`
  triple.
- The wires (`openai_completions.py` / `openai_responses/*` /
  `anthropic/anthropic.py`): handle auth, base_url, headers, and the oauth
  decision by the rules above.
- Places that read specific payload fields (the manager's OAuth refresh reading
  `refresh_token`/`expires`, delegated reading external file paths, and so on):
  switch to reading from `CredentialData.data[...]`.

**Retained:**
- The `base_url` in the model list stays, as the default when a credential does
  not specify one. Built-in providers work out of the box as before.
- `Credential.metadata` and display information such as the OAuth email are read
  by the UI and usage statistics as before.
- The login registry, storage paths, refresh ownership, and cross-interface
  unification are out of scope here.

## Old format: one-time migration

The runtime (`_payload_from_dict` / `resolve_connection`) recognizes only the
new structure. Reading old 6-payload JSON (carrying the `__type__`
discriminator) is an error, not a fallback path. Existing credentials are moved
to the new structure by a one-time migrator, and users do not need to log in
again.

`openprogram/auth/_migrate_payload.py` converts the old `payload` in each
`~/.openprogram/auth/<provider>/<profile>.json` in place into the new
`CredentialData` and writes it back atomically (reusing the store's
write→fsync→replace). Conversion rules:

| old `__type__` | → new `kind` | `auth_value` | `data` (remaining fields moved over wholesale) |
|---|---|---|---|
| `ApiKeyPayload` | `api_key` | `api_key` | `{}` (`base_url`/`headers` empty if absent in the old form) |
| `OAuthPayload` | `oauth` | `access_token` | `refresh_token` `expires_at_ms` `scope` `client_id` `token_endpoint` `id_token` `extra` |
| `DeviceCodePayload` | `device_code` | `access_token` | `refresh_token` `expires_at_ms` `device_code_flow_id` |
| `CliDelegatedPayload` | `cli_delegated` | `""` | `store_path` `access_key_path` `refresh_key_path` `expires_key_path` |
| `ExternalProcessPayload` | `credential_process` | `""` | `command` `parses` `json_key_path` `cache_seconds` |
| `SsoPayload` | `sso` | `""` | `broker` `subject` |

The migrator is idempotent: a payload already in the new structure (top-level
`kind` field present, no `__type__`) is skipped. It runs automatically on the
first `AuthStore` load, and can also be triggered manually with
`openprogram auth migrate`. Management files that contain no `credentials`, such
as `_rotation/_active/_disabled/_order.json`, have no payload, and the migrator
skips them.

## Tests

- `resolve_connection`: one case per kind — api_key with and without base_url,
  oauth yielding access_token, cli_delegated reading the external file live,
  credential_process running a fake helper script (json and text parsing, the
  cache window de-duplicating forks, and every failure mode raising rather than
  falling through), sso raising.
- Serialization round trip: `CredentialData` → dict → `CredentialData` with
  identical fields (including `data`).
- Wire value rules: a credential with base_url wins, without one
  `model.base_url` is used; with `kind=oauth`, `is_oauth` is true without
  depending on any prefix.
- End to end: store an `api_key` credential with `base_url=Bailian`, run
  `openai-completions`, and verify the client's base_url points at Bailian while
  the official openai credential still points at the default endpoint.
- Migrator: one old `ApiKeyPayload/OAuthPayload/CliDelegatedPayload` JSON each,
  with correct `kind`/`auth_value`/`data` and no `__type__` after migration;
  idempotent skip for files already in the new structure; management files
  (`_rotation.json` and friends) left untouched.
