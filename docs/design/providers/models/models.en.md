# Model Catalog and Provider Configuration (Final Design)

> This document describes the **target runtime logic** of the model catalog: the file structure, how the files interact with code, and how the backend and frontend each consume the data.
> Gaps between this design and the current code, plus the migration path, live in section 8 — sections 1–7 only ever describe the target state, never history.
> Thinking-effort parameter details are covered in [thinking-effort.md](thinking-effort.md).

## 1. Architecture in one sentence

Each provider is a self-contained directory holding a few small files; at startup a **single merge pipeline** turns them into the runtime registry `MODEL_REGISTRY`; the backend's `get_model()` and every frontend surface (settings page, chat picker, thinking picker) read that same merged result.

**Core invariant: any model the frontend can select, the backend can resolve** — because there is no second copy of the data anywhere.

## 2. File structure

```
openprogram/providers/
├── deepseek/                      ← one directory per provider (underscore naming)
│   ├── provider.json              ← git: id + endpoint groups (api/base_url)
│   ├── models.json                ← git: builtin baseline (only fields no external source provides)
│   ├── models.fetched.json        ← gitignored: official model list pulled by Fetch
│   ├── thinking.json              ← git: thinking-effort mapping
│   ├── probe_thinking.py          ← probe script auto-run during Fetch (optional)
│   └── deepseek.py                ← wire/stream implementation (only for providers with a dedicated protocol)
├── model_registry/                ← merge pipeline (the single data outlet), defines MODEL_REGISTRY
│   ├── loader.py                  ← reads provider.json + models.json + models.fetched.json
│   ├── models_dev.py              ← models.dev source (in-memory cache, TTL 24h)
│   └── merge.py                   ← layered overlay, produces Model objects
├── thinking_spec.py               ← reads thinking.json + effort translation + thinking-field derivation
└── models.py                      ← get_model / get_providers / get_models
```

**Naming rule: the word "catalog" is retired entirely.** Historically `_catalog/`, `catalog.json`, `_catalog_new.py`, `thinking_catalog.py`, and webui's `_model_catalog/` used one word for five different things — the root of the naming confusion. The word does not appear in the target state: the merge pipeline is called `model_registry` (its product is `MODEL_REGISTRY`), thinking derivation folds into `thinking_spec.py`, and the webui presentation layer becomes `_model_listing/`. `models_generated.py` retires with it — it hasn't been "generated" for a long time.

One table for every file's role:

| File | In git | Written by | Read by | Change frequency |
|---|---|---|---|---|
| `provider.json` | ✅ | human (once, when adding a provider) | merge pipeline | almost never |
| `models.json` | ✅ | human (only fields no external source provides) | merge pipeline | rarely |
| `models.fetched.json` | ❌ | Fetch (overwritten on every click) | merge pipeline | every Fetch |
| `thinking.json` | ✅ | human + probe auto-writes overrides | thinking_spec | only when the API format changes |
| models.dev (remote) | — | third party | merge pipeline (lazy) | continuously upstream |
| `config.json` providers section | — | user actions in the settings page | frontend listing + runtime | any time |

**Division of labour: every field has exactly one authoritative source.**

- `api` / `base_url` → `provider.json` endpoints (bound in pairs, see 2.1)
- "which models exist" → `models.fetched.json` if fetched, else the `models.json` baseline, else models.dev
- price, context, capabilities → models.dev (official Fetch data wins)
- thinking levels → derived from `thinking.json` (`thinking_spec.derive_thinking_fields`)
- "which models the user enabled" → `config.json` (`enabled` / `enabled_models`) — user state, never mixed into catalog data

`models.json` is therefore thin: `id`, `name`, `endpoint` (when not default), `key_prefix` (dual-key cases), `headers`, `compat`, plus inline spec fields only when models.dev doesn't cover the provider. **Any field that models.dev or the official API can supply is never hand-written** — hand-written data has no update mechanism and inevitably rots. For most providers this file may not exist at all (the model list falls back to models.dev).

### 2.0 Why models.json and models.fetched.json are two files

Their contents look similar but their **lifecycles are entirely different**; merging them into one file breaks either way:

