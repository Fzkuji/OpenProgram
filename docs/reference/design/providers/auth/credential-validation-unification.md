# Credential validation

Every surface that asks "is this provider key valid?" — save, the verify button,
the connectivity check, the CLI, TUI status rows, the setup wizard — calls one
entry point. Adding a provider once makes it validate everywhere.

## 1. Two questions, not one

`configured` and `valid` are different facts. A key can be present in the
environment and rejected by the provider; a key can be accepted and still have
no balance behind it; a key can be fine while the specific model named is
temporarily down. Status rows that show a green dot for any present key conflate
the first two, and a validator that answers only yes/no cannot express the rest.

The design separates them: a cheap offline presence check answers `configured`,
one auth-endpoint call answers `valid`, and a closed status taxonomy carries the
distinctions between rejection, no balance, and model unavailability.

**Validating a credential never invokes a model.** An auth probe costs one GET
and zero tokens; running inference to check a key spends completions to learn
something an auth endpoint already knows.

This is deliberately not lazy-only validation. OpenClaw and opencode validate at
first model use and have no save-time probe; OpenProgram keeps a save-time
green/red indicator, so it keeps an explicit cheap auth probe — the mechanism
both references describe for exactly that indicator.

Out of scope: this is not a usage or quota dashboard. Balance is reported only
where a provider exposes it cheaply, such as OpenRouter's `/key`.

## 2. Prior art

**OpenClaw** — the UI never validates. It calls one gateway RPC,
`models.authStatus` (`ui/src/ui/controllers/model-auth-status.ts`), returning a
`{ts, providers[]}` snapshot cached server-side for 60 s with a `refresh: true`
bypass. Server-side (`src/gateway/server-methods/models-auth-status.ts`,
`src/infra/provider-usage.*`) it validates off usage endpoints rather than a
model call: `401/403` on the usage/quota endpoint means the token expired,
anything else 4xx/5xx is reported as "HTTP n". Credential health is a separate
rollup (`src/agents/auth-health.ts`): `ok | expiring | expired | missing |
static`, where an OAuth profile counts as healthy if a refresh token is present
even when the access token has expired. Results are secret-redacted — only
`profileId/type/status/expiry`, never the token.

**opencode** — stores the key on `auth login` with no live check; the first real
request surfaces a bad key. The catalog comes from models.dev, decoupled from
credentials. A single `provider/error.ts` maps upstream error shapes to
user-facing remediation strings.

Adopted here: the status taxonomy, the 60 s cache with force-refresh, secret
redaction, the layering of cheap presence against one-network-call auth against
model reachability, and the centralized status-to-message mapper.

## 3. The entry point

`openprogram/webui/_model_catalog/credentials.py`, re-exported from
`_model_catalog/__init__.py`.

```python
def validate_credential(
    provider_id: str,
    *,
    api_key: str | None = None,  # explicit (verify-before-persist); None => resolve from env+config+AuthManager
    model: str | None = None,    # set ONLY to additionally check layer-2 model reachability
    timeout: float = 15.0,
    use_cache: bool = True,      # 60s TTL, like OpenClaw models.authStatus
) -> CredentialResult
```

```python
@dataclass
class CredentialResult:
    provider_id: str
    status: Literal["valid", "invalid_credential", "valid_no_balance",
                    "valid_model_unavailable", "missing", "not_applicable", "unknown"]
    ok: bool          # status in {valid, valid_no_balance, valid_model_unavailable}
    kind: str         # probe that ran: openai_bearer | openrouter_key | anthropic_native | anthropic_compat | google_query | oauth | cloud | none
    via: str | None   # "GET /models", "GET /key", "AuthManager", "POST /chat/completions(model)"
    http_status: int | None
    latency_ms: int | None
    model: str | None # echoed when layer 2 ran
    detail: str | None  # human-readable, secret-free remediation
    cached: bool
```

Thin wrappers delegate to it, preserving their existing shapes:

- `routes/config.py::_validate_api_key(env_var, value)` maps env_var to
  provider_id, calls `validate_credential(pid, api_key=value)`, and returns the
  `error|None` its caller expects.
