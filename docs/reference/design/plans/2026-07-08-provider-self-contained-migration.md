# Provider Self-Contained Migration

The model catalogue moves out of `openprogram/providers/_catalog/*.json` (752
rows across 22 providers, tracked in git) into a self-contained per-provider
layout: `providers/<p>/provider.json` groups api and base URL by endpoint, and
`providers/<p>/catalog.json` holds the per-model run spec. The `MODELS` dict
keeps its type, population semantics, and key format exactly. `_catalog/` is
removed only once the new source reproduces every key.

The design source is
[`../providers/models/models.md`](../providers/models/models.md) §9.

## Data model

`provider.json` declares the provider identity and its wire endpoints:

```json
{"id": "opencode",
 "no_proxy": false,
 "endpoints": {
   "default":   {"api": "openai-completions", "base_url": "https://opencode.ai/zen/v1"},
   "anthropic": {"api": "anthropic-messages", "base_url": "https://opencode.ai/zen"}}}
```

`id` is the hyphenated canonical name, because `MODELS` keys are
`"<hyphenated provider>/<model id>"`. A single-wire provider has only
`{"default": {...}}`.

`catalog.json` holds one row per model:

```json
{"models": [{"id": "claude-x", "name": "Claude X", "endpoint": "anthropic", ...}]}
```

`endpoint` defaults to `"default"`; each model's `api` and `base_url` are filled
from `provider.json.endpoints[endpoint]`. Grouping wire configuration by
endpoint rather than repeating it per row is the point of the split — a provider
with 40 models and two wires stores two endpoint definitions instead of 80
duplicated fields.

Every row must retain its full field set: `id, name, api, provider, base_url,
reasoning, thinking_levels, default_thinking_level, thinking_variant, input,
cost{input, output, cache_read, cache_write}, context_window, max_tokens,
headers, compat`. `cost` is a nested object and `headers`/`compat` may be dicts;
none of them may be dropped or flattened.

### Key prefix

A row may carry `"key_prefix"`, in which case its `MODELS` key is
`"<key_prefix>/<id>"` instead of `"<provider.json.id>/<id>"`. The row's
`provider` field is always `provider.json.id`, never the key prefix, because
`get_providers()` groups by `model.provider`.

Each row produces its **own** `Model` object; prefixed rows do not share one.
This is what gemini-subscription needs: its catalogue holds ten keys — five
`google-gemini-cli/*` and five `gemini-subscription/*` — where five model ids
repeat, both batches carry `provider: "gemini-subscription"`, and every spec
field is identical *except `name`* (`… (Cloud Code Assist)` versus
`… (Subscription)`). Sharing a single `Model` between two aliased keys would
force both keys to one name and lose the distinction, so per-row `key_prefix`
with independent objects is used instead. The other 21 providers carry no
`key_prefix` and behave as before.

## Naming constraints

Two collisions govern where the data lands.

**Directories use underscores, not hyphens.** `providers/<p>/` directories
already exist holding wire implementation code (`<p>.py`, `auth_adapter.py`,
`thinking.json`) and are named in lowercase with underscores
(`amazon_bedrock/`, `google_gemini_cli/`), while catalogue provider prefixes use
hyphens (`amazon-bedrock`, `google-gemini-cli`). Migration reuses the existing
`provider_models._provider_dir(provider_id)` mapping — try the literal name,
then `.replace("-", "_")`, otherwise create the underscore form — so data files
land beside the wire code they belong to. Creating `providers/<hyphenated>`
directories instead would collide with five existing directories and produce
hyphen/underscore twins for six more. Eleven prefixes have no directory yet
(cerebras, gemini-subscription, groq, huggingface, kimi-coding, minimax,
mistral, openai, opencode, vercel-ai-gateway, xai, zai) and get underscore
directories created for them.

**The git-tracked spec file is `catalog.json`, not `models.json`.** The name
`providers/<p>/models.json` is already taken by the Fetch cache: written by
`provider_models.save_fetched`, gitignored, shaped like models.dev
(`{"provider", "models": [...]}`), and never loaded into `MODELS`. That is a
completely different schema from the catalogue's `{"<p>/<id>": row}`. Naming the
git-tracked run spec `catalog.json` lets both data sets live in the same
directory as clean layers without overwriting each other, and leaves the Fetch
path and its gitignore rule untouched.

## Loading

`models_generated._load()` merges two sources during migration: the legacy
`_catalog/*.json` files first, then the new per-provider source, which wins on
key collision. The legacy branch is deleted once equivalence is verified.

`MODELS: dict[str, Model]` keeps its name, type, mutability, and
`"<provider>/<id>"` key format. `_register_custom_model_in_registry` writes
`MODELS[k] = m` in place, so the loader may not return a read-only view or
rebuild the dict on each access. Loader fault tolerance follows current
behaviour: a missing or corrupt file is skipped rather than crashing, and
ordering is deterministic.

### Equivalence checking

Migration correctness is defined by a two-way comparison rather than by review.
Forward: every key in old `_catalog` must reproduce a byte-identical `Model`
from the new source. Reverse: the new source must not introduce keys absent from
the old one, which is what catches duplicated or spurious rows produced by the
endpoint grouping. The check runs while `_catalog/` still exists, reading old
from `_catalog/` and new from the per-provider directories independently.

Rows are kept one-per-key with no deduplication by model id — deduplicating
would silently drop one of gemini's two names.

## Breaking the providers ↔ webui cycle

`_default_api_for` and `_resolve_base_url` in the webui layer currently derive
api and base URL by scanning `MODELS`. With endpoints declared in
`provider.json`, that derivation moves to `openprogram/providers/_provider_meta.py`,
which exposes `provider_apis(provider_id) -> set[str]` and
`provider_base_url(provider_id) -> str | None` by reading `provider.json`
directly. The module imports no webui code and reads no `MODELS`, and its
directory resolution is read-only — it never creates a directory. The webui
helpers prefer it and fall back to the old `MODELS` scan while both sources
coexist.

## Appendix: Implementation Status

Designed, not yet landed. The intended order is: write the new loader
(`load_provider_dir` / `load_new_catalog`); write the migration script
(`migrate_catalog_file` / `migrate_all` / `verify_equivalence`); run the
migration for real and confirm zero mismatched keys across all 22 providers;
make `_load` dual-source with the new files winning; add `_provider_meta.py` and
repoint the two webui derivation helpers at it; then delete `_catalog/` and the
legacy loader branch.

Coverage against the design source: endpoint grouping and field preservation are
exercised by the loader and migration tests (headers, multimodal `input`, nested
`cost`); the circular dependency is resolved by `_provider_meta.py`; the
gemini dual-key case is handled by per-row `key_prefix` with independent
`Model` objects; multi-wire verification checks that each api/base_url
combination for opencode and github-copilot still resolves as before.

Two items are explicitly out of scope. `no_proxy` is a separate piece of work
tracked in the design source. The Fetch-cache filename conflict needs no work at
all — naming the git source `catalog.json` resolves it, and the Fetch path stays
as it is.

`tests/unit/test_provider_wire_invariants.py` and
`tests/unit/test_model_fetch_routing.py` are the regression gate and must stay
green throughout. After `_catalog/` is deleted, the equivalence test loses its
old-side input and should become a snapshot check or be retired, with the
dual-source and full suites carrying the guarantee.
