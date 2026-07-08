# Model Catalog and Provider Configuration (Final Design)

> This document describes the **target runtime logic** of the model catalog: the file structure, how the files interact with code, and how the backend and frontend each consume the data.
> Gaps between this design and the current code, plus the migration path, live in section 8 — sections 1–7 only ever describe the target state, never history.
> Thinking-effort parameter details are covered in [thinking-effort.md](thinking-effort.md).

## 1. Design principle: split by author, not by content

Model data has exactly three authors; each gets one territory and never crosses into another's:

| Author | Location | Content |
|---|---|---|
| **Humans** (git) | `providers/<p>/provider.json` (+ `<p>.py` for dedicated protocols) | all hand-written config for that provider: endpoints, thinking, cache, a few model overrides |
| **A machine** (git) | `providers/models_dev_snapshot.json` (one global file) | models.dev snapshot, refreshed by a script — factory defaults + offline fallback |
| **The program** (user machine) | `~/.openprogram/fetched/<p>.json` | Fetch + probe results. **The package directory is never written** |
| Third party (network) | models.dev live (in-memory cache, TTL 24h) | optional refresh, no impact when offline |

One merge at startup → the runtime registry `MODEL_REGISTRY` → the backend's `get_model()` and every frontend surface read the same merged result.

**Core invariant: any model the frontend can select, the backend can resolve** — there is no second copy of the data anywhere.

Properties that follow automatically:

- **Hand-written data cannot rot**: humans only write what machines cannot obtain (endpoints, thinking mappings, header-style overrides). Model lists, prices, and context come from the snapshot/Fetch — machines update their own data.
- **git stays clean**: the program writes only to the user directory, never to the repository or the installed package (a pip-installed package directory may be read-only). Probe results likewise go into `fetched/<p>.json`, never back into git.
- **Runs offline**: the snapshot ships in git with the package, so even an offline install has full factory models; fetched files are local; models.dev live is a bonus.

## 2. File structure

```
openprogram/providers/                     ← all git-tracked, read-only at runtime
├── models_dev_snapshot.json               ← machine-generated: models.dev snapshot (the one "full list")
├── deepseek/
│   ├── provider.json                      ← all hand-written config for this provider (section 3)
│   └── deepseek.py                        ← wire/stream implementation (only for dedicated protocols)
├── model_registry/                        ← merge pipeline, defines MODEL_REGISTRY
│   ├── loader.py                          ← reads snapshot + provider.json + fetched
│   ├── models_dev.py                      ← live source + snapshot refresh script
│   └── merge.py                           ← merge + thinking derivation
└── models.py                              ← get_model / get_providers / get_models

~/.openprogram/                            ← user-machine state, written by the program
├── config.json                            ← enabled / enabled_models / keys / custom_models
└── fetched/
    └── deepseek.json                      ← official list pulled by Fetch + probe results
```

**Naming rule: the word "catalog" is retired entirely** (historically one word for five things — the root of the naming confusion). The merge pipeline is `model_registry` (its product is `MODEL_REGISTRY`); the webui presentation layer is `_model_listing/`. `models_generated.py`, `thinking_catalog.py`, and `_catalog_new.py` all retire.

**Providers without a directory** (fireworks, together, and other community providers): the snapshot already carries their models and base_url, so a user just enters a key; Fetch results go to the user directory — **no package directory is ever created**. Adding a builtin provider = writing one `provider.json`, and only when it needs overrides.

## 3. provider.json: the only hand-written file

