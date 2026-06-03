# API-key / credential resolution unification

Status: **core landed (steps 1–5)** · step 6 deferred (see §4) · Owner: providers · Created: 2026-06-04

Part of the 2026-06 optimization roadmap (audit item #3 — the root cause of the
provider-config fragmentation theme). Follows [credential-validation-unification](credential-validation-unification.md):
that doc unified "is this key *valid*"; this one unifies "what *is* the key, and
is the provider *configured*".

## 1. Problem

"What API key does provider X use, and is X configured?" is answered by at least
**four env-var maps** and **three resolvers**, each with different knowledge:

Maps (provider → env var):
- `providers/env_api_keys.py:10` `PROVIDER_ENV_VARS` — 20 providers; `google → GEMINI_API_KEY`.
- `webui/_model_catalog/providers.py:97` `_ENV_API_KEYS` — 19 providers; `google → GOOGLE_GENERATIVE_AI_API_KEY`, `anthropic → ANTHROPIC_API_KEY`.
- `webui/_model_catalog/credentials.py` `provider_id_for_env_var` — inline reverse aliases.
- `webui/_model_catalog/storage.py:_resolve_api_key` — inline Google multi-name special case.

Resolvers:
- `env_api_keys.get_env_api_key(provider_id)` — the **runtime** path (used by
  `providers/stream.py:62,105` and every provider adapter). **Env vars only, no
  config.json.** Has the broadest special-case knowledge: GitHub Copilot (3
  tokens), Anthropic (`ANTHROPIC_OAUTH_TOKEN` > `ANTHROPIC_API_KEY`), Amazon
  Bedrock + Google Vertex (return the `"<authenticated>"` sentinel).
- `storage._resolve_api_key(provider_id)` — the **webui/model-catalog** path
  (validate, fetchers, test). Uses `_env_var_for` (the *other* map) + a
  config.json `api_keys` fallback + my Google multi-name fallback. Does **not**
  know Anthropic OAuth precedence, Bedrock/Vertex, or Copilot.
- `server._get_api_key(env_var)` — keyed by env-var *name* (not provider id);
  env > config.json. Used for "is configured" checks in `check_providers`.

### Consequences

1. **A provider can read "configured" on one surface and "missing" on another.**
   `google` resolves under different env-var names per path. Anthropic resolves
   to `ANTHROPIC_OAUTH_TOKEN` at runtime but `ANTHROPIC_API_KEY` in the webui.
2. **Latent runtime bug.** Nothing hydrates config.json `api_keys` into
   `os.environ` at startup — only `routes/config.py:87` does, *on save, in that
   process*. The runtime resolver `get_env_api_key` is **env-only**. So a key
   saved purely through the web UI is in config.json + the live process env, but
   after a **worker restart** it's gone from env, and runtime LLM calls fail to
   find it even though config.json has it. (The webui path masks this because
   `_resolve_api_key` *does* read config.json — so the connectivity check passes
   while the actual chat fails.)
3. **`"<authenticated>"` sentinel** conflates "configured" with "here's the key":
   Bedrock/Vertex return a fake string that any Bearer-header code would send
   verbatim. Today their runtime adapters use the AWS/ADC SDK chains and only
   treat it as a truthiness flag, but it's a footgun.

## 2. Goal

One canonical credential module — `providers/env_api_keys.py` (it already has
the broadest special-case knowledge and lives in `providers/`, importable by
both the runtime and the webui with no circular dependency). Every other
resolver/map becomes a thin wrapper over it. Unifying onto a resolver **with a
config.json fallback also fixes the restart bug**, so this is correctness work,
not just dedup.

Best-design bar: the resolver must be the single place that knows how any
provider's credential is found, layered (env → config → cloud-cred chain),
cached on the hot path, and reverse-mappable — so adding a provider is one entry.

## 3. Canonical API (in `providers/env_api_keys.py`)

```python
def env_vars_for(provider_id: str) -> list[str]:
    """Accepted env-var names for this provider, in precedence order.
    google -> [GEMINI_API_KEY, GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY]
    anthropic -> [ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_API_KEY]
    github-copilot -> [COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN]
    Replaces PROVIDER_ENV_VARS, _ENV_API_KEYS, and _env_var_for."""

def resolve_api_key(provider_id: str, *, allow_config: bool = True) -> str | None:
    """The real, usable key/token, or None.
    1. each env var in env_vars_for(), first hit wins;
    2. if allow_config: config.json api_keys[<name>] for each name (cached);
    3. cloud-credential providers (bedrock/vertex) -> None here (no bearer key);
       their state is is_configured(), not a key.
    Replaces get_env_api_key and storage._resolve_api_key."""

def is_configured(provider_id: str) -> bool:
    """True when the provider has working credentials, INCLUDING cloud-cred
    chains: resolve_api_key() is not None, OR the bedrock AWS chain / vertex ADC
    is satisfied (the logic that used to mint the '<authenticated>' sentinel).
    Replaces the scattered bool(_get_api_key(env)) / _is_configured checks."""

def provider_id_for_env_var(env_var: str) -> str | None:
    """Reverse of env_vars_for, for the save-key verify path that only knows the
    env-var name. Moves here from credentials.py."""
```

