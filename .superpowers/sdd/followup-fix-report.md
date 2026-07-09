# Follow-up fix: enabled-models migration picker flood + table regression

Branch `refactor/enabled-models`, commit `a7c11c01`. Backup of real config:
`~/.openprogram/config.json.bak-20260708-235218`.

## Root cause

v1 `_migrate_specs` merged EVERY legacy `custom_models` row into
`providers.<p>.models` tagged `source:"manual"`. For community providers the
OLD Fetch flow cached the provider's ENTIRE upstream catalogue in
`custom_models` (openrouter 399 rows, only 3 in `enabled_models`; openai 134,
provider disabled). That key was an availability cache, never "user enabled
these". The new enabled-only registry treats every `models` row as enabled, so
the chat picker showed 465.

## Root-cause fix (migration semantics)

`_migrate_specs`: a `custom_models` row is promoted to a spec row ONLY if its id
is in that provider's legacy `enabled_models`. Non-enabled rows stay in the
untouched `custom_models` key (rediscoverable via live browse; nothing lost).

## Repair pass and why it's precise

`_repair_over_merged_specs` (versioned, config marker `spec_migration_version: 2`,
one-shot per machine): for each provider, DROP every `models` row with
`source == "manual"` whose id ∉ legacy `enabled_models`. This is an EXACT
reversal of the bulk merge:
- `toggle_model` enable writes a spec row via `spec_row_for`, which strips only
  the `enabled` UI flag — it writes NO `source` key. So a `source:"manual"` row
  can ONLY have come from the v1 merge.
- A manual row whose id ∉ `enabled_models` is therefore a bulk-merge artefact,
  never a genuine user action → safe to drop.
- Rows with no `source` (toggled since migration) and rows with id ∈
  `enabled_models` are kept.
- `source:"migration-minimal"` rows keep their semantics: id ∈ `enabled_models`
  by construction, so the pass never touches them.

The picker provider-level gate (`list_enabled_models` skips providers whose
`enabled` is false) already existed on the branch; verified and pinned with a
new regression test. `ENABLED_MODELS._load()` still loads disabled providers'
rows into the registry (registry = runnable), but after repair those providers
have zero rows anyway, and the picker gate keeps them out regardless.

## bailian finding

bailian has `enabled_models: null` (0 legacy enabled) and its 4 `models` rows
are all `source:"manual"` (an availability cache from browse). Per the repair
rule all 4 are pruned → bailian contributes 0 to the picker. Confirmed there are
no no-`source` rows there that should survive.

## Per-provider picker counts after the real-config repair

Before: 465 total. After: 33 total.

| provider | picker count | note |
|---|---|---|
| openrouter | 3 | 3 legacy-enabled kept |
| claude-code | 4 | 2 config rows + 3 dynamic seeds, one (`claude-opus-4-8`) overlaps → 4 unique |
| openai-codex | 12 | 1 config row + import-time seed of the known Codex ids (same seed mechanism as claude-code; Codex has no custom-model fallback so listed==registered). Not a regression. |
| claude-max-proxy | 11 | all `migration-minimal`, all enabled — kept |
| chatgpt-subscription | 1 | |
| deepseek | 1 | |
| minimax-cn-coding-plan | 1 | |
| openai | 0 | disabled provider, 134 rows pruned |
| anthropic | 0 | disabled provider, 9 rows pruned |
| bailian | 0 | 4 manual cache rows pruned (legacy_enabled=0) |

`spec_migration_version` bumped to 2 in the real config.

## Table-regression ("表变丑") findings

Investigated `list_models_for_provider` (settings table) and the picker against
`db291679~1` (`_model_catalog/listing.py`). Findings:

- The settings TABLE is a LIVE browse in both old (`combined_models`) and new
  (`_browse_models`) code, iterating models.dev in the same dict order — ordering
  unchanged. Its consumer (`web/components/settings/providers/model-list.tsx`)
  reads `reasoning`, `context_window`, `input_cost/output_cost/cache_*_cost`;
  those flat models.dev fields pass through `list_models_for_provider`'s
  `{k:v for k,v in raw.items() if not k.startswith("_")}` intact. Verified live:
  `ring-2.6-1t → reasoning:True, thinking_levels:[low,medium,high]`,
  `input_cost` present. The table itself did NOT lose fields.
- The visible "ugliness" was the PICKER flood: it reshapes stored spec rows
  (`_model_to_dict` over `ENABLED_MODELS`), and the openrouter manual dumps
  carried `reasoning:False, thinking_levels:[]`. Example before repair (openrouter
  picker row): `{id: anthropic/claude-opus-4.7, reasoning: False, thinking_levels: []}`
  — one of 399 flood rows. After repair the picker shows only the 3 enabled
  openrouter rows.
- No `_model_to_dict` change was warranted: the chat picker frontend
  (`web/lib/api.ts` `mapModel`) consumes only id/name/vision/video/tools/
  reasoning/context_window/enabled/provider — every field `_model_to_dict`
  already emits. `cost`/`thinking_levels` aren't read by the picker. Re-adding a
  live browse to enrich sparse enabled rows would violate the new "picker = no
  network" design; the remaining field sparsity on minimal enabled rows is a
  data-capture limitation from what was stored at enable time, not a code
  regression. `web/` left untouched.

## TDD evidence

- Updated `test_migration_merges_only_enabled_custom_models_as_manual`
  (was `..._merges_custom_models_as_manual`): a non-enabled custom row is NOT
  promoted; enabled one is; `custom_models` key untouched.
- New `test_repair_prunes_non_enabled_manual_rows_only`: drops manual/non-enabled,
  keeps enabled-manual + no-source + migration-minimal.
- New `test_repair_is_one_shot_via_version_marker`: marker at target → no-op.
- New `test_repair_pass_runs_through_run_once`: end-to-end via
  `_run_spec_migration_once`, prunes + bumps marker.
- New `test_disabled_provider_rows_in_registry_absent_from_picker`: disabled
  provider's registry rows absent from picker, enabled provider's present.
- No test reads/writes the real config (fixture seams / stubbed `_load_config`).

## Suite results

Green: `test_model_spec_copy.py`, `test_enabled_models_community.py`,
`test_registry_from_config.py`, `test_browse_live_and_refresh.py`,
`test_provider_wire_invariants.py`, `tests/providers/` — 91 passed / 16 skipped
/ 5 xfailed together. Full `tests/unit/` once: 1113 passed, only the 3 known
pre-existing failures (2 `test_context_route`, 1 `test_graph_layout` from the
concurrent session). Integration ModuleNotFound not run.

## Concerns

- openai-codex shows 12 picker rows via its import-time seed (parallel to the
  claude-code seed the task said not to redesign). Acceptable but noted; if the
  picker should show only config-enabled Codex ids, the seed mechanism (not this
  migration) would need revisiting.
- Committed only `storage.py` + 2 test files; the concurrent session's
  `graph_builder.py`, `graph_layout/*`, `web/.../dag/*` edits were left unstaged
  and out of the commit.

---

## Follow-up C2: import-time seed flood → login-enable (subscription providers)

### Seeding that existed (removed)
- `openprogram/providers/anthropic/_claude_code_registry.py` — `_seed_claude_code_models()` called at module import, dict-wrote 3 claude-code rows into `ENABLED_MODELS` (no config). REMOVED; module kept as the documented claude-code wire-mapping anchor.
- `openprogram/providers/openai_codex/runtime.py` — `_augment_registry_with_codex_models()` called at import, mirrored all 11 `_KNOWN_CODEX_MODELS` into `ENABLED_MODELS` via `ensure_codex_model_registered`. Import-time call REMOVED. `ensure_codex_model_registered` KEPT as the runtime-registration helper (live Fetch `fetchers/codex.py` + runtime miss-path still use it; it needs a config-backed `openai-codex` template row, which now comes from a login-written spec row). `_KNOWN_CODEX_MODELS` KEPT for `list_models()`.
- `openprogram/providers/enabled_models.py` — `reload()` called `_reapply_dynamic_seeds()` (fix C1) to re-seed claude-code after clearing the dict. Both REMOVED; `reload()` now rebuilds from config spec rows only.

### Login-enable seam
New module `openprogram/auth/login_enable.py`:
- `enable_default_models_on_login(provider_id)` — writes the default set as config spec rows via storage's `_upsert_spec_row`/`_write_providers_cfg` (marked `source: "subscription-login"`), then `enabled_models.reload()`. Lazy import of webui storage (auth→webui is allowed; the forbidden direction is providers→webui — untouched).
- `seed_default_models_if_logged_in(provider_id)` — first-run convenience; fires `enable_default_models_on_login` iff credentials already exist. Credential existence resolved through the login pool id (`_credential_provider_id`, so claude-code checks the `anthropic` pool).

Wired at every successful-login surface:
- Web: `webui/routes/provider_login.py` `_drive()` after `persist(cred)`.
- CLI: `auth/cli.py` `_cmd_login` after `store.add_credential` — keyed on the USER-facing `provider` (not `cred.provider_id`, which routes claude-code to `anthropic`).
- First-run adoption: `auth/interactive.py` `_action_import_credentials` after adopting vendor-CLI credentials.
- NOT hooked into `list_enabled_models()` — a read path must not write config; an earlier picker-hook attempt caused real-config pollution (see Concerns) and was reverted.