All human configuration for a provider lives in one file; every field is optional:

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
  "models": [
    {"id": "some-model-not-on-models-dev", "context_window": 128000}
  ],
  "models_from": null
}
```

| Field | Purpose | Default behaviour |
|---|---|---|
| `endpoints` | api/base_url groups referenced by models by name (multi-wire providers like opencode have 4 groups, copilot 3; single-wire has just `default`) | models.dev base_url from the snapshot + OpenAI-compatible protocol |
| `thinking` | wire_format / effort maps / model_overrides (formerly thinking.json; see thinking-effort.md) | OpenAI-compatible fallback (low/medium/high) |
| `cache` | prompt-caching declaration (formerly cache.json) | no explicit cache control |
| `models` | model overrides or additions: headers, compat, `key_prefix` (dual keys), `endpoint` reference, and full specs for models the snapshot doesn't carry | model list comes entirely from snapshot/Fetch |
| `models_from` | data borrowing for subscription providers (e.g. claude-code → anthropic) | no borrowing |

**The test: if a machine can obtain a field, a human doesn't write it.** Writing an auto-obtainable field (price, context, model list) violates the design. Most providers' provider.json is a few endpoint lines — or the file doesn't exist at all.

Directory names use underscores (`amazon_bedrock/`); `id` keeps the hyphenated name (`amazon-bedrock`) — registry keys use hyphens. Same-service-multiple-protocols (Bailian's OpenAI-compatible + Anthropic-compatible endpoints) = two endpoints of one provider, not two providers.

## 4. The merge (single data outlet)

```
MODEL_REGISTRY = snapshot ∪ live models.dev ∪ fetched ∪ provider.json.models
                 (rightmost wins per field; model EXISTENCE follows fetched as authority —
                  for a fetched provider, rows absent from fetched are hidden from the UI
                  but kept in the registry so old sessions don't break)
                 + api/base_url filled from endpoints by each model's endpoint name
                 + thinking fields derived from provider.json.thinking (always derived, never stored)
```

Why this priority: hand-written = a human's explicit override, strongest; fetched = facts from the official API, next; live is newer than the snapshot. Three of the four sources are machine-maintained — humans own only the smallest one.

Output is `Model` objects keyed `"<prefix>/<id>"`; prefix defaults to `id`, overridable per row with `key_prefix` (the gemini-subscription dual-key case).

```python
# openprogram/providers/model_registry/__init__.py
MODEL_REGISTRY: dict[str, Model]   # the one and only runtime model registry
```

Naming note: not `ENABLED_MODELS` — the registry holds **all known models**, while "enabled" is user state in `config.json` (a list of id bookmarks pointing into the registry); two concepts must not share one name.

## 5. How the backend uses it

```python
get_model("deepseek", "deepseek-v4-flash")  # → Model | None, with alias fallback
get_providers()                              # → provider ids that have models
get_models("deepseek")                       # → all Models of that provider
```

The 20+ runtime callers (agent, runtime, failover, …) only know these three functions. `get_model` falls back through `auth.aliases` on a miss.

**Fetch write path**: "Fetch Models" in settings → call the official API (Anthropic additionally pulls per-model capabilities) → probe infers reasoning → the model list + probe results are atomically written to `~/.openprogram/fetched/<p>.json` → registry reloads → frontend refreshes. **The moment a new model appears in the settings page, `get_model` resolves it.**

**Snapshot refresh**: a maintainer runs the refresh script in `model_registry/models_dev.py`, regenerating `models_dev_snapshot.json` through a normal git commit — the only machine-written git file, and it only changes on the repository side; on user machines it is read-only.

**Custom models**: config's `custom_models` are written into `MODEL_REGISTRY` in place (the registry is one mutable dict); default api/base_url come from endpoints.

## 6. How the frontend uses it

The frontend has no model data of its own; three listing functions (`webui/_model_listing/`, pure presentation) read the same registry:

| Frontend surface | API route | Listing function | Content |
|---|---|---|---|
| Settings provider sidebar | `GET /api/providers` | `list_providers()` | all registry providers (incl. community providers via the snapshot) |
| Settings model table | `GET /api/providers/<id>` | `list_models_for_provider()` | that provider's registry rows + enabled flags |
| Chat model picker | `GET /api/models/enabled` | `list_enabled_models()` | delegates to the row above, filtered by config |
| Thinking level picker | (`_thinking.py`) | delegates to `list_models_for_provider` | thinking_levels from the same rows |

Enabled state lives only in `config.json`; checking/unchecking edits config only. Listing does no merging and no derivation — that is all finished in `model_registry`. webui imports providers; providers never import webui.

**End to end**: enable deepseek → Fetch (`fetched/deepseek.json` written, `deepseek/deepseek-v4-flash` appears in the registry) → check the box (config records the id) → pick it in chat and send (`get_model` hits the same registry row; endpoints give base_url; thinking derivation gives levels). No step involves a second list.

## 7. Invariants (check before changing code)

1. **Single outlet**: frontend display and runtime resolution come from the same merged result. A second data chain is a violation.
2. **Split by author**: human data in git; program data in the user directory; the package directory is read-only at runtime. A program writing a git file or the package directory is a violation.
3. **Minimal hand-writing**: provider.json only stores what machines cannot obtain.
4. **One-way layering**: `openprogram.providers` never imports `openprogram.webui`.
5. **Runs offline**: snapshot + fetched suffice; models.dev live is an enhancement.
6. **Key compatibility**: `"<prefix>/<id>"`, alias fallback, `key_prefix` dual keys (gemini-subscription's 10 keys) preserved; the registry stays one mutable dict.

## 8. Current deviations and migration

> Recorded 2026-07-08. Full problem description: [../PROBLEM-models-and-bailian.md](../PROBLEM-models-and-bailian.md).

### 8.1 Deviations

1. **Two data chains**: merge logic exists only in webui (`provider_models.combined_models` feeds the settings page) while the runtime registry reads only the hand-written git `models.json` — the settings page can select `deepseek-v4-flash`, `get_model` cannot find it.
2. **752 hand-written model rows** (22 providers' `models.json`), largely duplicating models.dev, with no update mechanism — already rotted. Replaced by the snapshot in the target state; only overrides remain.
3. **Five file kinds per provider** (provider.json / models.json / thinking.json / cache.json / models.fetched.json), split by content instead of by author. Converges to one provider.json.
4. **The program writes to the wrong places**: Fetch writes into the installed package directory (papered over with .gitignore); probe results write back into git-tracked thinking.json. Target: everything goes to `~/.openprogram/fetched/`.
5. **Inverted layering**: the models.dev source and merge logic live in `webui/_model_catalog/`, while the providers layer reads the registry back to fill api/base_url — a dependency cycle.
6. ~~`MODELS` too generic~~ renamed `MODEL_REGISTRY` (2026-07-08, 20 files). Definition still in `models_generated.py` for now.
7. **Non-standard bailian naming**: models.dev calls the same-base_url provider `alibaba-token-plan-cn`; the reserved empty directory exists; the user has asked to adopt the standard name and delete `bailian/`.

### 8.2 Migration order (each step commits independently, nothing breaks)

1. **bailian → alibaba_token_plan_cn**: rename directory + id to `alibaba-token-plan-cn`, models carried over. Small and independent — first.
2. **Land the snapshot**: write the refresh script, pull models.dev, commit `models_dev_snapshot.json`.
3. **Move fetched to the user directory**: `provider_models` read/write paths change to `~/.openprogram/fetched/<p>.json`; migrate the 4 existing in-package fetch results; drop the `.gitignore` line. Probe write-back moves from thinking.json into the fetched file.
4. **Unify config**: fold `thinking.json`, `cache.json`, and `models.json` (its post-slimming override part) into `provider.json`; `thinking_spec`/`cache_spec` read the new location. Migration script verifies per-provider equivalence.
5. **Sink the pipeline and connect it**: merge logic moves into `providers/model_registry/`; `_load()` runs the full merge (section 4); fetched and snapshot enter the registry. **The two chains become one here.** `_default_api_for`/`_resolve_base_url` read endpoints; the cycle dissolves.
6. **Thin out listing**: delete webui's own merge, read the registry directly; rename `_model_catalog/` → `_model_listing/`.
7. **Naming finish**: `MODEL_REGISTRY` definition moves into `model_registry/__init__.py`; retire `models_generated.py`, `thinking_catalog.py`, `_catalog_new.py`. No "catalog" left in the code.

### 8.3 What the migration must preserve (from prior reviews)

- **Aliases / dual keys**: gemini-subscription's `google-gemini-cli/*` + `gemini-subscription/*` are 10 keys with distinct names, carried by per-row `key_prefix`; deduplicating by `(provider, id)` drops 5.
- **The claude-code borrowing chain**: thinking alias→anthropic, models.dev data borrowed from anthropic, special fetcher — declared explicitly via `models_from` in provider.json; must not be lost.
- **Field-by-field fidelity**: `cost` is nested, `input` is multimodal, `headers` (copilot depends on it), `compat` — before deleting any hand-written field, confirm the merge layers restore an equivalent value.
- **Verification granularity**: multi-wire providers need one exec per `(api, base_url, headers, compat)` combination.
- Core regression tests stay green: `tests/unit/test_provider_wire_invariants.py`, `tests/unit/test_model_fetch_routing.py`.