The `"<authenticated>"` sentinel is **deleted**: `resolve_api_key` returns `None`
for cloud-cred providers (their adapters never used it as a real key), and
`is_configured` carries the "yes, configured" answer. A cheap module-level cache
(config dict by mtime) keeps `resolve_api_key` off the filesystem on the
per-stream hot path.

## 4. Migration (each step independently committable + verifiable)

Status: steps 1–5 **landed** (commits f4fec73d, 9d4d55dc, 62e78e3c, d3ce990d,
5c4d6aa6). Step 6 **deferred** — see the note after the list.

1. **(done)** Add the canonical functions (`env_vars_for`, `resolve_api_key`,
   `is_configured`, `provider_id_for_env_var`) to `env_api_keys.py` with the
   merged env-var table + mtime-cached config read. No callers changed. Added
   `tests/unit/test_api_key_resolution.py`.
2. **(done)** `storage._resolve_api_key` delegates to `resolve_api_key` (known
   providers); community/models.dev providers keep the env-var fallback.
3. **(done)** `get_env_api_key` (runtime) delegates to `resolve_api_key` — gains
   the config.json fallback (the restart-bug fix); Bedrock/Vertex keep the
   sentinel for their adapters.
4. **(partial)** `_model_catalog/providers.py:_is_configured` key-branch
   delegates to the canonical `is_configured`. **Still using `_get_api_key`:**
   `providers/registry.py:check_providers`, the `server.py` provider table, and
   `routes/providers.py:45` — left for a later pass (they work; just duplicative).
5. **(done)** `credentials.provider_id_for_env_var` re-exports the canonical.
6. **(deferred)** Collapse the legacy flat maps. *Not a safe mechanical change:*
   `_env_var_for`/`_ENV_API_KEYS` is the **display-primary** name (anthropic →
   `ANTHROPIC_API_KEY` for the key form), which is a different concept from
   `env_vars_for`'s **resolution-precedence** list (anthropic → OAuth first), so
   `_env_var_for` is *not* `env_vars_for(pid)[0]`. And the flat `PROVIDER_ENV_VARS`
   is still consumed by `auth/cli.py` + `auth/interactive.py` as the
   "providers-with-an-env-key" list whose membership **intentionally excludes**
   anthropic/copilot (former special cases) — deriving it from the canonical
   table would change that list's membership and the auth-CLI login flow. Closing
   this needs the auth-CLI semantics reconciled first; tracked as future work.

Back-compat: `get_env_api_key`, `_resolve_api_key`, `_env_var_for` keep their
names as thin wrappers so the ~30 call sites don't churn.

## 5. Verification

- Unit: `tests/unit/test_api_key_resolution.py` — precedence, config fallback,
  cloud-cred is_configured True with no key, reverse map, Anthropic OAuth>key,
  Google three-name, no `<authenticated>` ever returned by `resolve_api_key`.
- Cross-surface: for each of the user's configured providers (anthropic, openai,
  google, deepseek, openrouter, …) confirm the SAME resolution from the runtime
  path and the webui path.
- Restart-bug repro: POST a key via `/api/config`, restart the worker, assert
  `resolve_api_key` finds it from config.json (env cleared).
- Each migration step: restart worker, `/healthz`, `/api/providers/auth-status`
  unchanged, a real provider validate still `valid`.

## 6. Open questions

- Should the runtime config fallback be always-on, or behind `allow_config`
  defaulting True with a way for pure-env deployments to opt out? (Proposed:
  always-on; a key in config.json is the user's intent regardless of env.)
- Config-read caching: mtime-stat per call is cheap; a TTL is simpler. Proposed
  mtime so a freshly-saved key is picked up immediately without a restart.
- Longer term: a `Provider` metadata dataclass (id, env_vars, kind, base_url,
  default_api) folding in [credential-validation-unification](credential-validation-unification.md)'s
  KIND table and `_PROVIDER_DEFAULT_API` — one registry per provider. Out of
  scope here; this doc only unifies key resolution.
