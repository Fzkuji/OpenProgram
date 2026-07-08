# Model Catalog and Provider Configuration (Final Design)

> This document describes the **target runtime logic** of the model catalog: where data lives, how files interact with code, and how the backend and frontend each consume it.
> Gaps between this design and the current code, plus the migration path, live in section 8 — sections 1–7 only ever describe the target state, never history.
> Thinking-effort parameter details are covered in [thinking-effort.md](thinking-effort.md).

## 1. Architecture in one sentence

**The system only remembers the models the user enabled.** Browsing "which models are available" is a live settings-page query that is never persisted; the act of **enabling** copies that model's full spec, as of that moment, into `config.json`. The runtime registry `ENABLED_MODELS` is those few dozen config rows — what `get_model()` resolves, what the chat page shows, and what the user checked are physically the same data.

**Core invariant: selectable in chat = enabled = resolvable by the backend.** Not because a merge pipeline keeps two lists aligned, but because there is only one list.

Properties that follow automatically:

- **No big files**: no full catalog is stored (models.dev has 151 providers, thousands of largely duplicated models); config holds only the handful the user enabled.
- **Nothing goes stale**: staleness requires storage. The available list is queried live, so it is always current; enabled models' specs are overwritten on demand via the settings page "Refresh" — refreshing only what the user actually uses.
- **git stays clean**: the program writes only the user's config; the repository holds only hand-written provider.json; the installed package directory is read-only at runtime.

## 2. Data layout (split by author)

| Author | Location | Content | Size |
|---|---|---|---|
| **Humans** (git) | `providers/<p>/provider.json` (+ `<p>.py` for dedicated protocols) | endpoints, thinking, cache, per-model overrides | a few lines each |
| **The program** (user machine) | `config.json` → `providers.<p>.models` | full specs of enabled models + keys and other user state | tens of lines |
| Third party (network) | models.dev + official `/v1/models` | live sources for settings-page browsing | never persisted |

```
openprogram/providers/                 ← all git-tracked, read-only at runtime
├── deepseek/
│   ├── provider.json                  ← all hand-written config for this provider (section 3)
│   └── deepseek.py                    ← wire/stream implementation (only for dedicated protocols)
├── enabled_models.py                  ← ENABLED_MODELS: loads config + endpoint fill + thinking derivation
└── models.py                          ← get_model / get_providers / get_models

~/.openprogram/
└── config.json                        ← the only user-side persistence
```

**Naming rule: the word "catalog" is retired entirely** (historically one word for five things — the root of the naming confusion). `models_generated.py`, `thinking_catalog.py`, `_catalog_new.py`, and webui's `_model_catalog/` (→ `_model_listing/`) all retire. **The name `ENABLED_MODELS` is only valid once the dict truly holds enabled models only** — semantics change first, the name follows (see 8.2).

**Providers without a directory** (fireworks, together, …): models.dev lists them live; the user enters a key, browses, enables — no file in the package is ever needed.

## 3. provider.json: the only hand-written file

All human configuration for a provider in one file; every field optional:

```json
{
  "id": "deepseek",
  "endpoints": {
    "default": {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}
  },
  "thinking": {
    "wire_format": "effort_string",
    "effort_map": {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "max": "max"},
    "default_effort": "medium"
  },
  "cache": {"mode": "none"},
  "model_overrides": {
    "some-model": {"headers": {"X-Foo": "1"}, "compat": {"no_stream_options": true}}
  },
  "models_from": null
}
```

| Field | Purpose | Default behaviour |
|---|---|---|
| `endpoints` | api/base_url groups referenced by name (opencode has 4, copilot 3; single-wire has just `default`) | models.dev base_url + OpenAI-compatible protocol |
| `thinking` | wire_format / effort maps / per-model levels (formerly thinking.json; see thinking-effort.md) | OpenAI-compatible fallback (low/medium/high) |
| `cache` | prompt-caching declaration (formerly cache.json) | no explicit cache control |
| `model_overrides` | per-model headers, compat, `endpoint` reference, `key_prefix` — fields machines cannot obtain, **folded into the spec at enable time** | none |
| `models_from` | browsing-source borrowing for subscription providers (claude-code → anthropic) | no borrowing |

