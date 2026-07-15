# Credential-validation unification

Status: **in progress** · Owner: providers/webui · Last updated: 2026-06-03

## 1. Problem

"Is this provider key valid?" is answered five different ways in the codebase,
and most of them are wrong:

| Surface | File · symbol | What it does | Verdict |
| --- | --- | --- | --- |
| Connectivity button | `webui/_model_catalog/test_provider.py::test_provider` | auth-only `GET /key` (OpenRouter) / `GET /models`, inference-ping fallback | the only correct one — but reachable from one surface |
| Save-key verify | `webui/routes/config.py::_validate_api_key` | per-provider branch for **OpenAI / Anthropic / Google only**; `return None` (no-op) for ~17 others | silently passes invalid keys for most providers |
| Save key | `webui/routes/config.py::save_config` | character check only, then persist | **no validation at all** |
| Model fetch | `_model_catalog/fetchers/*` | each fetcher re-implements its own key check + 401 handling | duplicated, inconsistent |
| Status rows | `providers/registry.py::check_providers`, `_model_catalog/providers.py::_is_configured` | env-var / file **presence** | conflates *configured* (present) with *valid* (accepted) |

Three concrete defects fall out of this:

1. **The ~17-provider silent no-op.** Paste a garbage OpenRouter / DeepSeek /
   xAI / Groq / Mistral / … key and `_validate_api_key` returns `None` →
   "valid". Validation only existed for OpenAI/Anthropic/Google.
2. **Validation spends completions.** The Anthropic branch did
   `client.messages.create(...)` and the Google branch looped
   `generate_content` over three models — i.e. it ran *inference* to check a
   *key*. The connectivity button did the same until it was refactored to hit
   an auth endpoint. Validating a key should never invoke a model.
3. **`configured` ≠ `valid`.** TUI/web status rows show a green dot for any
   present key, valid or not, and the TUI has no way to actually test one.

## 2. Goals & non-goals

**Goals**

- Validate a *credential* without invoking a *model*.
- One entry point that every surface (save, verify button, connectivity check,
  CLI, TUI status rows, setup wizard) calls — add a provider once, it validates
  everywhere.
- A closed status taxonomy that separates "key rejected" from "key fine, no
  balance" from "key fine, that model is down right now".

**Non-goals**

- Not a usage/quota dashboard (balance is reported only where a provider
  exposes it cheaply, e.g. OpenRouter `/key`).
- Not lazy-only. OpenClaw and opencode validate lazily at first model use and
  have no save-time probe; OpenProgram keeps a save-time green/red indicator,
  so we keep an explicit cheap auth probe — the thing both references say to
  build *if* you want that indicator.

## 3. Prior art

**OpenClaw** (`/Users/fzkuji/Documents/Agent-Infrastructure/references/openclaw`)

- The UI never validates. It calls one gateway RPC, `models.authStatus`
  (`ui/src/ui/controllers/model-auth-status.ts`), which returns a snapshot of
  `{ts, providers[]}` and is **server-cached for 60 s** with a `refresh: true`
  bypass after a user-initiated refresh.
- Server-side (`src/gateway/server-methods/models-auth-status.ts`,
  `src/infra/provider-usage.*`) it validates **parasitically off usage
  endpoints**, not a model call: a `401/403` on the provider's usage/quota
  endpoint = "token expired", anything else 4xx/5xx = "HTTP n".
- Credential health is a separate rollup (`src/agents/auth-health.ts`):
  `ok | expiring | expired | missing | static`. OAuth profiles count as healthy
  if a refresh token is present even when the access token is expired.
- Results are **secret-redacted** — only `profileId/type/status/expiry`, never
  the token.

**opencode** (sst/opencode)

- Stores the key on `auth login` **without** a live check (store-then-fail-
  lazily); the first real request surfaces a bad key. Catalog comes from
  models.dev, decoupled from credentials. A single `provider/error.ts` maps
  upstream error shapes → user-facing remediation strings.

**What we adopt**: the status taxonomy, the 60 s cache + force-refresh, secret
redaction, the layering of cheap-presence vs one-network-call auth vs
model-reachability, and the centralized status→message mapper. **Where we
differ**: we keep an explicit save-time auth probe (layer 1) because we want the
indicator neither reference has.

## 4. The unified entry point

New module `openprogram/webui/_model_catalog/credentials.py`, re-exported from
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