### Idempotence rule (implemented)
Defaults are written ONLY when the provider currently has ZERO `providers.<p>.models` spec rows (fresh provider). Any prior enable/disable leaves a non-empty list → login/import is a no-op → a disabled default never resurrects. Verified end-to-end (fresh→writes 3; re-login→[]; disable sonnet→re-login keeps sonnet gone).

### Codex default-set choice
`_DEFAULTS["openai-codex"] = [gpt-5.5, gpt-5.5-codex]` — a SMALL set, not all 11. Justification: the seed list led with the `gpt-5.5` flagship family; `gpt-5.5` is the runtime's default chat model and `gpt-5.5-codex` is the coding variant this tool is built around. The rest are fetch-on-demand from Settings. claude-code default = the original 3 seed models (opus-4-8, sonnet-4-6, haiku-4-5).

### Per-provider picker counts (real config, READ-ONLY, current code)
- claude-code: 2 (claude-opus-4-8, claude-opus-4-5-20251101) — the user's config rows, no phantom seeds.
- openai-codex: 1 (gpt-5.5) — no 12-row flood.
- (others unrelated: openrouter 3, claude-max-proxy 11, chatgpt-subscription 1, deepseek 1, minimax-cn-coding-plan 1; total 20 rows.)
Real config never written (verified via md5 before/after). No repair needed — removing seeds only subtracts rows the user's config already covered.

### Dependents audit
- `_register_custom_model_in_registry` (webui `_runtime_management.py`, `agent/_model_tools.py`) — genuine runtime custom-model registration; KEPT, unaffected.
- `ensure_codex_model_registered` — KEPT; live Fetch + codex runtime `__init__` miss-path. Requires a config-backed codex template; login-enable now supplies it. Runtime miss-path only fires for a model already in config (so template present).
- claude-code browse/fetch borrows the anthropic live path — no seed dependency, unaffected.
- Never-enabled seeded id in an old session → `get_model` → None → `dispatcher._resolve_model` raises `LLMError(INVALID_REQUEST)` with a clear message (no crash). Confirmed by existing `test_unknown_model_raises_instead_of_swapping` / `test_unknown_bare_id_raises`.

