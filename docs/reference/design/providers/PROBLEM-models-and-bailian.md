# Model list and the Bailian provider

This note records a structural problem in the model list mechanism, plus one naming
inconsistency in the Bailian provider. For the target state and the resolution logic,
see [`models/overview.md`](models/overview.md).

## 1. Concepts involved

- **provider**: a model vendor, such as OpenAI, DeepSeek, or Bailian. In the code each
  provider gets its own folder: `openprogram/providers/<name>/`.
- **model registry**: the master list of "every model currently enabled" at runtime.
  Anywhere in the program that needs a model looks it up here
  (`get_model("deepseek", "xxx")`); 20+ runtime files depend on it (runtime, agent,
  failover, and others).
- **models.json** (one per provider folder, committed to git): the spec list of "which
  models this provider has enabled". The registry is simply the concatenation of every
  provider's `models.json`.
- **models.fetched.json** (per provider folder, not committed to git): a cache of the
  model list pulled from the provider's official API when the user clicks "Fetch Models"
  on the settings page. Only 4 providers have ever been fetched.
- **models.dev**: a third-party public site (`https://models.dev/api.json`) that catalogs
  model specs (context length, pricing, capabilities) for 151 providers, used as a
  reference manual.

## 2. The two model lists disagree

The system has two model data chains that are never synchronized with each other:

| | Chain A: registry (used by running code) | Chain B: model picker on the settings page |
|---|---|---|
| Data source | each provider's `models.json` (hand-written, in git) | `models.fetched.json` (Fetch cache) + models.dev |
| Who uses it | all backend code, via `get_model()` | the model selector on the webui settings page |
| Implementation | `models_generated._load` → `_catalog_new.load_new_catalog` | `provider_models.combined_models` |

The two chains hold different data. Take DeepSeek: chain A has the two older models
`deepseek-chat` and `deepseek-reasoner`; models.dev has four, `deepseek-v4-flash`,
`deepseek-v4-pro`, `deepseek-reasoner` and `deepseek-chat`; the Fetch cache behind
chain B has the two newer ones, `deepseek-v4-flash` and `deepseek-v4-pro`.

The result is that a user picks `deepseek-v4-flash` on the settings page and the backend
call `get_model("deepseek", "deepseek-v4-flash")` cannot find it — the settings page
offers a model the code does not know about.

The root cause is that the `models.json` feeding the registry is hand-written and nothing
updates it automatically, while Fetch and models.dev are live and do update, but their
results never reach the registry. The comment at the top of `models_generated.py` states
that the original design intent was "Fetch rewrites this file directly, no manual
maintenance needed", but the implementation never delivered that — Fetch writes
`models.fetched.json` while the registry reads `models.json`, two different files.

The "models.dev as primary data source + layered overlay" described in
[`models/overview.md`](models/overview.md) is implemented only in chain B; chain A was never
wired up to it.

## 3. Naming of the Bailian provider

In this project the provider is called `bailian` (`providers/bailian/`, 14 models, using
the OpenAI-compatible format). models.dev calls the same thing (same base_url,
`token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`) `alibaba-token-plan-cn`,
and catalogs 18 models for it. The project also contains an empty folder
`providers/alibaba_token_plan_cn/`, reserved as the slot for this provider.

Converging on the models.dev canonical name `alibaba-token-plan-cn` is the direction
consistent with the naming rules; this is independent of the list mechanism problem in
section 2 and can be handled separately.

## 4. Target state

The direction set out in [`models/overview.md`](models/overview.md) is: every provider is
self-contained (all its configuration lives under `providers/<p>/`); models.dev serves as
the primary data source for the model list, pricing and capabilities; `thinking`
declarations supply the thinking tiers; and the required fields of the registry schema are
only `id/name/api/provider/base_url`, where `api` and `base_url` come from `provider.json`.

Under that design the model list is no longer maintained by hand, the two data chains
become one, and the divergence between chain A and chain B goes away.