Thin wrappers delegate to it (back-compatible shapes preserved):

- `routes/config.py::_validate_api_key(env_var, value)` → map env_var →
  provider_id, `validate_credential(pid, api_key=value)`, return the old
  `error|None`.
- `test_provider.py::test_provider(pid, model)` →
  `validate_credential(pid, model=model)` adapted to the legacy
  `{ok, latency_ms, model, note, error}` the React `Connectivity` component
  reads.
- `provider_auth_status(provider_ids=None, refresh=False)` — batch helper for
  status rows, mirrors `models.authStatus` (60 s cache, refresh bypass).

## 5. Layered validation

| Layer | Question | Cost | When |
| --- | --- | --- | --- |
| 0 — presence/format | is there a credential, is it not the masked placeholder, is the OAuth token structurally unexpired? | offline, µs | always (powers cheap status rows) |
| 1 — auth acceptance | did the provider's auth endpoint accept the key? | one GET, 0 tokens | the canonical green/red check |
| 2 — model reachability | can I reach *this named* model right now? | one inference ping | only when `model` is passed |

Layer 2 is exactly today's behaviour: `429/5xx` / OpenRouter "no endpoints" →
`valid_model_unavailable` (key proven good, model down), real bad request →
error.

## 6. Per-provider-KIND probe table