| | `models.json` | `models.fetched.json` |
|---|---|---|
| Nature | factory defaults: repository-maintained models + hand-written overrides | live state on the user's machine: the official list pulled by Fetch |
| Changed by | humans, via git commits | the program, overwritten on every Fetch |
| Without it | offline + never fetched → the provider has no models | just not fetched yet; baseline + models.dev cover it |

As one file: either it is git-tracked — then a single Fetch click dirties the repository (a pip-installed package directory may even be read-only, so the write fails) — or it is not — then the project ships with no factory models and an offline install is empty. Hence "the repository's data stays in the repository, the machine's stays on the machine", overlaid at read time by the merge pipeline.

### 2.1 provider.json: endpoint groups

A single provider can span multiple wires (measured: opencode's 30 models fall into 4 `(api, base_url)` combinations, github-copilot's 19 models into 3 apis). But the combinations are few and bound in pairs, so they are declared centrally as endpoint groups that models reference by name:

```json
{
  "id": "opencode",
  "endpoints": {
    "default":   {"api": "openai-completions",   "base_url": "https://opencode.ai/zen/v1"},
    "anthropic": {"api": "anthropic-messages",   "base_url": "https://opencode.ai/zen"},
    "google":    {"api": "google-generative-ai", "base_url": "https://opencode.ai/zen/v1"},
    "responses": {"api": "openai-responses",     "base_url": "https://opencode.ai/zen/v1"}
  }
}
```

- Single-wire providers (deepseek, openai, …) have just one `default` and degenerate to the simplest form.
- A model in `models.json` writes `"endpoint": "anthropic"` to reference a group; omitted means `default`.
- Directory names use underscores (`amazon_bedrock/`); the `id` field keeps the hyphenated name (`amazon-bedrock`) — registry keys use hyphens.
- "Same service, multiple protocols" falls out naturally: Bailian's OpenAI-compatible endpoint and its Anthropic-compatible endpoint are two endpoints of one provider, not two providers.

### 2.2 Providers without a directory

Community providers (fireworks, together, …) can have no directory at all:

- model list → models.dev fallback; once the user enters a key and clicks Fetch, `_provider_dir()` auto-creates the directory and writes `models.fetched.json`
- `api`/`base_url` → defaults from the models.dev provider entry
- thinking → OpenAI-compatible fallback (low/medium/high)
- probe → silently skipped

Adding a builtin provider = create the directory + write `provider.json` (a few lines) + optionally `thinking.json`. No hand-copied model list required.

## 3. The merge pipeline (single data outlet)

`model_registry.merge` overlays layers per provider, bottom-up; non-empty fields from higher layers win:

```
layer 4  thinking.json derivation      → thinking_levels / default / variant
layer 3  models.fetched.json         → official authority: which models, context, capabilities
layer 2  models.json (git baseline)    → hand-written overrides: name, headers, compat…
layer 1  models.dev (lazy)             → fallback: model list, pricing, capabilities
base     provider.json endpoints      → api / base_url (per model, by endpoint name)
```

Rules:

