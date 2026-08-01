# Credential Connection Unification

Six payload classes collapse into one `CredentialData` (shared fields plus a
`data` dict), and a single `resolve_connection()` hands the wire layer
everything one request needs: auth value, base URL, headers, and kind. A key can
therefore carry its own base URL, and stored credentials are migrated once to
the new shape.

The design source is
[`../providers/auth/credential-connection-unification.md`](../providers/auth/credential-connection-unification.md).

## Data model

`CredentialData` sits where `Credential.payload` used to hold one of
`ApiKeyPayload`, `OAuthPayload`, `DeviceCodePayload`, `CliDelegatedPayload`,
`ExternalProcessPayload`, or `SsoPayload`:

```python
@dataclass
class CredentialData:
    kind: str
    auth_value: str = ""
    base_url: str = ""
    headers: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
```

The common fields answer "what do we send" uniformly across every credential
kind. `data` holds whatever is specific to one kind — refresh tokens, external
file paths, device-code flow ids.

Serialization is flat — `{kind, auth_value, base_url, headers, data}` — with no
`__type__` discriminator, since `kind` is now a real field rather than a class
identity. `_payload_from_dict` accepts only this structure; there is no runtime
compatibility path for the old six-class JSON.

`Credential.metadata` and display information (email, name, org) stay where they
are. They belong to neither `CredentialData` nor `ResolvedConnection`.

### Kind matching replaces isinstance

With one class, type tests become value tests:

| Old | New |
|---|---|
| `isinstance(payload, ApiKeyPayload)` | `payload.kind == "api_key"` |
| `isinstance(payload, OAuthPayload)` | `payload.kind == "oauth"` |
| `isinstance(payload, (OAuthPayload, DeviceCodePayload))` | `payload.kind in ("oauth", "device_code")` |
| `isinstance(payload, DeviceCodePayload)` | `payload.kind == "device_code"` |
| `isinstance(payload, CliDelegatedPayload)` | `payload.kind == "cli_delegated"` |
| `isinstance(payload, ExternalProcessPayload)` | `payload.kind == "external_process"` |
| `isinstance(payload, SsoPayload)` | `payload.kind == "sso"` |
| `payload.access_token` / `payload.api_key` | `payload.auth_value` |
| `payload.refresh_token` | `payload.data.get("refresh_token", "")` |
| `payload.expires_at_ms` | `payload.data.get("expires_at_ms", 0)` |
| other old fields (`store_path`, `client_id`, …) | `payload.data.get(...)` |

### Construction mapping

| Old constructor | New constructor |
|---|---|
| `ApiKeyPayload(api_key=K)` | `CredentialData(kind="api_key", auth_value=K)` |
| `OAuthPayload(access_token=A, refresh_token=R, expires_at_ms=E, scope=S, client_id=C, token_endpoint=T, id_token=I, extra=X)` | `CredentialData(kind="oauth", auth_value=A, data={"refresh_token":R,"expires_at_ms":E,"scope":S,"client_id":C,"token_endpoint":T,"id_token":I,"extra":X})` |
| `DeviceCodePayload(access_token=A, refresh_token=R, expires_at_ms=E, device_code_flow_id=F, extra=X)` | `CredentialData(kind="device_code", auth_value=A, data={"refresh_token":R,"expires_at_ms":E,"device_code_flow_id":F,"extra":X})` |
| `CliDelegatedPayload(store_path=P, access_key_path=A, refresh_key_path=R, expires_key_path=E)` | `CredentialData(kind="cli_delegated", data={"store_path":P,"access_key_path":A,"refresh_key_path":R,"expires_key_path":E})` |
| `ExternalProcessPayload(command=C, parses=Pa, json_key_path=J, cache_seconds=S)` | `CredentialData(kind="external_process", data={"command":C,"parses":Pa,"json_key_path":J,"cache_seconds":S})` |

## Resolution: one read exit

`_extract_token` (returning a bare `str`) is replaced by `resolve_connection`,
which returns everything a request needs:

```python
@dataclass
class ResolvedConnection:
    kind: str
    auth_value: str
    base_url: str | None
    headers: dict


def resolve_connection(cred: Credential) -> ResolvedConnection | None:
    ...
```

Rules the resolver enforces:

- `cli_delegated` reads its external file at resolve time, so the token is
  always the freshest one on disk.
- `external_process` and `sso` are not wired to a request, and a credential with
  no auth value cannot make one — both yield `None`, and the caller falls back.
- An empty `base_url` becomes `None`, which is what lets the wire layer
  distinguish "this credential specifies no endpoint" from "this credential
  specifies an endpoint" and fall back accordingly.

`acquire_pooled(provider, profile=None)` returns
`tuple[ResolvedConnection, str, str] | None` rather than a token triple, so the
connection information reaches the wire layer intact.

### Credential first, catalogue as fallback

At the wire layer the credential wins and the model catalogue fills the gaps:

```python
base_url = (conn.base_url if conn and conn.base_url else None) or model.base_url
headers  = {**(opts.headers or {}), **(conn.headers if conn else {})}
```

This is what allows one API key to point at its own endpoint while every other
key for the same provider keeps the catalogue default. OAuth detection follows
the same route: instead of sniffing the token string, the wire layer receives
`is_oauth` derived from `conn.kind in ("oauth", "device_code")`.

## Stored-credential migration

Old format is not supported at runtime, so conversion happens once, out of band.
`migrate_payload_dict(old) -> dict` maps one old payload dict (carrying
`__type__`) to the flat new shape, and is idempotent — a payload already in the
new shape is returned unchanged. `migrate_store(root) -> int` walks
`<root>/auth/<provider>/<profile>.json`, rewrites each file atomically, and
returns the number of files changed. Administrative files (`_rotation`,
`_active`, `_disabled`, `_order.json`) have no `credentials` list and are
skipped.

The migration runs automatically on first store load, before any pool is read,
and is also exposed as `openprogram auth migrate`. A failure there must never
block store startup: a genuinely corrupt file surfaces later through
`from_dict`'s `AuthCorruptCredentialError`.

`CREDENTIAL_SCHEMA_VERSION` increments with this structural change. An old `v`
value triggers migration rather than a corruption error.

## Appendix: Implementation Status

Designed, not yet landed. The intended order is: introduce `CredentialData` and
its flat serialization; add the one-shot migrator; wire the migrator into store
load and add the `auth migrate` command; add `ResolvedConnection` and
`resolve_connection`, keeping `_extract_token` briefly as a thin wrapper over
it; convert every construction site (auth methods, auth sources, provider auth
adapters, web routes, CLI) using the construction mapping above; convert every
match site using the kind-matching table; change `acquire_pooled` to return a
`ResolvedConnection` and make the wire layer prefer credential base URL and
headers; then delete the dead `_claude_max_proxy_registry.py` and
`_max_proxy_runtime.py`.

Each step is test-first, and the auth and provider suites are the regression
gate. Verification of the migrator should be run against a *copy* of a real
credential store, checking that the migrated files carry `kind` and no longer
carry `__type__`.