- `test_provider.py::test_provider(pid, model)` calls
  `validate_credential(pid, model=model)` and adapts to the
  `{ok, latency_ms, model, note, error}` shape the React `Connectivity`
  component reads.
- `provider_auth_status(provider_ids=None, refresh=False)` is the batch helper
  for status rows, mirroring `models.authStatus` (60 s cache, refresh bypass).

## 4. Three layers

| Layer | Question | Cost | When |
| --- | --- | --- | --- |
| 0 — presence/format | is there a credential, is it not the masked placeholder, is the OAuth token structurally unexpired? | offline, µs | always (powers cheap status rows) |
| 1 — auth acceptance | did the provider's auth endpoint accept the key? | one GET, 0 tokens | the canonical green/red check |
| 2 — model reachability | can I reach *this named* model right now? | one inference ping | only when `model` is passed |

Layer 2 exists because "the key is good but this model is down" is a real and
distinct outcome: `429/5xx` or OpenRouter's "no endpoints" resolve to
`valid_model_unavailable`, while a genuine bad request is an error.

## 5. Probe per provider KIND

| KIND | Providers | Layer-1 probe |
| --- | --- | --- |
| `openai_bearer` | openai, deepseek, groq, cerebras, mistral, huggingface, kimi-coding, vercel-ai-gateway, xai, zai, opencode-api | `GET {base}/models`, `Authorization: Bearer` |
| `openrouter_key` | openrouter | `GET {base}/key` (`/models` is **public** there) — body also exposes balance |
| `anthropic_native` | anthropic | `GET https://api.anthropic.com/v1/models`, `x-api-key` + `anthropic-version: 2023-06-01` (Bearer is ignored) |
| `anthropic_compat` | minimax, minimax-cn (any registry provider with `api='anthropic-messages'` that isn't native `anthropic`) | `GET {base}/v1/models`, `x-api-key` + `anthropic-version` — same probe as native but against the provider's OWN base_url (e.g. `https://api.minimaxi.com/anthropic`). The `openai_bearer` `GET {base}/models` 404s on these hosts and would brand a good key `invalid_credential`. |
| `google_query` | google | `GET https://generativelanguage.googleapis.com/v1beta/models?key=…&pageSize=1` |
| `oauth` | openai-codex, gemini-subscription, github-copilot, claude-code, opencode | `AuthManager.acquire_sync(pid).status` (`fresh`→valid, `needs_reauth`→invalid); no network beyond an optional token refresh |
| `cloud` | amazon-bedrock, google-vertex, azure-openai-responses | `not_applicable` for the generic probe (SigV4 / ADC / deployment-keyed) until a native list-call is added |

## 6. Status-code interpretation

One interpreter maps outcomes to statuses:

```
200                                          -> valid
401 / 403                                    -> invalid_credential
402 / body~insufficient.?quota|balance       -> valid_no_balance
429 / 5xx / "no endpoints" / "data policy"   -> valid_model_unavailable   (layer 2 only)
transport error / ambiguous                  -> unknown
no credential resolvable                      -> missing
provider has no key concept                  -> not_applicable
```

`valid_no_balance` is only cheaply detectable for OpenRouter (via `/key`) and
through a layer-2 `402`. Elsewhere a `200` proves auth but not balance, so the
result is plain `valid` until the first real call surfaces `insufficient_quota`.

## 7. Caching

A 60 s in-process TTL keyed by `provider_id` plus whether a model was named.
`use_cache=False` / `refresh=True` bypasses it. Results carry `cached: bool`.
The secret is never stored and never returned.

## 8. How each surface uses it

- **Save** (`POST /api/config`): persist first, so a slow or offline provider
  never blocks saving, then fire `validate_credential(pid, api_key=val)` and let
  the row flip from `Checking…` to green/amber/red/grey. Layer 1 only — saving a
  key never spends a completion.
- **Verify button** (`POST /api/config/verify`): the same call with an explicit
  `api_key`, synchronous, showing status plus `detail`.
- **Connectivity check** (the React component behind `/test` → `/validate`):
  layer 1 by default; a "Test a model" affordance passes `{model}` for layer 2.
  The "Model X is unavailable right now" note is how `valid_model_unavailable`
  renders.
- **Status rows** (`config_schema.get_settings`, TUI, the web Providers tab):
  two columns — `Configured` (layer-0 presence, instant) and `Validated`
  (cached layer 1, 60 s). Every row carries a `/test` action, so the TUI reaches
  the same probe as the web button. OAuth rows render `fresh` / `expiring` /
  `needs_reauth` distinctly.

Remediation copy is centralized, in the style of opencode's `error.ts`:
`valid_no_balance` → "Key works — account has no balance. Add funds at <doc>.";
`invalid_credential` → "Key rejected (401). Re-check the key or re-login.";
`unknown` → "Couldn't reach <provider> to verify. Saved anyway; will validate
on first use."; OAuth `needs_reauth` → "Login expired — run `openprogram
providers login <pid>`."

## 9. Adding a provider

Declare its probe KIND in `credentials.py::_kind_for`; the default
`openai_bearer` needs no declaration at all. That single line wires the provider
into save-verify, the connectivity button, status rows, and the CLI/TUI at once.

**Anthropic-wire third parties** (MiniMax and friends) are detected
automatically: `_kind_for` returns `anthropic_compat` for any provider whose
registry `api` is `anthropic-messages` and which isn't native `anthropic`. Three
places must agree, or the provider half-works:

- `_kind_for` → `anthropic_compat`, so the credential probe hits
  `{base}/v1/models`;
- `_model_catalog/providers.py::_PROVIDER_DEFAULT_API` must stamp
  `anthropic-messages`, so fetched and custom rows route to the right stream
  function rather than `POST /chat/completions` — matching `models_generated`;
- `_model_catalog/fetchers` routes `anthropic-messages` providers to the
  base_url-aware `_fetch_anthropic`, because the OpenAI-compatible
  `GET {base}/models` 404s on a `/anthropic` host.

`test_model_fetch_routing.py` pins the api stamp to `models_generated` so the
three cannot drift apart.

## 10. Test matrix

Outcome × KIND: `200→valid`, `401→invalid_credential`,
`402/insufficient_quota→valid_no_balance`, OpenRouter's public `/models` **not**
mistaken for valid (the probe must use `/key`), Anthropic without
`anthropic-version`, OAuth `needs_reauth`, layer-2 `429→valid_model_unavailable`,
offline→`unknown`, no key→`missing`.

## Implementation status

`credentials.py` holds `CredentialResult`, the status enum, and the per-KIND
probe registry, with `validate_credential()` running layers 0→1→(2 when a model
is named) plus the 60 s cache and `provider_auth_status()`. `test_provider()`
and `_validate_api_key()` delegate to it, which is what closes the validation
gap for the providers that previously had no probe at all;
`POST /api/providers/{name}/validate` and `GET /api/providers/auth-status` are
served, with `/test` aliasing `/validate`.

Still to land: fetchers calling `validate_credential(pid)` once before
dispatching instead of reimplementing key-presence checks; `check_providers()`
and `_is_configured()` exposing a cached `validated` alongside cheap presence,
with `config_schema.get_settings()` reading both; and bedrock/vertex reporting
`not_applicable` rather than a placeholder-driven green.

Open points:

- Whether a single-key save triggers layer 1 automatically or defers to an
  explicit Verify click on bulk save, which would need throttling to avoid a
  burst of probes.
- Anthropic OAuth (`ANTHROPIC_OAUTH_TOKEN`) needs `Authorization: Bearer` plus
  `anthropic-beta: oauth-…` on the same `/v1/models` probe; either confirm the
  beta value or route it through the AuthManager path.
- openai-codex has no auth-only listing endpoint (the ChatGPT backend 403s), so
  its only end-to-end probe is a layer-2 `/responses` ping. The default check
  relies on AuthManager `Credential.status`, which is structural rather than
  end-to-end.
