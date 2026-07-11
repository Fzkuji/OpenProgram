# Fast tier — detection, storage, and wires

Status: shipped (2026-07-12, each rule user-adjudicated). This doc records
where the fast feature's code lives, where its data comes from, and the
detection rules. Sibling doc: [`thinking-effort.md`](thinking-effort.en.md)
(the same "model capability → UI toggle" declarative pattern; the structure
is deliberately aligned).

## 1. What Fast is

The "Fast" toggle in the composer's + menu. When on, the request carries the
vendor's high-speed knob:

| Family | Wire shape | Billing reality |
|---|---|---|
| GPT 5.4 / 5.5 / 5.6 | body `service_tier: "priority"` (OpenAI's priority processing) | A surcharged pay-as-you-go API tier — **not** a subscription feature |
| Claude Opus 4.6 / 4.7 / 4.8 | body `speed: "fast"` + header `anthropic-beta: fast-mode-2026-02-01` | Also pay-as-you-go; a subscription account without usage credits gets Anthropic's 429 "Usage credits are required for fast mode" (verified 2026-07-12), surfaced **as-is** — an account problem, not lack of support |

No other model family (Gemini / DeepSeek / Qwen / Llama / MiniMax…) has a
fast tier at all.

## 2. Detection: `supports_fast(provider, model)` — two tiers

Entry point: `openprogram/webui/_model_listing/listing.py`. Strips the
wire-format `"provider:"` prefix first (the runtime records the current
model as `openai-codex:gpt-5.5`).

1. **Hand-written subscription entries** — `openai-codex` and `claude-code`
   (`_SUBSCRIPTION_FAST_PROVIDERS`). These are subscription front-doors the
   public catalogue doesn't index; they consult the family table
   `enabled_models.default_fast(model_id)`: id starts with
   `gpt-5.4/5.5/5.6` → True; id contains `opus-4-6/4-7/4-8` (hyphen or dot
   spelling) → True; else False.
2. **Everything else is automatic** — models.dev `speed_modes`: the model
   has a mode with `service_tier == "priority"` or `id == "fast"` → True;
   no such mode, or the provider is unknown to the catalogue → False.

Ruling: only those two subscription entries get hand-written rules; private
gateways (e.g. frontier-intelligence) are NOT special-cased — unknown to the
catalogue means no fast button. Use the config override (§3) for exceptions.

## 3. Storage: rules + on-demand computation, almost nothing on disk

| Layer | Location | Persistence |
|---|---|---|
| Family table | `providers/enabled_models.py::default_fast` + `listing.py::_SUBSCRIPTION_FAST_PROVIDERS` | source code |
| Explicit user override | `"fast": true/false` on a model spec row in `~/.openprogram/config.json` (row wins; honored by `_build_model_from_row`) | config file (optional; currently unused) |
| `Model.fast` field | backfilled from the family table at registry build / dynamic registration (codex `ensure_codex_model_registered`, anthropic `ensure_anthropic_model_registered`) | memory only (`ENABLED_MODELS` is an in-process dict) |
| models.dev catalogue | `webui/_model_listing/sources/models_dev.py`, `https://models.dev/api.json` | remote; 1h in-memory cache locally (60s retry on failure), **no disk cache** |

Known gap: offline / models.dev outage after the memory cache expires makes
the automatic tier return False across the board (subscription entries are
unaffected). Possible mitigation (not built): persist the last good
catalogue under `~/.openprogram/cache/`.

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
| `providers/openai_codex` (ChatGPT subscription) | same passthrough (added 2026-07-12; unknown fields are ignored upstream per OpenAI convention) |
| `providers/anthropic` | `opts.service_tier` present **and** `model.fast` → body `extra_body={"speed":"fast"}` + `_BETA_FAST` header (appended via `_build_client(fast=...)`, never clobbering other betas) |
| all other wires | no passthrough; the knob never leaves the process |

## 6. File map

```
openprogram/providers/types.py                     Model.fast field
openprogram/providers/enabled_models.py            default_fast table + config-row backfill
openprogram/providers/openai_codex/{openai_codex,runtime}.py   codex passthrough + registration backfill
openprogram/providers/anthropic/{anthropic,_claude_code_direct_runtime}.py  Claude fast wire + registration backfill
openprogram/webui/_model_listing/listing.py        supports_fast entry; _model_to_dict exposes fast
openprogram/webui/_model_listing/sources/models_dev.py  catalogue fetch + 1h memory cache
openprogram/webui/routes/runtime.py                /api/agent_settings emits chat.fast
web/lib/session-store/types.ts                     AgentBadgeInfo.fast type
web/components/chat/composer/index.tsx             toggle visibility + send gate
```

Change guide: add/remove fast for a model → touch only the `default_fast`
table (or that model's config row); change detection logic → touch only
`listing.py::supports_fast`; a new provider wanting fast → nothing to do
(it works automatically once models.dev knows it).