### TDD evidence
`tests/providers/test_login_enable.py` (new, 7 tests) written first: `test_reload_has_no_seed_resurrection` FAILED against the pre-change reload (seed reapplied) → passed after removal. Also pins fresh-login writes defaults, small codex default, idempotence/disable-respect, credentials-exist seeding. C1 regression test `test_reload_reapplies_claude_code_seed` REWRITTEN to `test_reload_has_no_seed_resurrection` (new invariant: config rows survive, phantom claude-code rows don't). `test_claude_code_dynamic_registration_lands_in_registry` rewritten to `test_claude_code_rows_come_from_config_not_import_seed`. Updated seed-referencing comments/asserts in `test_dual_source_load.py`, `test_browse_live_and_refresh.py`.

### Suite results
Target suites all green: test_registry_from_config, test_enabled_models_rename, test_browse_live_and_refresh, test_model_spec_copy, test_provider_wire_invariants, tests/providers/, test_login_enable, test_resolve_model, tests/auth/ (57 in the combined target run + full providers suite). Full `tests/unit/`: 1113 passed, 3 failed = the known pre-existing failures only (2 `test_context_route`, 1 `test_graph_layout` from the concurrent session). Full unit suite verified NOT to write real config (md5 unchanged).

### Concerns
- During iteration, an earlier attempt to seed defaults from inside `list_enabled_models()` (a read path) caused a REAL-config write when `test_enabled_models_reads_registry` invoked the picker with live credentials present but writes unpatched — it dropped claude-code rows and injected codex defaults. Reverted; restored `~/.openprogram/config.json` from `config.json.bak-20260708-235218` (polluted copy saved as `config.json.POLLUTED-*`). Current code never writes on read; re-verified real config is byte-identical after the full unit suite.
- `test_reload_has_no_seed_resurrection` uses the REAL `reload()` (mutates shared `ENABLED_MODELS`) — same pattern the old C1 test used; no observed leakage, but it's a shared-state test.

---

# Follow-up 2: live per-provider verification fixes (F1–F4)

Branch `refactor/enabled-models`. TDD, fixture-driven; no test touches real config/network. `~/.openprogram/config.json` untouched (byte-identical before/after). API shapes unchanged; `web/` untouched.

## F1 — minimax-cn-coding-plan empty base_url (FIXED, root cause)

NOT an empty-string-in-row problem: `_build_model_from_row` already fills on falsy (`if not data.get("base_url")`). The `minimax-cn-coding-plan/MiniMax-M3` row carries NEITHER `api` NOR `base_url` (both keys absent). Root cause: `openprogram/providers/minimax_cn_coding_plan/` is an **empty provider dir — no `provider.json`** — so `provider_endpoints("minimax-cn-coding-plan")` returned `{}` and the fill fell to `""`.

First attempt filled from models.dev — but the live retest still FAILED (base_url still empty in a fresh process). Deeper cause: models.dev is an **in-memory, network-fetched, 1-hour-TTL cache that is EMPTY at cold import**, and `ENABLED_MODELS` snapshots at import (`enabled_models.py:63`). A warm `reload()` filled it; a fresh `create_runtime` process did not.

Final fix in `openprogram/providers/_provider_meta.py` — new `resolved_endpoints(provider_id)`, fill order:
1. own `provider.json`;
2. alias target's `provider.json`;
3. **region-sibling OFFLINE**: a `-coding-plan` token-plan provider shares its region sibling's wire (same account, same endpoint) — strip `-coding-plan` → `minimax-cn`, whose real `provider.json` gives `anthropic-messages` + `https://api.minimaxi.com/anthropic`. Deterministic, no network;
4. models.dev base_url (with `anthropic-messages` when the base ends `/anthropic[/v1]`) as a last resort.
`provider_endpoints` delegates to `resolved_endpoints`, so `_load` inherits the fill unchanged. Verified at COLD import: `get_model("minimax-cn-coding-plan","MiniMax-M3")` → `anthropic-messages`, `https://api.minimaxi.com/anthropic`.

**Config scan** (real config, read-only): only rows with falsy api/base_url are `claude-max-proxy/*` (11 rows, `base_url:""` — OAuth/proxy provider that fills base_url at auth time; expected) and `minimax-cn-coding-plan/MiniMax-M3` (both absent — the F1 case). `chatgpt-subscription/gpt-5.5` and `openai-codex/gpt-5.5` carry `api:null,base_url:null` (also absent → filled by the same path). Empty got in because the row was persisted without api/base_url and the provider dir has no provider.json — not an empty-string backfill.

## F2 — AgentState seed crash (FIXED, root cause)

`agent.py:106` seeded `AgentState(model=get_model("google","gemini-2.5-flash-lite-preview-06-17"))` → None post-migration → pydantic rejects, crashing EVERY Agent construction. Fix: `AgentState.model` is now `Model | None = None` (`agent/types.py`) and `agent.py` seeds `model=None`. The real model is always applied immediately from `opts.initial_state` (AgentSession always supplies one — `session.py:82`), so None is a transient placeholder never observed downstream.

**All hardcoded provider/model fallbacks in runtime paths scanned** — only `agent.py:106` crashed; every other site already None-guards:
- `agent/internals/_auto_classifier.py:46-49` — `get_model("anthropic",…) or …`; `if model is None: return True,…`. Safe.
- `agent/_model_tools.py:242` and `agent/internals/_model_tools.py:261` — `get_model("anthropic",…)` behind `if m:`. Safe.
- `providers/openai_codex/runtime.py:212` — `if _get_model("openai-codex",model) is None:`. Safe.
- `mixture_of_agents` / `llm_bridge` — no hardcoded `get_model("provider",…)` fallbacks.

## F3 — chatgpt-subscription credential resolution (REAL bug, FIXED — two seams)

Verdict: real bug. Two distinct breaks, both fixed:
1. **Endpoint routing** (same as F1): `chatgpt-subscription` is `auth.aliases → openai-codex`; its provider dir is also empty. Pre-fix it filled to `api="openai-completions"` + empty base_url and hit the openai-completions wire → `resolve_provider_key("chatgpt-subscription")` → None. `resolved_endpoints` now resolves the alias to `openai-codex`'s `provider.json` → `api="openai-codex"`, so it routes to the codex transport (which hardcodes the canonical id for credential lookup).
2. **Credential seam** (`openprogram/providers/openai_codex/openai_codex.py` `_resolve_codex_bearer_token`): it read `payload.access_token`, which is **None** on the stored OAuth credential — the live bearer is in `payload.auth_value` (1952 chars). This silently broke codex AND chatgpt-subscription (which routes through the SAME resolver). Fixed to read via the canonical `resolve_connection(cred).auth_value`. Evidence: `~/.openprogram/auth/openai-codex/default.json` (kind=oauth, `access_token=None`, `auth_value` present, `expires_at_ms` in the future); no `chatgpt-subscription` pool. Post-fix `_resolve_codex_bearer_token(None)` returns the 1952-char bearer. (This fix also repairs plain `openai-codex`, which was live-broken by the same attribute mismatch though it wasn't in the F3 retest set.)

## F4 — claude-code / claude-max-proxy (INVESTIGATE ONLY — both real missing credentials, environmental)

Read-only auth-store evidence:
- **claude-code** → aliases from the `anthropic` pool. `~/.openprogram/auth/anthropic/` holds ONLY `default.json.lock` — **no credential**. `_claude_code_direct_runtime.py` fails construction at `resolve_api_key_sync("anthropic")` → None → "No Claude credential". **Real missing credential** (no anthropic login). Not a code bug.
- **claude-max-proxy** → no `~/.openprogram/auth/claude-max-proxy/` pool at all, and its **local daemon is not running** (no LISTEN socket). Api-key wire raises "No API key configured". **Real missing credential + daemon down.** Not a code bug.

Both environmental; no fix, no retest possible.

## Retest (affected providers, tiny "Say OK" prompt, ALL_PROXY unset / HTTP_PROXY kept)

| provider / id | api | base_url | result |
|---|---|---|---|
| minimax-cn-coding-plan/MiniMax-M3 | anthropic-messages | https://api.minimaxi.com/anthropic | **PASS** 4.2s, reply "OK" |
| chatgpt-subscription/gpt-5.5 | openai-codex | https://chatgpt.com/backend-api | **PASS** 2.8s, reply "OK" |

(claude-code / claude-max-proxy not retested — F4 environmental.)

## TDD evidence

- `tests/providers/test_registry_from_config.py`: `test_coding_plan_provider_fills_from_region_sibling_offline` (F1 — asserts models.dev is NOT consulted), `test_community_provider_base_url_filled_from_models_dev` (F1 models.dev fallback), `test_alias_provider_endpoints_resolve_from_canonical` (F3 endpoint half). RED before the `_provider_meta` fix, GREEN after.
- `tests/unit/test_agent_seed_no_hardcoded_model.py` (F2): 3 tests, RED→GREEN.
- `tests/unit/test_codex_auth_adapter.py::test_resolve_codex_bearer_reads_auth_value_not_access_token` (F3 credential seam — fixture oauth cred with `auth_value` and no `access_token`): verified RED against the old `access_token` logic, GREEN with the fix.

## Suite results

- `tests/providers/`: 76 passed, 16 skipped, 5 xfailed.
- `tests/unit/`: passed except the known pre-existing set only (`test_context_route` ×2, `test_graph_layout` — concurrent session's DAG/graph edits). No new failures.

---

## Follow-up fix: codex BROWSE path floods runtime registry (2026-07-09)

**Symptom.** `/api/models/enabled` showed `openai-codex=14` while the user enabled 1. Root cause: `fetchers/codex.py::_fetch_codex_live` called `ensure_codex_model_registered(mid)` for every runnable models.dev id, bulk-injecting ~13 rows into `ENABLED_MODELS`. Post-migration that dict means "enabled"; browse is a read path and must not write it.

**Fix.** Removed the `ensure_codex_model_registered(mid)` call and its import from the fetcher loop; updated the module docstring and the loop comment (the stale "A listed id must also be dispatchable — register it" comment) to the new reality: dispatchability comes from enable-time config spec rows + `OpenAICodexRuntime`'s on-miss single-model registration.

**Sweep — every `ENABLED_MODELS[...]` writer + `ensure_codex_model_registered` caller (non-test):**

| Site | Kind | Verdict |
|---|---|---|
| `webui/_model_listing/fetchers/codex.py:83` (`ensure_codex_model_registered` in browse loop) | bulk browse write | **FIXED** — removed |
| `providers/openai_codex/runtime.py:88` (inside `ensure_codex_model_registered`) | single-model helper body | keep — used by runtime on-miss + resolve fixture |
| `providers/openai_codex/runtime.py:213` (`__init__` on-miss) | single-model runtime on-miss (c) | keep — makes the one dispatched id resolvable |
| `providers/anthropic/_claude_code_direct_runtime.py:121` (on-miss register) | single-model runtime on-miss (c) | keep — analogous, dispatches one id |
| `webui/_runtime_management.py:284` (`_register_custom_model_in_registry`) | enable-time config spec row (b) | keep |
| `webui/_runtime_management.py:330` (`_register_custom_model_in_registry` legacy custom_models) | enable-time config row (b) | keep |
| `webui/routes/tree.py:239` (`cands.insert(0, ENABLED_MODELS[mid])`) | READ | keep |

No other bulk-browse writer found. The codex fetcher was the only one.

**TDD.** Added `tests/unit/test_model_fetch_routing.py::test_codex_browse_does_not_grow_registry`: stubs the models.dev catalogue with 3 runnable codex ids, runs `_fetch_codex_live`, asserts the 3 rows are returned AND `ENABLED_MODELS` is byte-for-byte unchanged. Confirmed RED against the pre-fix fetcher (git-stash of just codex.py → 1 failed), GREEN with the fix. No existing test asserted the old bulk-registration behavior, so none needed updating; `test_resolve_model.py` uses `ensure_codex_model_registered` directly as the whitelisted single-model on-miss path (unchanged, still passes).

**Suites.**
- `test_model_fetch_routing.py` + `test_browse_live_and_refresh.py` + `test_resolve_model.py` + `tests/providers/`: 104 passed, 16 skipped, 5 xfailed.
- Full `tests/unit/`: 1118 passed, 4 skipped; 3 failed = the known pre-existing set (2 `test_context_route`, 1 `test_graph_layout` — the latter from the concurrent session's unstaged graph edits). No test touched real config/network.

---

## Follow-up: models.dev community tier "missing" from settings provider list

**Root cause.** No code regression — tier-2 in `list_providers()` was intact.
The reported "only 6 providers" was read off the WRONG endpoint: `GET
/api/providers` (legacy `routes/runtime.py` → `_list_providers()`, shape
`{name,available,active}`) is the runtime provider list, not the settings page.
The settings page uses `GET /api/providers/list` → `_mc.list_providers()`
(shape `{id,enabled,model_count}`), which already returns the full
registry ∪ models.dev index. The one real latent bug that COULD reproduce the
6-provider symptom: `sources/models_dev.py::_load()` cached a failed/empty
fetch as a success for the full 1h TTL, so a transient network blip at server
startup would hide the entire community tier for an hour.

**Evidence.**
- In-process fetch (server env, `env -u ALL_PROXY`, HTTP_PROXY on :7890):
  `httpx.get(models.dev/api.json)` → 200, 151 providers; `models_dev.list_providers()` → 151.
- Running server (started `env -u ALL_PROXY`, HTTP_PROXY/HTTPS_PROXY set, no
  ALL_PROXY): `/api/providers` → 6 (`name` shape); `/api/providers/list` → 154
  (`id` shape). So models.dev was reachable from the live process the whole
  time; the settings endpoint was never actually broken.

**Fix.** `_load()` now caches a non-empty success for `_TTL_SECONDS` (1h) but a
failed/empty result for only `_FAIL_TTL_SECONDS` (60s) — an empty models.dev
response is no longer pinned as success. Proxy handling untouched (httpx reads
standard env vars correctly; no `proxies=` misuse). File:
`openprogram/webui/_model_listing/sources/models_dev.py`.

**Test.** `tests/providers/test_models_dev_cache.py` (fixture-driven, no
network — httpx.get stubbed): failed fetch not cached as success (retries
after fail-TTL), success cached for full TTL, tier-2 providers surface in
`list_providers()` with `community_source="models.dev"`.

**Live verification** (after restart, same launch command):
- `GET /api/providers/list` → **154** providers (community tier present),
  `claude-max-proxy` present: **False**.
- `GET /api/models/enabled` → **9** rows, `claude-max-proxy` present: **False**.

**Suites.** `test_browse_live_and_refresh.py` + `tests/providers/` + wire
invariants: 92 passed / 16 skipped / 5 xfailed. Full `tests/unit/`: 1118
passed, only the 3 known pre-existing failures (2 test_context_route, 1
test_graph_layout from concurrent session).

---

## Alibaba/Bailian provider cleanup (2026-07-09)

### Entry inventory (from `list_providers()` / `/api/providers/list`)
Six Alibaba rows exist; all but the CN token plan are stock models.dev community entries:
| id | label (models.dev) | base_url | source |
|---|---|---|---|
| `alibaba` | Alibaba | dashscope-intl…/compatible-mode/v1 | models.dev (Tier 2) |
| `alibaba-cn` | Alibaba (China) | dashscope.aliyuncs.com/compatible-mode/v1 | models.dev |
| `alibaba-coding-plan` | Alibaba Coding Plan | coding-intl.dashscope…/v1 | models.dev |
| `alibaba-coding-plan-cn` | Alibaba Coding Plan (China) | coding.dashscope…/v1 | models.dev |
| `alibaba-token-plan` | Alibaba Token Plan | token-plan.**ap-southeast-1**.maas…/v1 | models.dev (INTERNATIONAL) |
| `alibaba-token-plan-cn` | **Alibaba Token Plan (China)** | token-plan.**cn-beijing**.maas…/v1 | models.dev + `providers/alibaba_token_plan_cn/provider.json` |

The user's provider is `alibaba-token-plan-cn`. It has NO ENABLED_MODELS rows (models.json deleted in the enabled-models migration), so it surfaces via Tier 2 (models.dev) only, plus its `provider.json` carries the cn-beijing default endpoint.

### What the logo-less "china" entry was
It was **not a separate hand-made row** in the current branch code. `list_providers` never iterates config keys — it only merges the static registry with the models.dev catalogue, both alias-aware. The user saw it on a running backend carrying legacy state. Two real defects underlay the report:
1. **Missing logo**: `web/components/settings/lobe-icons.ts` mapped `alibaba`, `alibaba-cn`, `alibaba-coding-plan`, `alibaba-coding-plan-cn` to the `alibaba` slug but NOT `alibaba-token-plan` / `alibaba-token-plan-cn` — so those rows fell through to the models.dev-SVG / letter-avatar fallback and looked logo-less.
2. **Config under the wrong key**: `~/.openprogram/config.json` held the enabled state (`enabled: true`, 4 `custom_models`) under the legacy `bailian` key, not `alibaba-token-plan-cn`. `_is_configured` is alias-aware (reads the auth-store credential, canonicalising `bailian→alibaba-token-plan-cn`), so `configured` was already correct — but `enabled`/`custom_models` are read by literal key (`cfg.get('alibaba-token-plan-cn')`), so the user's `enabled: true` was being dropped.

### models.dev findings
models.dev's index **does** carry `alibaba-token-plan-cn` — name "Alibaba Token Plan (China)", api `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`, env `ALIBABA_TOKEN_PLAN_API_KEY`, 18 models. It also carries `alibaba-token-plan` (international, ap-southeast-1), both coding-plan variants, and `alibaba` / `alibaba-cn` (pay-as-you-go dashscope). No provider carries per-id logo metadata beyond the models.dev SVG CDN; the sidebar keys logos by id through `lobe-icons.ts`, which is why the missing entry mattered.

### Changes
**Code (committed 042dddd6):**
- `web/components/settings/lobe-icons.ts`: add `"alibaba-token-plan"` and `"alibaba-token-plan-cn"` → `{ slug: "alibaba", hasColor, hasMono }`. Label + base_url already correct from models.dev; no `_PROVIDER_LABELS` override needed.
- `tests/providers/test_alibaba_token_plan_cn_rename.py`: +2 tests guarding the cn-beijing base_url (not international ap-southeast, not coding-plan) and the two lobe-icon map entries. File now 6 passing.

**Config (NOT committed — user file):** migrated `~/.openprogram/config.json` provider key `bailian` → `alibaba-token-plan-cn` (moved `enabled:true` + 4 custom_models). Backup: `~/.openprogram/config.json.bak-20260709-023945`. Alias `bailian→alibaba-token-plan-cn` (auth/aliases.py, unchanged) keeps any old session/agent.json refs resolving.

### Live verification (backend restarted, pid 12940)
- `/api/providers/list`: single `alibaba-token-plan-cn` row — label "Alibaba Token Plan (China)", `enabled=true`, `configured=true`, base `token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`. No `bailian` orphan row.
- `/api/models/enabled`: 9 rows (unchanged).
- Tests: `tests/providers/` + `tests/unit/test_browse_live_and_refresh.py` → 88 passed, 16 skipped, 5 xfailed.

## Merge chatgpt-subscription → openai-codex (2026-07-09, commit 1ee2c232)

**Config diff / migration.** Both keys carried `enabled_models: ["gpt-5.5"]`; `openai-codex.custom_models` already contains the full gpt-5.5 spec row. No enabled id or setting present under the alias was missing under the canonical key → **pure delete** (verified: `chatgpt-subscription enabled_models = openai-codex enabled_models = {gpt-5.5}`, diff empty). Removed the `chatgpt-subscription` key from `~/.openprogram/config.json`.
- Backup: `~/.openprogram/config.json.bak-20260709-024715`

**Canonicalization seam chosen: `enabled_models._load()` (single lowest seam).** Traced every surface — `get_providers()`, `get_models()`, `list_providers` tier 1, and the `list_enabled_models` picker all derive from `ENABLED_MODELS`, and the `chatgpt_subscription/` provider dir is empty (no static registry entry of its own). So the ONLY reason the alias appeared anywhere was the config key producing a `chatgpt-subscription/gpt-5.5` registry row. Fixing `_load()` fixes every surface at once. `_load()` now skips an alias config key (`resolve(pid) != pid`) **only when the canonical id is also a config key** — dedup when both present; a lone alias key still loads and routes via its resolved endpoints, so old configs keep working (preserves existing test `test_alias_provider_endpoints_resolve_from_canonical`). `get_model`'s alias fallback is untouched, so old `chatgpt-subscription/...` session refs still resolve. Added a defensive alias skip in `list_providers` (both registry and community tiers) so the sidebar guarantee is local regardless of registry behavior.

**Tests.** Added: registry `test_alias_key_folds_into_canonical_when_both_present`, `test_get_model_still_resolves_alias_after_fold`; listing `test_list_providers_hides_alias_provider_row`. Suites green — `tests/providers/` + `tests/unit/test_browse_live_and_refresh.py` = 29 passed; wire = 4 passed; full `tests/unit/` = 1201 passed (only the 3 known pre-existing failures: 2 `test_context_route`, 1 `graph_layout`).

**Live verification (backend restarted, pid 23496).**
- `/api/providers/list`: `openai-codex` present (label "OpenAI Codex", enabled=true); **no `chatgpt-subscription` row** (`has chatgpt-subscription: False`).
- `/api/models/enabled`: **8 rows** (was 9 — duplicate gpt-5.5 gone); single gpt-5.5 under `openai-codex`; providers = claude-code, deepseek, minimax-cn-coding-plan, openai-codex, openrouter (all previously present, intact).
- Real request: `create_runtime("openai-codex","gpt-5.5").exec("Say OK", max_iterations=1)` → reply **`"OK"`** (ALL_PROXY unset for httpx). Merge did not break the working chain.

---

## Fix: enabling a model doesn't appear in chat picker in real time (2026-07-09)

**Symptom (user):** Enabling a model under `alibaba-token-plan-cn` in settings does not show up in the chat model picker until process restart.

**Repro (old backend, port 18109):**
- `POST /api/providers/alibaba-token-plan-cn/models/qwen3.6-flash/toggle {"enabled":true}` → returns `{"enabled":true}`, config row written.
- Immediately `GET /api/models/enabled` → count stays 8, `qwen3.6-flash` absent. Registry stale.
- Also observed: config already had `glm-5.2` + `kimi-k2.7-code` enabled for alibaba, but the running picker showed only 8 (missing both) — they'd been toggled on live earlier and never reloaded. A fresh Python import of `ENABLED_MODELS` loaded all 10, proving config-write was correct and only the in-memory registry was stale.

**Root cause:** `toggle_model` (and every sibling mutation — `toggle_provider`, `remove_custom_model`, `add_custom_models`, `replace_fetched_models`, `set_provider_config`) writes config via `_write_providers_cfg` but never calls `enabled_models.reload()`. Only the Fetch/Refresh path and `login_enable` reloaded. So `list_enabled_models()` → `ENABLED_MODELS` stayed frozen at import-time until restart. NOT alias/key related: config wrote to the canonical `alibaba-token-plan-cn` key, no resurrected `bailian`, `_load()` keyed rows correctly and endpoints resolved. Bug affected every provider, not just Alibaba.

**Fix seam:** `openprogram/webui/_model_listing/storage.py::_write_providers_cfg` — the single choke point every config mutation routes through. Added `enabled_models.reload()` after the save. `reload()` reads config from disk directly (`read_providers_config`, not `_read_providers_cfg`), so it never re-enters the migration guard. The redundant `reload()` in `fetchers/__init__.py` is now harmless (idempotent), left in place.

**Tests:** `tests/unit/test_model_spec_copy.py::test_toggle_enable_refreshes_registry_without_restart` — drives real `toggle_model` through real `_write_providers_cfg` + real `ENABLED_MODELS`; asserts `get_model("acme-plan","acme-1")` and `list_enabled_models()` reflect enable, then disable, with NO manual reload. Confirmed it FAILS with the fix reverted. Suites green: `tests/providers/` (103 passed), `test_browse_live_and_refresh.py`, `test_provider_wire_invariants.py` (4), full `tests/unit/` = 1120 passed; only the 3 known pre-existing failures (2 `test_context_route`, 1 `test_graph_layout` from concurrent dag edits).

**Live verification (fixed backend, port 18109):**
- After restart, picker = 10 (the true pre-test enabled set, including the 2 alibaba rows the old process couldn't see).
- Enable `qwen3.6-flash` via API → picker 10→11, `qwen3.6-flash` present immediately.
- Disable → picker 11→10, gone immediately. No restart.

**State left clean:** config alibaba rows = `[glm-5.2, kimi-k2.7-code]` (pre-test), no `bailian` key, picker = 10. Backup: `~/.openprogram/config.json.bak-20260709-025741`.

---

## Fix: alibaba-token-plan-cn multi-key management + alias-aware pool keying (2026-07-09)

### Architecture map
- **Storage:** `~/.openprogram/auth/<provider_id>/<profile_id>.json` (one credential POOL per profile), 0600, atomic write+flock, mtime-watch reload. Sidecars: `_active.json` (active pin), `_rotation.json`, `_order.json`, `_disabled.json` — all keyed by provider_id. Code: `openprogram/auth/store.py`.
- **Model:** account = profile; a pool holds one+ credentials. Design: `docs/design/providers/auth/unified-account-management.md` (P-E: account=profile the one model spanning every provider).
- **Routes (settings "API keys" section):** `openprogram/webui/routes/accounts.py` — `/api/providers/{provider}/accounts` (list summary), `/accounts/use|remove|rename|add|reorder|rotation|enabled`, `/accounts/keys` (add api-key), `/accounts/{name}/reveal|update|validate|retry`, `/accounts/validate-all`. claude-code's separate Meridian routes were already removed; it is served by this same surface via `_pool_id("claude-code") → "anthropic"`. Single `<ProviderAccounts>` component drives all providers.

### Root cause (data + code)
- **Data:** user's key lived in `~/.openprogram/auth/bailian/default.json` (`provider_id: "bailian"`), the pre-rename id. No `alibaba-token-plan-cn/` dir existed.
- **Code:** the store was NOT alias-aware. `_pool_path` had a reverse-alias fallback so `find_pool("alibaba-token-plan-cn","default")` COULD read the `bailian/` file — but `list_pools()` keyed pools by the literal on-disk dir name AND `CredentialPool.from_dict` carried the literal `provider_id="bailian"`. `_generic_summary` filters `list_pools()` by `p.provider_id == "alibaba-token-plan-cn"`, so the `bailian` pool was filtered OUT → accounts list came back empty. Split-brain waiting to happen: a key added under either name could land in two different dirs.

### Fixes
- **Store canonicalization (`auth/store.py`):** added `AuthStore._canon(provider_id)` = `aliases.resolve(...)`. Applied at every public entry: `get_pool`, `add_credential`, `put_pool` (also rewrites `pool.provider_id`), `remove_credential`, `delete_pool`. `list_pools` now maps each on-disk dir name → canonical and relabels the loaded pool's `provider_id`, so a legacy `bailian/` dir surfaces as `alibaba-token-plan-cn`. Alias-aware on BOTH read and write ⇒ one pool, never split-brain.
- **Route (`routes/accounts.py`):** `_pool_id` now resolves through the full alias table (not just claude-code→anthropic), and the handlers that hit sidecars / filter `list_pools` (add-key, validate-all, rotation, reorder, enabled) use `_pool_id(provider)` so pool + sidecars key off ONE canonical id.
- **Data migration:** moved `bailian/default.json` → `alibaba-token-plan-cn/default.json` with internal `provider_id` rewritten to canonical; removed old dir. Timestamped backup retained.
- **Tests:** `tests/unit/test_auth_store.py` — 4 alias-aware pool tests (write-under-alias→read-under-canonical, alias+canonical share one pool, legacy dir surfaces canonical in list_pools, alias-aware delete). Fixture-driven (`AuthStore(root=tmp_path)`), no real auth files. Updated `test_auth_cli.py::test_login_resolves_alias` (its old assertion `find_pool("claude") is None` encoded the pre-alias-aware store; now asserts on-disk canonical location instead — the real guarantee, now stronger).

### Uniformity inventory (per provider type)
- **UNIFIED (already):** All providers — api-key, oauth (codex/anthropic/copilot), subscription (claude-code via anthropic pool), import-cli (gemini-subscription/qwen) — share ONE `/api/providers/{id}/accounts/*` route surface and ONE `<ProviderAccounts>` component. list / use / rename / remove / validate / rotation are identical across every backend. claude-code's Meridian literal routes were already removed and folded in.
- **DIVERGES (by necessity, per design P-E — REPORTED not built):**
  - *Add flow branches on `add_mode`*: api-key pastes a key (`/accounts/keys`); oauth/device drive `/login/*`; import-cli reads an external CLI file. This is intrinsic (you can't paste a key for an OAuth provider) and already surfaced uniformly via `add_mode` + `login_methods` in the summary. No fix needed.
  - *Backend model split (api-key = many creds in one pool; login = one cred per profile)*: design doc P-E says the true unification is "account = profile, one credential each" and to retire the in-pool multi-credential api-key surface. That's a real refactor (touches `auth/usage.acquire_pooled`, rotation across profiles, migration of existing multi-cred pools) — **REPORTED, not built**; out of scope for this fix.
  - *gemini-subscription / anthropic-subscription cannot self-refresh* (no distributable OAuth client) — a hard external constraint, documented in `unified-auth-storage.md`, not a code divergence.

### Live verification (port 18109, restarted)
- `GET /accounts` for alibaba-token-plan-cn: user's key `sk-sp-…BUCh` (api_key, valid) NOW shows (was empty).
- Add dummy `dummy2` → 2 accounts, landed in `alibaba-token-plan-cn/` (no `bailian/` recreated); remove → back to 1.
- `POST /accounts/default/validate`: HTTP 200, status valid, via GET /models, 185ms.
- `/api/models/enabled`: 10 enabled, 2 alibaba entries — unchanged.
- Test artifacts (dummy2 files + lock) removed.

### Backups
- `~/.openprogram/auth/bailian.backup-20260709-162858/` (original bailian pool dir, pre-migration).

### Suites
- `tests/unit/test_auth_store.py`, `test_auth_methods.py`, `test_auth_cli.py`, `tests/providers/` — all green.
- Full `tests/unit/`: only the 3 known pre-existing failures (2 `test_context_route`, `test_graph_layout`) remain.

---

## 2026-07-09 — Feature: "Add custom provider" (tier-3 config-only providers)

### Summary
Added the settings-page surface for user-created custom LLM providers (config-only, OpenAI-compatible endpoints not shipped as a provider dir or known to models.dev). The runtime already builds Models from `providers.<pid>.models` spec rows, so a config-only provider worked at runtime; this fills the missing UI + routes.

### Backend
- `openprogram/webui/_model_listing/storage.py`
  - `create_custom_provider(id, label, base_url)` — validates kebab-case slug, refuses collision with an existing tier-1/tier-2 provider id or a known alias (`aliases.resolve`), writes `providers.<id> = {enabled:true, source:"custom", label, base_url, models:[]}` through `_write_providers_cfg` (which reloads the runtime registry).
  - `delete_custom_provider(id)` — deletes only keys whose config has `source == "custom"` (refuses otherwise); leaves the AuthStore credential pool on disk.
  - `add_manual_model(pid, id, name)` — writes an ENABLED minimal spec row (`source:"manual"`, api/base_url derived from provider config) for a provider whose `/models` is unavailable; usable in chat after reload.
  - `_is_custom_provider(pid)` helper; `_SLUG_RE` kebab-case validator.
- `openprogram/webui/_model_listing/listing.py` — tier-3 block in `list_providers`: config keys with `source=="custom"` not in tier 1/2 appear as sidebar rows flagged `custom:true`, sorted at the end (appended after the alpha sort). `label` from config `label` → title-cased id fallback. `api_key_env` synthesised via `_synth_env_var` so the frontend renders the keys section. Also stamps the provider config `base_url` onto every browse row so a spec row copied via `spec_row_for` (the toggle path) carries the endpoint the runtime needs — fixes toggle for a dir-less/models.dev-less provider.
- `openprogram/webui/_model_listing/providers.py` — added `_synth_env_var(pid)` (`frontier-intelligence` → `FRONTIER_INTELLIGENCE_API_KEY`, DISPLAY-ONLY). Deliberately kept OUT of `_env_var_for`/`env_vars_for` so it can't flip `_is_configured` for community providers (that would be a regression — verified against `test_api_key_resolution::test_env_vars_for`).
- `openprogram/webui/_model_listing/fetchers/__init__.py` — route custom pids to the generic `_fetch_openai_compat` (`GET {base_url}/models`, Bearer from AuthStore). On failure returns `{"error":...}` which `_browse_models` degrades to an empty list — never caches a failure as success.
- `openprogram/webui/routes/providers.py` — `POST /api/providers/custom`, `DELETE /api/providers/custom/{name}`, `POST /api/providers/{name}/models` (manual add). Non-ok results return HTTP 400.
- `openprogram/webui/_model_listing/__init__.py` — re-export the three new mutators.

Credential resolution needed NO change: a custom pid resolves its key through the shared `resolve_provider_key` → AuthStore choke point (`stream.py`), alias-canonicalised by `AuthStore._canon` (commit 25113a5c). Env vars are display-only. The `/accounts/*` surface is already generic per-provider — verified, not rebuilt. Icon fallback (letter avatar in `provider-icon.tsx`) already handles unmapped ids gracefully — no mapping added.

### Frontend (`web/components/settings/providers/`)
- `types.ts` — `Provider.custom?: boolean`.
- `add-custom-provider.tsx` (new) — inline "Add custom provider" form (id, display name, base_url) under the sidebar search; POSTs `/api/providers/custom`, selects the new provider on success.
- `index.tsx` — mounts `AddCustomProvider`; wires `onDeleted` on `Detail` to invalidate + navigate away.
- `provider-item.tsx` — "Custom" badge on tier-3 rows.
- `detail.tsx` — destructive delete button (with `window.confirm`) for custom providers; "Add model by id" input (Enter or button) that POSTs the manual-add route.

### Tests
- New `tests/unit/test_custom_providers.py` (14 tests, fixture-driven, no network): create validation (bad slug, empty base_url, alias collision, existing id, marker write, label fallback), tier-3 listing + sorts-last, delete refuses non-custom / removes custom, manual model add → working ENABLED_MODELS entry after reload, toggle for a dir-less provider builds a full spec (base_url from config), custom browse fetch failure → empty not error. All 14 pass.
- `tests/unit/test_custom_providers.py` `mem_cfg` fixture also patches the `toggle` module's import-bound `_read/_write_providers_cfg` (they're bound at import via `from .storage import ...`).

### Test summary
- New suite: 14 passed.
- Related suites (`test_browse_live_and_refresh`, `test_provider_env_and_aliases`, `test_model_fetch_routing`, `providers/test_registry_from_config`): 49 passed.
- `test_api_key_resolution`: passes (verified the env-var invariant not broken).
- Full `tests/unit -q`: 1138 passed, 4 skipped, only the 3 documented pre-existing failures (`test_context_route` x2, `test_graph_layout` — all in the concurrent session's graph area).
- Frontend `tsc --noEmit`: no errors in touched files (pre-existing errors only in `components/functions/use-folder-meta.ts` and `lib/use-ws.ts`).

### Live verification (port 18109, server restarted with `env -u ALL_PROXY -u all_proxy`, curl `--noproxy '*'`)
- Create `frontier-intelligence` (base_url `https://api.frontier-intelligence.tech/v1`) → `ok:true`; appears in `/api/providers/list` with `custom:true`, synthesised `api_key_env`, correct base_url.
- Browse `/api/providers/frontier-intelligence/models` → HTTP 200, empty list (graceful, no 500 — no key / unreachable endpoint).
- Manual add `test-model` → `ok:true`; appears in `/api/models/enabled` under `frontier-intelligence`.
- Delete refuses `openai` (HTTP 400, "not a custom provider"); deletes `frontier-intelligence` → gone from `/list` and `/models/enabled`.
- Validation over the API: bad slug / alias `codex` / existing `openai` all → HTTP 400 with clear errors.
- Config integrity: only diff vs the session-start backup is the removal of a STALE `frontier-intelligence` key that was already present in the backup — leftover test-fixture residue (`browsed-1`/"Browsed One", no `source` key) from a prior session, cleaned up. No genuine user provider added/removed/changed. Backup at `~/.openprogram/config.json.bak-20260709-171035`.
- Server left RUNNING.

---

## 2026-07-09 — Review findings fix (commit 5d7e486a custom tier-3 providers)

Fixed four review findings on branch `refactor/enabled-models`. Diffs kept minimal; the shared root-cause fix lives in the storage helper both routes route through.

### Important 1 — unguarded manual-add route
`add_manual_model` (`storage.py`) did `cfg.setdefault(provider_id, {})` unconditionally, so `add_manual_model("totally-not-a-provider", "ghost-model")` created an ENABLED_MODELS entry with an empty `base_url` that can't dispatch. Added a guard: reject unless the id is a known tier-1/tier-2 provider (static registry ∪ models.dev) OR an existing config key with `source == "custom"`. Error style mirrors the `unknown provider {id!r}` convention of the sibling search-default route (`providers.py:140`). Factored the create route's inline known-provider computation into `_known_provider_ids()` / `_is_known_provider()` so both routes share one definition.

### Minor 1 — failed browse cached empty for the TTL
`_browse_models` (`listing.py`) cached the `[]` from a failed custom fetch, so after the user pasted a key the list stayed empty until TTL expiry. Now tracks `fetch_failed` (fetch returned an error / raised, vs. genuinely zero models) and skips the cache write when the fetch failed AND the merged result is empty — same fail-not-cached-as-success pattern as `sources/models_dev.py`.

### Minor 2 — keyless custom provider showed `configured: true`
`_is_configured` (`providers.py`) fell through to `env is None → True` for custom pids (no env-var mapping). Added a custom-provider branch before the community fall-through: configured only with a real credential — an AuthStore pool entry (already checked at the top) or the synthesised env var being set (`os.environ.get(_synth_env_var(pid))`).

### Minor 3 — duplicate helper removed
`_title_case` (storage.py) duplicated `_prettify` (providers.py). Removed `_title_case`; `create_custom_provider` now imports and uses `_prettify` (storage already lazily imports from providers, no cycle).

### Test
Added `test_manual_add_unknown_provider_rejected` to `tests/unit/test_custom_providers.py`: manual-add to an unknown id returns `ok:false` / "unknown provider" and writes nothing.

### Files touched
- `openprogram/webui/_model_listing/storage.py`
- `openprogram/webui/_model_listing/listing.py`
- `openprogram/webui/_model_listing/providers.py`
- `tests/unit/test_custom_providers.py`

### Test summary
`python -m pytest tests/unit/test_custom_providers.py tests/unit -q`: 15 passed in the custom-providers suite (was 13); full suite **1139 passed, 4 skipped, 3 failed** — only the 3 documented pre-existing failures (`test_context_route` x2, `test_graph_layout`), no new failures. No `.ts/.tsx` touched, so no `tsc` run needed.

### Live verification (port 18109, backend restarted with `env -u ALL_PROXY -u all_proxy`, curl `--noproxy '*'`)
- Config backed up first to `~/.openprogram/config.json.bak-20260709-172050`.
- Bogus manual-add `POST /api/providers/totally-not-a-provider/models` → HTTP 400 `{"ok":false,"error":"unknown provider 'totally-not-a-provider'"}`; config unchanged (id absent).
- Create + manual-add + delete cycle on `review-test-provider`: create → 200 (source=custom, base_url set); manual-add `rt-model-1` → 200 (model row written); delete → 200; provider gone from config afterward.
- No test residue left in config (both test ids absent). Server left RUNNING.

---

## 2026-07-09 — Welcome-screen example gating + install.sh `--programs` flag

Branch `refactor/enabled-models`. Two follow-up changes.

### Change 1 — welcome screen hides examples for uninstalled functions
`web/components/chat/welcome-screen.tsx`: the 4 hardcoded `EXAMPLES`
(gui_agent, research_agent, wiki_agent, extract_pdf_figures) now render only
when their `name` exists in `availableFunctions`. Implemented as a `useMemo`
filter over the reactive `availableFunctions` (sourced from the `useFunctions`
zustand store via `useWindowGlobals` — already re-renders when the
`functions_list` WS envelope calls `setFunctions`). The store starts at `[]`,
so before the list streams in nothing renders (no flash); the `.examples` row
is `position: absolute` (module CSS), so an empty/absent row causes no layout
shift — no placeholder needed. If all four are missing the row is empty.
The now-unreachable `pickExample` composer-fallback branch (and its
`setComposerInput`/`focusComposer` deps) was removed; the `openFnForm` path is
kept.

### Change 2 — `--programs` install flag
`scripts/install.sh`: added `--programs <gui|research|wiki|all>` (repeatable
and comma-separated), parsed into `PROGRAMS`, fanned out after the main install
by `install_programs()` running `openprogram programs install <each>`. Header
comment, `Usage:` block, and final "Programs:" hint updated.
`scripts/install.ps1`: mirrored as `[string[]]$Programs` + `Install-Programs`
(same comment/usage/hint parity). `README.md` (~line 90) and
`docs/README_CN.md` (~line 58) each got one sentence documenting the flag.

### Verification
- `web/`: `npx tsc --noEmit` — no errors reference `welcome-screen.tsx`. The
  reported errors (use-folder-meta.ts, use-ws.ts, execution-dag/*,
  session-dag-panel, composer/*-bubble refs) are all pre-existing / owned by
  the concurrent DAG session; not touched here.
- `scripts/install.sh`: `bash -n` clean; `--help` prints the new
  `--programs all` usage line; parse+fan-out logic unit-tested off-repo
  (comma-separated and repeated forms both expand to one
  `openprogram programs install <name>` per program). No full install run.
- Backend restarted on :18109 with `env -u ALL_PROXY -u all_proxy`; curl
  `--noproxy '*' /api/functions` returns 7 functions including all 4 example
  names, keyed by the same `name` field the filter uses → locally all 4 buttons
  still show. Server left running.
- No component test framework exists in `web/` (no `.test`/`.spec` files), so no
  test added per the no-new-infrastructure constraint.

## 2026-07-09 — Simplify "Add custom provider" form to Name + Base URL

User-approved change: the add-custom-provider form asked for three fields
(id, display name, base URL). Now it asks for two — Name and Base URL — with
the id derived server-side and all normalization automatic.

### Backend — `openprogram/webui/_model_listing/storage.py`
- Added `_slugify(text)`: lowercase → spaces/underscores to hyphens → drop
  chars outside `[a-z0-9-]` → collapse hyphen runs → trim leading/trailing
  hyphens. Empty result (CJK/emoji-only name) is the signal to 400.
- Added `_normalize_label(text)`: trim, collapse internal space runs,
  title-case words that are all-lowercase; mixed-case words the user typed
  ("OpenAI", "vLLM") are left untouched.
- Added `_id_taken(pid, cfg)`: alias / known tier-1/2 id / ANY existing config
  key. Used only by the derived-id auto-suffix loop — an existing custom key
  counts as taken so we suffix past it rather than clobber.
- Reworked `create_custom_provider`: `provider_id` is now optional. Blank id →
  slugify `label`, then `-2`/`-3`/… suffix until free (auto-resolve, no error).
  A non-empty explicit id keeps the strict path unchanged (bad slug / reserved
  alias / existing non-custom key → 400; overwrite-custom semantics preserved),
  so the existing `test_custom_providers.py` explicit-id cases still hold. Label
  is normalized before store; empty-after-slugify name → 400 "must contain
  letters or digits". Still flows through `_write_providers_cfg`.

### Route — `openprogram/webui/routes/providers.py`
- Docstring for `POST /api/providers/custom` updated: body is `{label,
  base_url, id?}`, id optional with derived-id + auto-suffix, explicit id strict.

### Frontend — `web/components/settings/providers/add-custom-provider.tsx`
- Removed the id input and its state. Two inputs: Name (placeholder "Frontier
  Intelligence") and Base URL (placeholder unchanged).
- Added a client `slugify()` mirroring the server, shown as a small live "id:"
  hint under the Name input (preview only). Post-create selection/refresh uses
  the id from the response (`d.id`), which is authoritative.
- Add button enabled when both Name (trimmed non-empty) and Base URL are
  present; request body drops `id`.

### Tests — `tests/unit/test_custom_providers.py`
Added derived-id path cases: slugify spaces/case, strip illegal chars, CJK-only
and emoji-only names → 400, collision auto-suffix (`-2`/`-3`), explicit-id
collision still 400, label normalization (lowercase title-cased, "OpenAI
Compatible" preserved). File: 23 passed.

### Verification
- `python -m pytest tests/unit/test_custom_providers.py tests/unit -q`:
  1147 passed, 3 failed — the 3 documented pre-existing failures only
  (test_context_route x2, test_graph_layout::test_manual_function_hangs_off_root).
  No new failures.
- `web/`: `npx tsc --noEmit` — no error references `add-custom-provider.tsx`.
  The reported errors (use-folder-meta.ts, use-ws.ts) are pre-existing.
- Backend restarted on :18109 with `env -u ALL_PROXY -u all_proxy`; curl
  `--noproxy '*'`:
  - POST `{label:"Frontier Intelligence", base_url:".../v1"}` →
    `id=frontier-intelligence`, `label="Frontier Intelligence"`.
  - POST same again → `id=frontier-intelligence-2`.
  - DELETE both → removed; config byte-identical to the pre-test timestamped
    backup. Backup removed. Server left running.

## 2026-07-09 — Feature: openclaw-style one-command install (curl | bash bootstrap + interactive program menu)

Goal: install OpenProgram with a single `curl … | bash` instead of clone-then-run,
with interactive choices made inside the installer (openclaw pattern).

### `scripts/install.sh`
- **Self-bootstrap** (step 0): checkout is now detected by `pyproject.toml`
  sitting next to the script (`is_openprogram_checkout`), not by `BASH_SOURCE`
  path — when piped via curl, `BASH_SOURCE` is `bash`/empty. When not in a
  checkout: require git (else `die` with install hint), resolve target
  (default `~/OpenProgram`, `--target DIR` override, interactive prompt on
  `/dev/tty` offering the default), then clone (`git clone --depth 1`) and
  `exec` the cloned `scripts/install.sh` forwarding all original args plus an
  internal `--bootstrapped` marker so the child skips re-bootstrapping. If the
  target already exists: reuse + `git pull --ff-only` when it IS an OpenProgram
  checkout, else `die`. Added internal `--bootstrap-only` (clone + exec child
  `--help`) purely for testability.
- **Interactive program menu** (`prompt_programs_menu` + `parse_program_choice`):
  after the main install, when no `--programs` was given and `/dev/tty` is
  usable (`-r`/`-w` + `: </dev/tty` probe, mirroring openclaw line 111-114),
  prints a numbered menu (GUI / Research / Wiki with one-line descriptions and
  sizes lifted from `KNOWN_PROGRAMS`) reading from `/dev/tty`. Empty → none,
  `all` → every harness, `1,3` → gui+wiki, invalid → re-prompt. Selected keys
  append to `PROGRAMS` and flow through the existing `install_programs`.
- **Non-interactive fallback**: no usable `/dev/tty` (CI, true pipe) or
  `--programs` given → old behavior; `--yes`/`-y` skips all prompts using
  defaults. Declined `/dev/tty` reads use `|| true` so `set -euo pipefail`
  never aborts on them.
- New flags: `--target`, `--yes`/`-y` (internal: `--bootstrapped`,
  `--bootstrap-only`). Header + Usage block updated with the curl one-liner.

### `scripts/install.ps1`
- Parallel `-Target` / `-Yes` / internal `-Bootstrapped` params.
- Bootstrap: when no script file (`iwr | iex`) or not a checkout
  (`Test-OpenProgramCheckout`), require git, clone to `$HOME\OpenProgram`
  (or `-Target`, or `Read-Host` prompt), reuse+`git pull --ff-only` if present,
  else `git clone --depth 1`, then re-invoke the child with `-Bootstrapped` and
  forwarded switches.
- Interactive menu via `Read-Host` (`Prompt-Programs` + `Convert-ProgramChoice`,
  same semantics as bash), gated on `[Environment]::UserInteractive` and no
  `-Programs`/`-Yes`.
- Also removed a pre-existing orphaned `else { … }` at the tail (dangling from
  an earlier refactor — it was a hard PS parse error).

### Docs
- `README.md`, `docs/README.en.md`, `docs/README.md`, `docs/README_CN.md`:
  primary macOS/Linux install is now
  `bash -c "$(curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh)"`,
  with the clone-then-run form kept as the "from a checkout" alternative and a
  note that plain `curl … | bash` also works (reads `/dev/tty`).

### Verification (no full install)
- `bash -n scripts/install.sh` OK; `--help` prints the new usage.
- Bootstrap: copied the lone script to scratchpad (no checkout), ran
  `--bootstrap-only --target <scratchpad>/clone-target` → it cloned from
  `github.com/Fzkuji/OpenProgram.git` and exec'd the cloned
  `scripts/install.sh --bootstrapped --help`. Re-run against a checkout copy
  carrying these edits → "reusing existing checkout" + `git pull --ff-only`
  ("Already up to date.") + child printed the new Usage. "exists but not a
  checkout" → aborts with a clear message. In-place detection on the real repo
  → installs in place, no clone.
- Menu parse cases (extracted `parse_program_choice`): ""→none, none→none,
  all→gui research wiki, 1,3→gui wiki, 1,1,2→gui research (dedup), 3,2,1→wiki
  research gui (order preserved), gui,wiki→gui wiki, 9→err, 1,x→err,
  "  All  "→all (trim+case). All as expected.
- `set -e` survival: no-tty guard takes the `|| return 0` path and the script
  survives (verified in a sandboxed shell where `: </dev/tty` fails).
- PowerShell: `pwsh` not installed on this machine — syntax check skipped.
  Brace/bracket balance checked by hand; the two `else` are proper `} else {`
  chains; the orphaned tail `else` was removed.

---

## 2026-07-09 — Agent-operable installer (prompt timeout, non-interactive env signals, agent docs)

Branch `refactor/enabled-models`. Goal: AI agents must drive the install reliably — no hang, standard non-interactive signals, agent-facing docs.

### scripts/install.sh
- Added `PROMPT_TIMEOUT_SECONDS="${OPENPROGRAM_PROMPT_TIMEOUT:-60}"` (named var → test hook).
- New `tty_prompt "<prompt>"` helper: `read -t "$PROMPT_TIMEOUT_SECONDS"`; on timeout/EOF the failing `read` (returns >128) is caught by an `if !`, resets reply to empty, prints `(no input in Ns — using default)`, and echoes the (empty) reply. Keeps `set -euo pipefail` safe.
- New `is_noninteractive()` shared helper — true when `ASSUME_YES=1` OR env `CI` non-empty OR `DEBIAN_FRONTEND=noninteractive` OR `OPENPROGRAM_INSTALL_YES` non-empty.
- Both prompt sites now route through `is_noninteractive` + `tty_prompt`: the clone-dir prompt and the program-menu prompt. No duplicated condition, no bare `read < /dev/tty` left.
- `--help` usage block gained an "AI-agent / non-interactive" paragraph documenting `--yes`, the three env signals, the 60s timeout, `OPENPROGRAM_PROMPT_TIMEOUT`, and the one-liner.

### scripts/install.ps1
- Added `Test-NonInteractive` (mirrors `is_noninteractive`: `-Yes` / `CI` / `DEBIAN_FRONTEND=noninteractive` / `OPENPROGRAM_INSTALL_YES`). Both prompt sites use it.
- `Read-Host` has no timeout → per brief, the ps1 prompts do NOT self-default on expiry; documented in a code comment and the usage block (agent must pass `-Yes` or an env signal on Windows).

### Docs
- `README.md`, `docs/README.en.md`: added a "Non-interactive / AI-agent install" block (exact `curl … | bash -s -- --yes --programs all`, flag/`--help` note, 60s-no-hang note, link to install.md).
- `docs/README.md`, `docs/README_CN.md`: same, translated.
- `docs/install.md`: replaced the minimal CLI-args table with a full flag matrix (flag POSIX/Win, what it controls, default) and added a "非交互 / AI agent 安装" section — env-var signals table, the 60s timeout + `OPENPROGRAM_PROMPT_TIMEOUT`, the one-liner, and the Windows-no-timeout caveat.
- No root `AGENTS.md`/`copilot-instructions.md` exists → none created (per brief).

### Verification (no full install)
- `bash -n scripts/install.sh` OK; `--help` prints the new AI-agent paragraph.
- Timeout under a real pty (Python `pty.fork`, no input, `OPENPROGRAM_PROMPT_TIMEOUT=3`): the program-menu prompt blocked the full 3s, printed `(no input in 3s — using default)`, proceeded with the default (`PROGRAMS=''`), exit 0 — `set -e` survived.
- Env signals under a live pty: `CI=1`, `OPENPROGRAM_INSTALL_YES=1`, and `DEBIAN_FRONTEND=noninteractive` each skipped the prompt entirely (prompted=False), defaults used, exit 0.
- PowerShell: `pwsh` unavailable — hand-checked. `{}` and `[]` balance exactly; the `()` count is unchanged in balance (my edits add +8/+8), and the pre-existing `()` skew is from parens inside strings/comments and predates these edits (verified against `HEAD`).
- Scratchpad test artifacts removed.

---

## 2026-07-09 — README refresh: simplified AI-agent install note + custom-provider docs + default-install fix

Branch `refactor/enabled-models`. Docs-only. Two tasks.

### Task 1 — AI-agent install note simplified (all 4 READMEs + install.md)
Old note led with the verbose `bash -s -- --yes --programs all` and re-explained the env signals. Rewrote to lead with the truth: the plain `curl … | bash` one-liner already works unattended (no terminal → defaults; with a terminal every prompt times out to its default after 60s, so nothing hangs). `-y` is now shown as the short form (optional, forces defaults immediately); `--programs all` (or `gui`/`research`/`wiki`) to also install agent programs. Kept it 3-4 lines.
- `README.md`, `docs/README.en.md` (English), `docs/README.md`, `docs/README_CN.md` (Chinese): the "Non-interactive / AI-agent install" block → "AI-agent install".
- `docs/install.md` "非交互 / AI agent 安装" heading paragraph reframed to the same minimal-truth framing (env-var flag matrix and Windows-no-timeout caveat kept); example one-liner switched to `-y`; removed the duplicated 60s-timeout paragraph that now lives in the reframed intro.

### Task 2 — README feature refresh
1. **Custom providers** (shipped today: commits `03071c2d`, `5d7e486a`, `25113a5c`). Added a few sentences where providers/settings are described:
   - `docs/README.md`, `docs/README_CN.md`: new "自定义 provider" paragraph after the multi-account paragraph — 设置页「添加自定义 Provider」, Name + Base URL (id auto-generated), models browsed from the provider's `/models` or added by id, unified credential-pool multi-key management.
   - `README.md`, `docs/README.en.md`: new "Providers & models" note after the Power-user commands block (no prior provider subsection existed) — Settings → Providers, multi-account/multi-key credential pool, **Add custom provider** (Name + Base URL, id auto-generated, any OpenAI-compatible endpoint), browse `/models` or add by id.
   Verified against `web/components/settings/providers/add-custom-provider.tsx` (exact labels), `openprogram/webui/_model_listing/{storage,listing}.py`, `openprogram/webui/routes/providers.py`, `openprogram/auth/{store,aliases}.py`.
2. **Welcome-screen example buttons gated to installed functions** (`web/components/chat/welcome-screen.tsx`, commit `5c0994f4`) — no README describes welcome-screen example buttons, so per the brief's constraint nothing was added.
3. **Default install does NOT include Research/Wiki** — programs are opt-in via menu or `--programs` (verified in `scripts/install.sh`: header comment, `prompt_programs_menu` default none, `install_programs` early-return when `$PROGRAMS` empty). Fixed the contradicting claim:
   - `docs/README_CN.md:67` and the identical `docs/README.md:67` ("默认安装包含…Research / Wiki 两个 agent 程序") → default installs host lightweight content only; programs opt-in.
   - `docs/install.md`: the intro paragraph, step-6 table row, the dependency-matrix heading, and the GUI-agent note all corrected the same false "default installs programs" claim.

### Verification
- `scripts/install.sh --help` confirms `-y` == `--yes`, `--programs <gui|research|wiki|all>`, the 60s prompt timeout with `OPENPROGRAM_PROMPT_TIMEOUT`.
- `grep -n "providers/custom" openprogram/webui/routes/providers.py` → POST/DELETE routes exist.
- Code-fence balance even in all 5 files; no leftover `--yes --programs all`; no leftover wrong default-install claim.
- Not touched (per brief): `openprogram/webui/graph_builder.py`, `openprogram/webui/graph_layout/`, `web/lib/runtime-bridge/dag/`.

---

## 2026-07-09 — Fix: enabled-model spec rows stored modalities/cost under models.dev flat keys the Model schema doesn't read

### Symptom
Sending an image to `openai-codex/gpt-5.5` failed 6 retries with
`ValueError: Model 'gpt-5.5' does not support input modality: image. Supported: ['text']`
(raised in `openprogram/providers/_shared/validate_modalities.py`, which reads `model.input`).

### Root cause
Every enabled-model spec row in `~/.openprogram/config.json` `providers.<p>.models`
stored modalities under `input_modalities` (models.dev display shape) and costs under
flat `input_cost`/`output_cost`/`cache_read_cost`, with NO schema `input` or nested `cost`.
`Model.model_validate` ignores unknown keys (Pydantic default), so every enabled model
validated as text-only with zero cost. Verified: all providers' rows had `input_modalities`,
zero had `input`; costs were flat too (gpt-5.5: `input_cost: 5.0` present, `cost: None`).

The spec row is BOTH the UI-display row and the runtime Model shape, and they diverged.
The row is produced by `list_models_for_provider` (which passes browse-row `input_modalities`
straight through) → `spec_row_for` → persisted by `toggle_model`.

### Fix (root cause, all paths)
1. **Writer choke point** — new `_normalize_spec_row` in
   `openprogram/webui/_model_listing/storage.py` stamps schema `input` (filtered to the
   `text/image/video/audio` Literal — drops `pdf`) and nested `cost` from the flat keys.
   Called in `spec_row_for` AND `_upsert_spec_row` (the single row-write choke every writer
   routes through: toggle enable, login-enable defaults, `add_manual_model`, migration backfill).
   `login_enable._DEFAULTS` already used `input: [...]` for claude-code and needs nothing;
   its openai-codex defaults had no `input` (correct — text-only until Refresh fills modalities).
2. **v3 config-repair migration** — `_SPEC_MIGRATION_VERSION` bumped 2→3; new
   `_repair_modality_cost_specs` rewrites existing rows (`input_modalities`→`input` filtered,
   flat cost→nested `cost`) and drops the redundant flat display keys. One-shot per machine
   via the persisted `spec_migration_version` marker, idempotent.
3. **Reader tolerance** — `_build_model_from_row` in
   `openprogram/providers/enabled_models.py` maps `input_modalities`→`input` and flat cost→
   nested `cost` when the schema keys are absent, covering pure-CLI configs that never pass
   through the webui migration.

### Other mismatched fields found
- **cost** — same disease, FIXED (flat `*_cost` → nested `cost`; was silently zero).
- `input_limit` — models.dev flat redundant with `context_window` (which IS present and maps
  correctly); the runtime uses `context_window`, so no data loss — left as-is.
- `attachment`, `family`, `knowledge_cutoff`, `release_date`, `vision`, `tools`,
  `structured_output`, `speed_modes`, `cost_tiers`, etc. — UI-display-only metadata the Model
  schema intentionally omits; Pydantic ignores them harmlessly. No fix needed.

Note: the Model schema has NO `output` field (only `input`), so `output_modalities` has no
schema target — it's dropped by the v3 migration as a stale display key, no data lost.

### Tests (tests/unit/test_model_spec_copy.py, +3)
- `test_enable_normalizes_modalities_and_cost_to_schema` — browse row with image+pdf →
  enabled spec row has `input == ["text","image"]` (pdf filtered) and nested cost; reload
  resolves image.
- `test_v3_repair_converts_legacy_modality_cost_row` — v3 migration converts a legacy
  `input_modalities`/flat-cost row and drops the old keys.
- `test_reader_tolerates_legacy_row_without_migration` — legacy row w/o webui migration still
  resolves image via `_build_model_from_row`.

### Results
- `tests/unit/test_model_spec_copy.py`: 13 passed.
- Related suites (custom_providers, enabled_models_community, browse_live_and_refresh): 37 passed.
- Full `tests/unit -q`: 1150 passed, 4 skipped, 3 failed (the 3 documented pre-existing:
  test_context_route x2, test_graph_layout).

### Live verification
- Config backed up: `~/.openprogram/config.json.bak.20260709-202333`.
- After migration: every enabled row now has `input`; zero rows retain `input_modalities`
  (two openrouter free rows have no `input` — genuinely text-only, never had modalities;
  they resolve to `["text"]` default). gpt-5.5: `input == ["text","image"]`, `cost.input == 5.0`.
- `ENABLED_MODELS["openai-codex/gpt-5.5"].input` contains `image`; `cost.input == 5.0`.
- Backend on :18109 (`GET /api/models/enabled`) reports gpt-5.5 `vision: true`.
- All enabled model ids and enabled providers identical to the pre-migration backup.
