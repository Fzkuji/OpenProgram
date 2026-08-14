# API-key / credential resolution

One module answers "what credential does provider X use, and is X configured?".
This is the companion to [credential-validation-unification](credential-validation-unification.md):
that document covers "is this key *valid*"; this one covers "what *is* the key,
and is the provider *configured*".

## 1. Why a single resolver

Credential resolution is asked from three different places — the runtime stream
path, the webui model-catalog path (validate, fetchers, test), and the
"is configured" status checks. If each keeps its own provider → env-var map and
its own lookup order, the same provider reads as configured on one surface and
missing on another: `google` resolves under a different env-var name per path,
`anthropic` resolves to `ANTHROPIC_OAUTH_TOKEN` at runtime but
`ANTHROPIC_API_KEY` in the webui.

A single resolver also decides *where* a credential may come from. Nothing
hydrates config.json `api_keys` into `os.environ` at startup — only
`routes/config.py:87` does, on save, in that process. An env-only resolver
therefore loses a web-UI-saved key after a worker restart while config.json
still holds it, and the webui connectivity check passes while the actual chat
fails. Layering config.json under env in one place removes that class of
divergence.

## 2. Where it lives

`providers/env_api_keys.py` is the canonical module. It sits in `providers/`, so
both the runtime and the webui import it with no circular dependency, and it
already carries the broadest special-case knowledge (GitHub Copilot's three
tokens, Anthropic's OAuth-before-key precedence, Amazon Bedrock and Google
Vertex's cloud-credential chains).

The resolver is the single place that knows how any provider's credential is
found: layered (env → config → cloud-credential chain), cached on the hot path,
and reverse-mappable. Adding a provider is one table entry.

## 3. The API

```python
def env_vars_for(provider_id: str) -> list[str]:
    """Accepted env-var names for this provider, in precedence order.
    google -> [GEMINI_API_KEY, GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY]
    anthropic -> [ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_API_KEY]
    github-copilot -> [COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN]"""

def resolve_api_key(provider_id: str, *, allow_config: bool = True) -> str | None:
    """The real, usable key/token, or None.
    1. each env var in env_vars_for(), first hit wins;
    2. if allow_config: config.json api_keys[<name>] for each name (cached);
    3. cloud-credential providers (bedrock/vertex) -> None here (no bearer key);
       their state is is_configured(), not a key."""

def is_configured(provider_id: str) -> bool:
    """True when the provider has working credentials, INCLUDING cloud-cred
    chains: resolve_api_key() is not None, OR the bedrock AWS chain / vertex ADC
    is satisfied."""

def provider_id_for_env_var(env_var: str) -> str | None:
    """Reverse of env_vars_for, for the save-key verify path that only knows the
    env-var name."""
```

Two questions, two functions. `resolve_api_key` returns a key or `None`;
`is_configured` answers configured-or-not, including the cloud-credential
providers that have no bearer key at all. Collapsing both into one function
means returning a placeholder string for the cloud providers, which any
Bearer-header code would then send verbatim — so they stay separate.

A module-level cache (the config dict keyed by mtime) keeps `resolve_api_key`
off the filesystem on the per-stream hot path. mtime rather than a TTL, so a
freshly saved key is picked up immediately without a restart.

The config fallback is always on: a key in config.json is the user's intent
regardless of what the environment holds. `allow_config=False` exists for
callers that deliberately want env-only resolution.

## 4. Callers

The pre-existing names stay as thin wrappers over the canonical functions, so
the roughly 30 call sites are unaffected:

- `get_env_api_key` (runtime) → `resolve_api_key`, which is where the runtime
  path gains the config.json fallback.
- `storage._resolve_api_key` (webui/model-catalog) → `resolve_api_key` for
  known providers; community and models.dev providers keep the env-var fallback.
- `_model_catalog/providers.py:_is_configured` and
  `providers/registry.py:check_providers` → `is_configured`.
- `credentials.provider_id_for_env_var` re-exports the canonical function.

`server.py`'s provider table and `routes/providers.py:45` keep `_get_api_key`,
which is keyed by env-var name and already reads env and config both.

## 5. Two maps that are not the same thing

`_env_var_for` / `_ENV_API_KEYS` holds the **display-primary** name — the one
the key form shows, `anthropic → ANTHROPIC_API_KEY`. `env_vars_for` holds the
**resolution-precedence** list, where anthropic puts OAuth first. They answer
different questions, so `_env_var_for` is not `env_vars_for(pid)[0]` and the two
cannot be mechanically merged.

Similarly, the flat `PROVIDER_ENV_VARS` is consumed by `auth/cli.py` and
`auth/interactive.py` as the "providers with an env key" list, and that list
deliberately excludes anthropic and copilot. Deriving it from the canonical
table would change the auth-CLI login flow, so it stays until the auth-CLI
semantics are reconciled.

## 6. Verification

- `tests/unit/providers/test_api_key_resolution.py` pins precedence, the config fallback,
  cloud-credential `is_configured` returning True with no key, the reverse map,
  Anthropic OAuth-over-key, and Google's three names.
- Cross-surface: for each configured provider, the runtime path and the webui
  path resolve identically.
- Restart behaviour: POST a key via `/api/config`, restart the worker, and
  `resolve_api_key` still finds it from config.json with the env cleared.

## Implementation status

The canonical functions and all five delegations above are in place. The legacy
flat maps (§5) remain, deliberately, until the auth-CLI semantics are settled.

A longer-term direction, out of scope here: a `Provider` metadata dataclass
(id, env_vars, kind, base_url, default_api) folding in
[credential-validation-unification](credential-validation-unification.md)'s KIND
table and `_PROVIDER_DEFAULT_API`, giving one registry entry per provider.