**The test: if a machine can obtain a field, a human doesn't write it.** provider.json contains no model list — the list is browsed live, the spec is copied at enable time.

Directory names use underscores (`amazon_bedrock/`); `id` keeps the hyphenated name (`amazon-bedrock`). Same-service-multiple-protocols (Bailian's OpenAI-compatible + Anthropic-compatible endpoints) = two endpoints of one provider, not two providers.

## 4. The two actions: browse, enable

### 4.1 Browse (live, never persisted)

The user opens a provider's model list in settings:

```
list_available_models(provider_id)
  = official /v1/models (when a key exists; Anthropic additionally pulls per-model
    capabilities; probe infers reasoning)
  ⊕ models.dev (fills price/capabilities; full fallback when there is no key)
  → merged in memory, returned straight to the frontend
```

Results live in memory only (a short-TTL cache is fine); closing the page discards them. Browsing is unavailable offline — **discovering new models requires the network by definition**; that is a fact, not a defect.

### 4.2 Enable (copy the spec into config)

The user checks a model in the browse list:

```
enable_model(provider_id, row)
  → spec = browse row ⊕ provider.json.model_overrides[id] ⊕ api/base_url from endpoints
  → thinking levels derived from provider.json.thinking, written alongside
  → appended to config.json providers.<p>.models
  → ENABLED_MODELS reloads
```

- **Disable** = delete the row from config.
- **Refresh** = re-run browse for enabled models and overwrite their specs (handles spec drift over time; touches only what the user actually uses).
- **Manually adding a model** (one the provider doesn't list) = the user fills in a row in the same form — it writes to the same list; the old `custom_models` concept dissolves.
- **Dynamic registration for subscription providers** (e.g. claude-code auto-adds 3 models after login) = the program performs an enable on the user's behalf, writing to the same list.

## 5. How the backend uses it

```python
# openprogram/providers/enabled_models.py
ENABLED_MODELS: dict[str, Model]   # key = "<prefix>/<id>", content = config specs + derived fields
```

Loaded from config at startup (tens of rows, instant), reloaded when config changes. The three query functions `get_model` / `get_providers` / `get_models` keep their interfaces — the 20+ runtime callers (agent, runtime, failover, …) change nothing. `get_model` falls back through `auth.aliases` on a miss.

**Contract: the system only knows enabled models.** Failover chains and agent configs must reference enabled models; referencing a non-enabled model is a configuration error whose message points to the settings page. Old sessions referencing a deleted model still display history; they just cannot continue with that model.

## 6. How the frontend uses it

| Frontend surface | API route | Data source |
|---|---|---|
| Settings provider list | `GET /api/providers` | providers with a provider.json + those models.dev lists live (community providers configurable directly) |
| Settings browse/check models | `GET /api/providers/<id>/available` | **live**: the browse result of 4.1 + enabled flags |
| Chat model picker | `GET /api/models/enabled` | **config**: ENABLED_MODELS as-is |
| Thinking level picker | (`_thinking.py`) | thinking_levels from the ENABLED_MODELS rows |

The webui presentation layer (`_model_listing/`) does no merging or derivation — browse merging is one function (4.1), spec merging happens at enable time. webui imports providers; providers never import webui.

**End to end**: enter a key → browse (live list shows `deepseek-v4-flash`) → check it (full spec written to config; `ENABLED_MODELS["deepseek/deepseek-v4-flash"]` appears) → pick it in chat and send (`get_model` hits the same config row). At every moment the system holds exactly one copy of model data.

## 7. Invariants (check before changing code)

1. **Persist only what's enabled**: the only persisted model data is the enabled specs in config. Any second persisted list (full snapshot, fetch cache file, hand-written catalog) is a violation.
2. **Browsing never persists**: the available list is a live query + in-memory cache, never written to a file.
3. **Split by author**: human data in git (provider.json); program data in user config; the package directory is read-only at runtime.
4. **Minimal hand-writing**: provider.json stores only what machines cannot obtain, and never a model list.
5. **One-way layering**: `openprogram.providers` never imports `openprogram.webui`.
6. **Key compatibility**: `"<prefix>/<id>"`, alias fallback, `key_prefix` (gemini-subscription dual keys) preserved; the registry stays one mutable dict.

## 8. Current deviations and migration

> Recorded 2026-07-08. Full problem description: [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md).

### 8.1 Deviations

1. **Persisting what shouldn't be persisted**: 752 hand-written `models.json` rows (22 providers, already rotted) + `models.fetched.json` (Fetch persisted into the installed package, papered over with .gitignore). Neither exists in the target state.
2. **Two data chains**: the settings page goes through webui's `combined_models` (fetched + models.dev) while the runtime registry reads only hand-written `models.json` — the settings page can select `deepseek-v4-flash`, `get_model` cannot find it. The target state needs no merge pipeline at all: config is the only copy.
3. **Five file kinds per provider** (provider.json / models.json / thinking.json / cache.json / models.fetched.json). Converges to one provider.json.
4. **Probe results write back into git-tracked thinking.json** — a program writing a version-controlled file. In the target state probe only affects browse results and the spec written to config at enable time.
5. **Inverted layering**: models.dev source and merge logic in `webui/_model_catalog/`; the providers layer reads the registry back to fill api/base_url — a cycle.
6. **The registry holds 755 models**, named `MODELS` then `MODEL_REGISTRY`. The target holds enabled models only, renamed `ENABLED_MODELS` — **the name follows the semantics; no rename before the semantics change**.
7. **Non-standard bailian naming**: models.dev calls the same-base_url provider `alibaba-token-plan-cn`; the reserved empty directory exists; the user asked for the standard name and deletion of `bailian/`.

### 8.2 Migration order (each step commits independently, nothing breaks)

1. **bailian → alibaba_token_plan_cn**: rename directory + id. Small and independent — first.
2. **Enable = copy the spec**: checking a model writes the full spec (browse row ⊕ overrides ⊕ endpoints ⊕ thinking derivation) into config `providers.<p>.models`, unified with `custom_models` into one list. Existing users' `enabled_models` id lists migrate once: resolve each id against the current registry and write full specs.
3. **Runtime switches to config**: the registry loads from "config specs + provider.json fill"; `get_model` semantics unchanged. **The two chains become one here** — the 752-row `models.json` files and the fetched-file machinery become dead code.
4. **Browsing goes live**: `list_models_for_provider` splits into "available" (live browse) and "enabled" (read config); Fetch persistence is deleted (a short-TTL memory cache may remain).
5. **Delete dead data**: `git rm` the 22 `models.json` files, the fetched-file machinery, and the `.gitignore` line.
6. **Unify config**: fold `thinking.json` and `cache.json` into `provider.json`; `thinking_spec`/`cache_spec` read the new location; `_default_api_for`/`_resolve_base_url` read endpoints (cycle dissolves).
7. **Naming finish**: rename the registry to `ENABLED_MODELS` (semantics now true), definition in `enabled_models.py`; retire `models_generated.py`, `thinking_catalog.py`, `_catalog_new.py`, `_model_catalog/` (→ `_model_listing/`). No "catalog" left in the code.

### 8.3 What the migration must preserve (from prior reviews)

- **Existing enablement must survive**: step 2's id → spec migration must cover all 22 providers' current enabled_models and custom_models; verify per row that `get_model` results are equivalent before and after.
- **Aliases / dual keys**: gemini-subscription's `google-gemini-cli/*` + `gemini-subscription/*` are 10 keys with distinct names — enabled rows carry their own key and name, so this survives naturally; alias fallback logic untouched.
- **The claude-code borrowing chain**: browse data borrowed from anthropic (`models_from`), 3 models auto-enabled after login, special fetcher — preserve each.
- **Field-by-field fidelity**: nested `cost`, multimodal `input`, `headers` (copilot depends on it), `compat` — the spec written to config at enable time must carry all of these; before deleting the hand-written catalogs confirm every enabled row has equivalent values.
- **Verification granularity**: multi-wire providers need one exec per `(api, base_url, headers, compat)` combination.
- Core regression tests stay green: `tests/unit/test_provider_wire_invariants.py`, `tests/unit/test_model_fetch_routing.py`.
