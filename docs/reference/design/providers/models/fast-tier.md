# Fast tier — detection, storage, and wires

Status: shipped (2026-07-12, each rule user-adjudicated). This doc records
where the fast feature's code lives, where its data comes from, and the
detection rules. Sibling doc: [`thinking-effort.md`](thinking-effort.md)
(the same "model capability → UI toggle" declarative pattern; the structure
is deliberately aligned).

## 1. What Fast is

The "Fast" toggle in the composer's + menu. When on, the request carries the
vendor's high-speed knob:

| Family | Wire shape | Billing reality |
|---|---|---|
| GPT 5.4 / 5.5 / 5.6 | body `service_tier: "priority"` (OpenAI's priority processing) | On the Codex subscription the endpoint advertises it as a per-model tier ("1.5x speed, increased usage"); which models expose it comes straight from `service_tiers` (§2.1), not a guess |
| Claude Opus 4.6 / 4.7 / 4.8 | body `speed: "fast"` + header `anthropic-beta: fast-mode-2026-02-01` | Also pay-as-you-go; a subscription account without usage credits gets Anthropic's 429 "Usage credits are required for fast mode" (verified 2026-07-12), surfaced **as-is** — an account problem, not lack of support |

No other model family (Gemini / DeepSeek / Qwen / Llama / MiniMax…) has a
fast tier at all.

## 2. Detection: `supports_fast(provider, model)` — three branches

Entry point: `openprogram/webui/_model_listing/listing.py`. Strips the
wire-format `"provider:"` prefix first (the runtime records the current
model as `openai-codex:gpt-5.5`).

1. **openai-codex → reads the persisted `Model.fast`**. Not hand-written:
   this field comes from the official codex models endpoint (§2.1), written
   into config alongside the spec at Fetch time, so detection just does
   `get_model("openai-codex", id).fast`. Tiers with no fast mode (e.g.
   `gpt-5.4-mini`) resolve False exactly — the old id-prefix table said True.
2. **claude-code → hand-written table** `enabled_models.default_fast`: id
   contains `opus-4-6/4-7/4-8` (hyphen or dot) → True. Its subscription
   endpoint isn't verified yet, so it stays hand-written; swap it for
   endpoint-backed storage (like codex) once we confirm one exists.
3. **Everything else → models.dev, automatic**: a mode with `service_tier ==
   "priority"` or `id == "fast"` → True; none, or unknown provider → False.

Ruling: private gateways (e.g. frontier-intelligence) are NOT special-cased —
unknown to the catalogue means no fast button. Use the config override (§3).

### 2.1 Codex's official source

`GET https://chatgpt.com/backend-api/codex/models?client_version=<ver>` — the
same account-level endpoint the official `codex` CLI hits on startup —
authorized with the subscription OAuth bearer + `chatgpt-account-id`. Each
model carries `service_tiers` (an `id:"priority"` entry ⇔ has a fast tier),
`supported_reasoning_levels` (the thinking picker), and the real subscription
`context_window` (372k, not the API platform's 1050k). Requests and dispatch
use the `originator: codex_cli_rs` + `version` identity — the backend
greylists ids (e.g. `gpt-5.6-luna`) by client identity, so a different
originator gets the model in the list but 404s at dispatch.

Why models.dev was dropped: it tracks the public *API platform* catalogue, not
the subscription front-door — leaking un-runnable ids, wrong context windows,
and a fast flag reconstructed from an id-prefix guess that misfired.

## 3. Storage: read official → write config → read the file thereafter

Codex principle: **all info comes live from the vendor, no hand-kept model
list.**

| Layer | Location | Persistence |
|---|---|---|
| Codex official endpoint | `webui/_model_listing/fetchers/codex.py::_fetch_codex_live` | remote; 10-min in-memory browse cache, **no disk cache** |
| config spec row (with `fast`/`thinking_levels`/`context`) | on enable, `fetch_and_normalize`'s normalised row is written to `~/.openprogram/config.json`; the Fetch button (`fetch_models_remote`) heals enabled rows with fresh endpoint data | config file (this is the "write to a file" step) |
| `Model.fast` field | `_build_model_from_row` reads the config row's `fast` (row wins; codex rows always carry it); enters `ENABLED_MODELS` at registry build | memory only (in-process dict, sourced from config) |
| claude-code hand table | `providers/enabled_models.py::default_fast` (only the Opus part is on the detection path now) | source code |
| models.dev catalogue | `webui/_model_listing/sources/models_dev.py` | remote; 1h in-memory cache, no disk cache |

Flow: **official endpoint → normalise → config.json → registry →
supports_fast / dispatch**. Offline / not signed in → the endpoint returns an
error and the saved config rows are kept, not blanked — you can't dispatch
these models without a token anyway, so a token-less browse losing the list
isn't a regression.

## 4. Event flow: adapts to any switch, no page reload

```
connect / session switch / model switch / every turn ack+settle
  → frontend loadAgentSettings()  (lib/runtime-bridge/providers.ts)
  → GET /api/agent_settings       (webui/routes/runtime.py)
      chat.fast = supports_fast(session's provider, model)   ← recomputed
  → zustand agentSettings.chat.fast
  → composer re-renders: shows/hides the Fast menu item and chip
```

Send-side double gate: the composer only attaches
`service_tier: "priority"` when `fastEnabled && fastSupported` — a stale
per-session fast setting never leaks to an unsupported model.

## 5. Wire side (request builders)

| Builder | Behavior |
|---|---|
| `providers/openai_responses` / `openai_completions` | `opts.service_tier` → body `service_tier` (pre-existing) |
| `providers/openai_codex` (ChatGPT subscription) | `opts.service_tier` → body passthrough; dispatch uses the `originator: codex_cli_rs` + `version` identity (the backend greylists ids by client identity — see §2.1) |
| `providers/anthropic` | `opts.service_tier` present **and** `model.fast` → body `extra_body={"speed":"fast"}` + `_BETA_FAST` header (appended via `_build_client(fast=...)`, never clobbering other betas) |
| all other wires | no passthrough; the knob never leaves the process |

## 6. File map

```
openprogram/providers/types.py                     Model.fast field
openprogram/providers/enabled_models.py            default_fast (claude-code Opus only) + config-row backfill
openprogram/providers/openai_codex/{openai_codex,runtime}.py   service_tier passthrough; codex_cli_rs identity + _CODEX_CLIENT_VERSION
openprogram/providers/anthropic/{anthropic,_claude_code_direct_runtime}.py  Claude fast wire + registration backfill
openprogram/webui/_model_listing/fetchers/codex.py official endpoint fetch + normalise (fast/thinking/context source)
openprogram/webui/_model_listing/fetchers/__init__.py  orchestration: passes through fetcher fast/thinking, enrich can't overwrite
openprogram/webui/_model_listing/listing.py        supports_fast entry; list_models_for_provider prefers fetcher thinking
openprogram/webui/routes/runtime.py                /api/agent_settings emits chat.fast
web/lib/session-store/types.ts                     AgentBadgeInfo.fast type
web/components/chat/composer/index.tsx             toggle visibility + send gate
```

Change guide: codex fast/thinking is fully automatic — add/remove a model
needs nothing but a Fetch; change detection logic → touch only
`listing.py::supports_fast`; claude-code add/remove fast → the Opus part of
`default_fast`; another provider wanting fast → works once models.dev knows it.