| KIND | Providers | Layer-1 probe |
| --- | --- | --- |
| `openai_bearer` | openai, deepseek, groq, cerebras, mistral, huggingface, kimi-coding, vercel-ai-gateway, xai, zai, opencode-api | `GET {base}/models`, `Authorization: Bearer` |
| `openrouter_key` | openrouter | `GET {base}/key` (`/models` is **public** there) — body also exposes balance |
| `anthropic_native` | anthropic | `GET https://api.anthropic.com/v1/models`, `x-api-key` + `anthropic-version: 2023-06-01` (Bearer is ignored) |
| `anthropic_compat` | minimax, minimax-cn (any registry provider with `api='anthropic-messages'` that isn't native `anthropic`) | `GET {base}/v1/models`, `x-api-key` + `anthropic-version` — same probe as native but against the provider's OWN base_url (e.g. `https://api.minimaxi.com/anthropic`). The `openai_bearer` `GET {base}/models` 404s on these hosts and would brand a good key `invalid_credential`. |
| `google_query` | google | `GET https://generativelanguage.googleapis.com/v1beta/models?key=…&pageSize=1` |
| `oauth` | openai-codex, gemini-subscription, github-copilot, claude-code, opencode | `AuthManager.acquire_sync(pid).status` (`fresh`→valid, `needs_reauth`→invalid); no network beyond an optional token refresh |
| `cloud` | amazon-bedrock, google-vertex, azure-openai-responses | `not_applicable` for the generic probe (SigV4 / ADC / deployment-keyed) until a native list-call is added |

## 7. Status-code → status (the single interpreter)

```
200                                          -> valid
401 / 403                                    -> invalid_credential
402 / body~insufficient.?quota|balance       -> valid_no_balance
429 / 5xx / "no endpoints" / "data policy"   -> valid_model_unavailable   (layer 2 only)
transport error / ambiguous                  -> unknown
no credential resolvable                      -> missing
provider has no key concept                  -> not_applicable
```

## 8. Caching & refresh

60 s in-process TTL keyed by `provider_id` (+ whether a model was named).
`use_cache=False` / `refresh=True` bypasses. Results carry `cached: bool`.
Never store or return the secret.

## 9. Surface integration

- **Save** (`POST /api/config`): persist *first* (a slow/offline provider must
  never block saving), then fire `validate_credential(pid, api_key=val)` and let
  the row flip `Checking…` → green/amber/red/grey. Layer 1 only — never spend a
  completion.
- **Verify button** (`POST /api/config/verify`): same call, explicit `api_key`,
  synchronous, shows status + `detail`.
- **Connectivity check** (existing React component → `/test`→`/validate`):
  default = layer 1; a "Test a model" affordance passes `{model}` for layer 2.
  The existing "Model X is unavailable right now" note is the
  `valid_model_unavailable` rendering.
- **Status rows** (`config_schema.get_settings` + TUI + web Providers tab): two
  columns — `Configured` (layer-0 presence, instant) and `Validated` (cached
  layer-1, 60 s). Every row gains a `/test` action so the TUI reaches the same
  probe the web button uses. OAuth rows render `fresh/expiring/needs_reauth`
  distinctly.

Ambiguous-state copy (opencode `error.ts` style):
`valid_no_balance` → "Key works — account has no balance. Add funds at <doc>.";
`invalid_credential` → "Key rejected (401). Re-check the key or re-login.";
`unknown` → "Couldn't reach <provider> to verify. Saved anyway; will validate
on first use."; OAuth `needs_reauth` → "Login expired — run `openprogram
providers login <pid>`."

## 10. Migration plan

1. **(done)** Create `credentials.py`: `CredentialResult`, status enum, per-KIND
   probe registry; move `_credential_check` / `_is_model_unavailable` /
   `_MODEL_DOWN_STATUSES` / `_CREDENTIAL_PROBE_PATHS` here; add the Anthropic,
   Google and OAuth probes and the `402`/no-balance branch; implement
   `validate_credential()` layers 0→1→(2 if model) + 60 s cache +
   `provider_auth_status()`.
2. **(done)** `test_provider()` delegates to `validate_credential()` and adapts
   to the legacy dict.
3. **(done)** `_validate_api_key()` becomes a shim → closes the ~17-provider
   gap. Add `POST /api/providers/{name}/validate` + `GET
   /api/providers/auth-status`; `/test` aliases `/validate`.
4. **(staged)** Fetchers call `validate_credential(pid)` once before dispatching;
   drop per-fetcher key-presence reimplementations.
5. **(staged)** `check_providers()` / `_is_configured()` keep cheap presence as
   `configured`; add cached `validated`. `config_schema.get_settings()` reads
   both and sets `action:'/test'`.
6. **(staged)** Fix the `<authenticated>` sentinel for bedrock / vertex — return
   `None` + KIND `cloud` so they report `not_applicable`, not a fake green.
7. **(staged)** Tests: outcome × KIND matrix.

## 11. Testing matrix

outcome × KIND: `200→valid`, `401→invalid_credential`,
`402/insufficient_quota→valid_no_balance`, OpenRouter public `/models` **not**
mistaken for valid (must use `/key`), Anthropic without `anthropic-version`,
OAuth `needs_reauth`, layer-2 `429→valid_model_unavailable`, offline→`unknown`,
no key→`missing`.

## 12. Adding a new provider

Declare its probe KIND in `credentials.py::_kind_for` (default `openai_bearer`
needs nothing). That single line wires it into save-verify, the connectivity
button, status rows, and the CLI/TUI at once.

**Anthropic-wire third parties** (MiniMax & friends) are auto-detected:
`_kind_for` returns `anthropic_compat` for any provider whose registry `api`
is `anthropic-messages` (and isn't native `anthropic`). Keep this consistent
across three places or the provider half-works:
- `_kind_for` → `anthropic_compat` (credential probe hits `{base}/v1/models`);
- `_model_catalog/providers.py::_PROVIDER_DEFAULT_API` must stamp
  `anthropic-messages` (so fetched/custom rows route to the right stream fn,
  not `POST /chat/completions`) — matching `models_generated`;
- `_model_catalog/fetchers` routes `anthropic-messages` providers to the
  base_url-aware `_fetch_anthropic` (the OpenAI-compat `GET {base}/models`
  404s on a `/anthropic` host).
A drift guard test (`test_model_fetch_routing.py`) pins the api stamp to
`models_generated`.

## 13. Open questions

- `valid_no_balance` is only cheaply detectable for OpenRouter (`/key`) and via
  a layer-2 `402`; elsewhere a `200` proves auth but not balance — accept plain
  `valid` until first real call surfaces `insufficient_quota`.
- Auto layer-1 on every single-key save vs defer to an explicit Verify click on
  bulk save (throttle to avoid a burst of probes).
- Anthropic OAuth (`ANTHROPIC_OAUTH_TOKEN`) needs `Authorization: Bearer` +
  `anthropic-beta: oauth-…` on the same `/v1/models` probe — confirm the beta
  value, or route it to the AuthManager path.
- openai-codex has no auth-only listing endpoint (the ChatGPT backend 403s), so
  its only end-to-end probe is a layer-2 `/responses` ping — rely on AuthManager
  `Credential.status` for the default (structural, not end-to-end) check.
