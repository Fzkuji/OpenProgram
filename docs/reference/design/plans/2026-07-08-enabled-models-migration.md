# Enabled-Models Migration

The system persists only the models a user has enabled. Their full specs live in
`config.json` under `providers.<p>.models`; the list of *selectable* models is
queried live by the settings page and never written to disk. This replaces a
752-row hand-maintained `models.json` set plus a `models.fetched.json` caching
layer, folds per-provider configuration into a single `provider.json`, and
renames the runtime registry to `ENABLED_MODELS`.

The design this realises is
[`../providers/models/models.md`](../providers/models/models.md) §1–7.

## Core principle

What `get_model()` resolves, what the chat page displays, and what the user
ticked in settings must be *physically the same data* — the enabled spec stored
in config. Enabling a model is therefore a copy, not a reference:

```
enabled spec = browsed row
             ⊕ provider.json model_overrides
             ⊕ api / base_url resolved from provider.json endpoints
             ⊕ derived thinking fields
```

The resulting complete row is written into config. Nothing at read time has to
re-join against a separate catalogue, so a model cannot display one way and
resolve another.

## Data model

`config.json` holds, per provider, a `models` list of complete spec rows. A row
carries every field the wire layer needs:

`id`, `name`, `api`, `base_url`, `cost` (nested `{input, output, cache_read,
cache_write}`), `input` (multimodal list), `context_window`, `max_tokens`,
`headers`, `compat`, `thinking_levels`, `default_thinking_level`,
`thinking_variant`, and `key_prefix` where present.

Field-level fidelity is a hard requirement: `cost` stays nested rather than
flattened, `headers` survives because github-copilot depends on it, and
`compat` and the thinking triple must all be present.

Manually added models join the same list with `"source": "manual"` instead of
living in a separate `custom_models` key.

### Registry semantics

The runtime registry is built from config, not from shipped data files.
`ENABLED_MODELS` is keyed `"<key_prefix or provider>/<id>"`, built by reading
each provider's config `models` rows and filling any missing `api`/`base_url`
from that provider's `provider.json` endpoints (an explicit value on the row
wins).

This changes the registry's meaning from "all 755 known models" to "only the
enabled ones". Two consequences are deliberate: referencing an un-enabled model
is a configuration error, and a fresh install with empty config yields an empty
registry, which is a legal state. The existing `get_model` → `None` error path
is unchanged.

The registry must remain **the same mutable dict** throughout — both
`_register_custom_model_in_registry` and `_claude_code_registry` write into it
in place.

### Browsing

The selectable-model list is assembled in memory on each request: live fetcher
results where an API key exists, `models.dev` as the no-key fallback and for
field completion, merged with enabled markers read from config. Nothing is
written to disk; a short in-memory TTL cache is optional. Without network the
browse degrades to an empty list plus an error message rather than failing.

"Fetch Models" means: force-refresh the browse cache, then overwrite the config
spec rows of models that are already enabled.

## Layering and interface constraints

- `get_model` / `get_providers` / `get_models` (`openprogram/providers/models.py`)
  keep their signatures and return types. The registry key format
  `"<prefix>/<id>"` is unchanged.
- Aliases keep working: `get_model` retains its fallback through
  `openprogram/auth/aliases.py`.
- One-way layering: `openprogram.providers` must not import
  `openprogram.webui`. When the providers layer needs config it uses a
  providers-layer or shared config reader.
- API response shapes are frozen. `GET /api/providers`,
  `GET /api/providers/<name>`, and `GET /api/models/enabled` return the same
  JSON fields as before, so the `web/` frontend needs no changes.
- gemini-subscription's ten keys (five `google-gemini-cli/*` plus five
  `gemini-subscription/*`, each with a distinct `name`) must all survive; every
  enabled row carries its own full key and name.
- The claude-code path is preserved: three models registered dynamically after
  login, thinking aliased to anthropic, and browse data borrowed from anthropic
  via `_SUBSCRIPTION_BORROW`.

## Naming

`bailian` becomes `alibaba-token-plan-cn`. The provider directory moves to the
existing `providers/alibaba_token_plan_cn/`, `provider.json`'s `id` becomes
`"alibaba-token-plan-cn"`, and an alias `bailian → alibaba-token-plan-cn` keeps
old config keys resolving.

Per-provider `thinking.json` and `cache.json` fold into `provider.json` under
`"thinking"` and `"cache"` keys. The registry moves to
`openprogram/providers/enabled_models.py` as `ENABLED_MODELS`;
`derive_thinking_fields` folds into `thinking_spec.py`. The word `catalog` does
not appear in code after the migration.

## Appendix: Implementation Status

Designed, migration not yet landed. The intended order is: rename bailian and
add its alias; make enabling copy the full spec into config while double-writing
the legacy `enabled_models` id list; switch the runtime registry to read config;
make browsing live and repoint the read paths; delete the 22 shipped
`models.json` files, the fetched-cache machinery, and the transitional double
write; fold `thinking.json` and `cache.json` into `provider.json` and move
`_default_api_for`/`_resolve_base_url` onto it to break the providers→webui
cycle; finish with the `ENABLED_MODELS` rename and directory cleanup.

Migration of existing installs happens once at config load: for each provider,
an `enabled_models` id with no matching row in `providers.<p>.models` is
resolved into a full spec and written back. Ids that cannot be resolved stay in
`enabled_models` with a warning rather than being dropped.

`tests/unit/test_provider_wire_invariants.py` and
`tests/unit/test_model_fetch_routing.py` must stay green. Where the
"enabled-only" registry invalidates their premise, they may be rewritten as
fixture-driven tests that inject a config covering several wire combinations
(openai-completions, anthropic-messages, google-generative-ai, openai-responses,
including headers, compat, and the dual-key case), but the original assertions
about wire invariants must be preserved rather than removed.
