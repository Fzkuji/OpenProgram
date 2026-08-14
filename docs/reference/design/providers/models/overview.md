# Model Catalog and Provider Configuration

> This document describes the runtime logic of the model catalog: where data lives, how files interact with code, and how the backend and frontend each consume it.
> Thinking-effort parameter details are covered in [thinking-effort.md](thinking-effort.md).

## 1. Architecture in one sentence

**The system only remembers the models the user enabled.** The settings page is hierarchical — providers first, then models: the first level shows the provider list; only after opening one provider does the page query, live, which models **that provider** offers — and that query is never persisted. The act of **enabling** copies a model's full spec, as of that moment, into `config.json`. The runtime registry `ENABLED_MODELS` is those few dozen config rows — what `get_model()` resolves, what the chat page shows, and what the user checked are physically the same data.

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

**Naming rule: nothing here is called a "catalog".** One word for five different things is what made the naming confusing, so each module is named for what it holds: `enabled_models.py` for the registry, `storage.py` for config persistence, `thinking_spec.py` for the thinking declarations, and webui's `_model_listing/` for the presentation layer. The name `ENABLED_MODELS` is accurate only because the dict holds enabled models and nothing else.

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

Browsing has two levels. **Level one: the provider list** (settings landing view) = providers with a local `provider.json` ∪ models.dev's provider index — names and configuration status only, no model names. **Level two: the model list** — only after the user opens one specific provider does the page query that provider:

```
list_available_models(provider_id)
  = the provider's official source (one fetcher, see below)
  ⊕ models.dev (fills price/capabilities; full fallback when there is no key)
  → merged in memory, returned straight to the frontend
```

**Fetcher placement rule: a provider whose interface departs from the standard
OpenAI shape carries its own fetcher in its own directory.** Each source has a
different shape but **returns the same contract**: success → `list[dict]`
(each row has at least id/name), failure → `{"error": ...}`.

| Source shape | fetcher location | example |
|---|---|---|
| Standard `/v1/models` (OpenAI-compatible) | generic `_model_listing/fetchers/openai_compat.py` (shared fallback, owned by no single provider) | openai, openrouter, groq, custom gateways |
| Anthropic `GET /v1/models` + per-model capabilities | `providers/anthropic/list_models.py` | anthropic, claude-code, minimax |
| Account-level private endpoint (`/v1/models` is Cloudflare-blocked; read the subscription's model table instead) | `providers/openai_codex/list_models.py` (see fast-tier.md §2.1) | openai-codex |
| Vendor-specific list API (shape / auth differ from OpenAI-compatible) | `providers/<name>/list_models.py`: `google` (query-param key + `models/<id>` prefix) / `amazon_bedrock` (boto3 SigV4, not HTTP) / `github_copilot` (session bearer + capabilities envelope) / `deepseek` (id-only, enriched after) | the matching provider |

**Convention loading**: a provider whose interface departs from standard ships
a `list_models.py` in its own directory exposing `fetch(provider_id, timeout)`;
the dispatcher's `_load_fetcher` `__import__`s it by directory name — the exact
mechanism `probe_thinking.probe()` uses, so adding a provider needs no central
edit. A standard-interface provider ships no such file and uses the generic
`openai_compat`. Whether the file exists = whether the provider's interface
departs from standard — a natural, on-demand test.

Whatever the source, `fetch_and_normalize` is the **single normalisation choke
point**: it collapses each fetcher's disparate keys (`context_length` /
`context_window` / `contextWindow`, …) into one entry dict, then layers
models.dev on top. Downstream only ever sees the normalised row — never the
source differences.

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
| Settings landing view: provider list (no model names) | `GET /api/providers` | providers with a provider.json + those models.dev lists live (community providers configurable directly) |
| Provider detail view: browse/check **that provider's** models | `GET /api/providers/<id>/available` | **live**: the level-two browse result of 4.1 + enabled flags |
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
7. **Many sources, one shape**: whether a row comes from an official `/v1/models`, an account-level private endpoint (codex), the models.dev community catalogue, or a hand-typed entry, it is normalised into one row shape by the single `fetch_and_normalize` function; enabling routes through the single `_upsert_spec_row` choke point into config (`_normalize_spec_row` fills the Model-schema keys); reading routes through the single `_build_model_from_row` converter into a `Model`. Each of the three choke points is exactly one; nothing may bypass them to mint its own shape.

## 8. Implementation status

The design above is what the code does. The registry is `ENABLED_MODELS`, defined in `enabled_models.py`; the webui presentation layer is `_model_listing/`; enabling a model copies its full spec into config; browsing is live with no disk persistence; and `thinking.json` and `cache.json` are folded into `provider.json`, so `_default_api_for` / `_resolve_base_url` read endpoints directly and the providers layer no longer reads the registry back.

Properties that any change here must preserve:

- **Enablement survives**: a config row must resolve through `get_model` to the same `Model` it did before, for every provider.
- **Aliases and dual keys**: gemini-subscription's `google-gemini-cli/*` and `gemini-subscription/*` are 10 keys with distinct names. Enabled rows carry their own key and name, and the alias fallback stays in place.
- **The claude-code borrowing chain**: browse data borrowed from anthropic via `models_from`, 3 models auto-enabled after login, and its own fetcher.
- **Field-by-field fidelity**: nested `cost`, multimodal `input`, `headers` (copilot depends on it), and `compat` all travel in the spec written to config at enable time.
- **Verification granularity**: multi-wire providers need one exec per `(api, base_url, headers, compat)` combination.
- `tests/unit/providers/test_provider_wire_invariants.py` and `tests/unit/providers/test_model_fetch_routing.py` stay green.