1. **"Which models exist" follows the highest layer present.** Fetched → the fetched list is authoritative (baseline rows absent from it are hidden from the UI but kept in the registry so old sessions don't break); never fetched → baseline ∪ models.dev.
2. **Field-level merge**: fetched (official) > models.json (hand-written) > models.dev (fallback). Hand-written means override; official means fact.
3. **Thinking fields are always derived, never stored**: `thinking.json` model_overrides > Fetch capabilities > provider-level mapping > fallback (priority details in thinking-effort.md).
4. **Offline-safe**: if models.dev is unreachable that layer is skipped and price-like fields stay empty; `provider.json` + `models.json` + fetched data are all local, so running never depends on the network.
5. Output is `Model` objects keyed `"<prefix>/<id>"`; the prefix defaults to `provider.json`'s `id` and can be overridden per row with `key_prefix` (the gemini-subscription dual-key case).

The pipeline's result is simply:

```python
# openprogram/providers/model_registry/__init__.py
MODEL_REGISTRY: dict[str, Model]   # the one and only runtime model registry
```

Naming note: it is not called `ENABLED_MODELS` — the registry holds **all known models**, while "enabled" is user state in `config.json`; two concepts must not share one name. The old name `MODELS` was too generic and is retired.

## 4. How the backend uses it

### 4.1 Query interface (`providers/models.py`)

```python
get_model("deepseek", "deepseek-v4-flash")  # → Model | None, with alias fallback
get_providers()                              # → provider ids that have models
get_models("deepseek")                       # → all Models of that provider
```

`get_model` looks up `"<provider>/<id>"` first and, on a miss, tries alias-equivalent provider names via `auth.aliases`. The 20+ runtime callers (agent, runtime, failover, registry, …) only know these three functions and never care where the data came from.

### 4.2 The Fetch write path

The user clicks "Fetch Models" in the settings page:

```
fetchers.fetch_models_remote(provider_id)
  → call the official API (/v1/models etc.; Anthropic additionally pulls per-model capabilities)
  → probe_thinking.probe() infers reasoning / writes thinking.json overrides
  → save_fetched() atomically overwrites providers/<p>/models.fetched.json
  → registry invalidated and reloaded (pipeline reruns for that provider)
  → frontend refetches → UI refreshes
```

Fetch only writes `models.fetched.json` and never touches the git-tracked `models.json` — the builtin baseline is repository-maintained, so user actions never dirty git. But Fetch results **enter the registry immediately**: a new model visible in the settings page is resolvable by `get_model` at that same moment.

### 4.3 Custom models

User-added models live in `config.json` under `custom_models`; `_register_custom_model_in_registry` builds a `Model` and writes it into `MODEL_REGISTRY` in place (the registry is one mutable dict; this path depends on that semantic). Default api/base_url come from `provider.json` endpoints, no longer by querying the registry back.

## 5. How the frontend uses it

The frontend has no model data of its own; everything goes through three listing functions that read **the same registry** (`webui/_model_listing/listing.py`, currently named `_model_catalog/`, a pure presentation layer: labels, enabled flags, setup hints):

| Frontend surface | API route | Listing function | Content |
|---|---|---|---|
| Settings page provider sidebar | `GET /api/providers` | `list_providers()` | registry providers ∪ models.dev community providers (configurable before builtin) |
| Settings page model table | `GET /api/providers/<id>` | `list_models_for_provider()` | that provider's merged result + enabled flags |
| Chat page model picker | `GET /api/models/enabled` | `list_enabled_models()` | delegates to the row above, filtered by config's enabled state |
| Thinking level picker | (`_thinking.py`) | delegates to `list_models_for_provider` | thinking_levels from the same rows |

- **Enabled state** lives only in `config.json` (`providers.<id>.enabled` / `enabled_models`); listing reads it to set flags; checking/unchecking only edits config, never catalog data.
- The community tier of `list_providers` (all models.dev providers) lets users enable fireworks/together-class providers without waiting for a code release — enter a key, click Fetch, and the auto-created-directory path of 2.2 kicks in.
- Listing is a thin shell: **no field merging, no thinking derivation** — all of that is finished inside the `providers/model_registry` pipeline. webui imports providers; providers never import webui.

## 6. End-to-end sequence (one typical interaction)

```
User enables deepseek in settings and clicks Fetch
  → models.fetched.json written (official new model deepseek-v4-flash arrives)
  → registry reloads: MODEL_REGISTRY["deepseek/deepseek-v4-flash"] appears
User checks deepseek-v4-flash in the model table
  → config.json enabled_models appended
User picks it in the chat page and sends a message
  → backend get_model("deepseek", "deepseek-v4-flash") hits the same registry row
  → api/base_url from provider.json endpoints, thinking levels derived from thinking.json
  → request goes out
```

No step involves "another list", so nothing can disagree.

## 7. Invariants (check before changing code)

1. **Single outlet**: the model data shown to the user and the model data the runtime resolves come from the same merge function. A second data chain is a violation.
2. **Minimal hand-writing**: `models.json` only stores fields models.dev / the official API cannot provide. Adding an auto-obtainable field is a violation.
3. **One-way layering**: `openprogram.providers` never imports `openprogram.webui`.
4. **Runs offline**: baseline + fetched data suffice with no network; only decorative fields like pricing are missing.
5. **Key compatibility**: the `"<prefix>/<id>"` format, alias fallback, and `key_prefix` dual keys (gemini-subscription's 10 keys) are all preserved; the registry stays one mutable dict (custom models write in place).
6. **Fetch never touches git**: Fetch only writes `models.fetched.json`; `models.json` is changed only by humans (or repository migration scripts).

## 8. Current deviations and migration

> This section records the gap between the current code (2026-07-08) and the target above. The full problem description is in
> [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md).

### 8.1 Deviations

1. **Two data chains.** The merge pipeline is only half-implemented, inside webui: `webui/_model_catalog/provider_models.combined_models` (fetched + models.dev) feeds the settings page, while the runtime `MODELS` (`models_generated._load` → `_catalog_new.load_new_catalog`) reads only the git `models.json` and never looks at the fetched data or models.dev. Result: the settings page can select `deepseek-v4-flash` while `get_model` cannot find it.
2. **models.json is a full rich spec** with derived/obtainable fields (`thinking_levels`, `cost`, `context_window`, …; 22 providers, 752 rows), has no update mechanism, and has already rotted (deepseek only lists old models).
3. **Inverted layering**: the models.dev source and merge logic live in `webui/_model_catalog/`, while the providers layer's `_default_api_for`/`_resolve_base_url` read `MODELS` back to fill api/base_url — a providers→webui→providers cycle.
4. **`MODELS` is too generic a name** (rename already requested by the user).
5. **Non-standard bailian naming**: models.dev calls the provider with the same base_url `alibaba-token-plan-cn`; the repository already has the reserved empty directory `alibaba_token_plan_cn/`; the user has explicitly asked to adopt the standard name and delete `bailian/`.

### 8.2 Migration order (each step commits independently, nothing breaks)

1. **bailian → alibaba_token_plan_cn**: rename the directory + change `provider.json`'s id to `alibaba-token-plan-cn`, models carried over as-is; once the pipeline lands it auto-aligns with models.dev's 18 models. Small and independent — do it first.
2. **Sink the data layer**: move `provider_models.py` (fetched-file read/write + combined merge) and `sources/models_dev.py` from `webui/_model_catalog/` into `openprogram/providers/model_registry/`; change `_default_api_for`/`_resolve_base_url` to read `provider.json` endpoints. The dependency cycle dissolves here.
3. **Connect the registry to the pipeline**: `_load()` runs the full merge (the five layers of section 3); fetched data and models.dev enter the registry; `_catalog_new.py` folds into `model_registry/loader.py`. The two chains become one at this point.
4. **Thin out listing**: `list_models_for_provider` drops its own combined + thinking merge and just reads the registry plus presentation fields; the package renames `_model_catalog/` → `_model_listing/`.
5. **Slim models.json**: a script deletes all derivable/obtainable fields, verifying per provider that the merged result is field-identical to before slimming.
6. **Naming cleanup**: `MODELS` → `MODEL_REGISTRY` (definition moves into `model_registry/__init__.py`, retiring `models_generated.py`); `thinking_catalog.py`'s derivation folds into `thinking_spec.py`. After this the word "catalog" no longer exists in the code.

### 8.3 What the migration must preserve (from prior reviews)

- **Aliases / dual keys**: gemini-subscription's `google-gemini-cli/*` + `gemini-subscription/*` are 10 keys with distinct names, carried by per-row `key_prefix`; deduplicating by `(provider, id)` would drop 5.
- **The claude-code borrowing chain**: thinking goes alias→anthropic, models.dev data borrows anthropic (`_SUBSCRIPTION_BORROW`), and the fetcher is special — it is not an ordinary directory; the borrowing must move along.
- **Field-by-field fidelity**: `cost` is a nested object, `input` is multimodal, `headers` (copilot depends on it), `compat` — before the slimming script deletes a field, confirm the overlay layers actually restore it.
- **Verification granularity**: multi-wire providers need one exec per `(api, base_url, headers, compat)` combination, not one per provider.
- Core regression tests stay green: `tests/unit/test_provider_wire_invariants.py`, `tests/unit/test_model_fetch_routing.py`.
