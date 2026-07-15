# Request building: Context → per-provider parameters

Status: **decided** · Created: 2026-06-24

> The providers layer has a single responsibility: **take a ready-made `Context`, translate it into the wire
> request for the current provider, and apply prompt caching according to that provider's mechanism.**
>
> providers does not care how the `Context` was built — how identity/tools/memory are assembled in the system
> prompt, whether to segment it, that is the concern of the upstream [`../context/`](../context/). This layer only handles "what to receive, what to translate,
> what to send".

---

## 1. Core: one unified format + one translation per provider

Upstream produces a provider-agnostic `Context`; this layer dispatches by `model.api` to the corresponding provider's
translation, converting it into the request that provider expects. All provider differences are contained in the translation
layer, and upstream only ever deals with `Context`.

```
Context (already built upstream)
    │  dispatch by model.api
    ▼
that provider's translation: Context → its wire request
```

The industry-consensus approach (opencode / hermes / openclaw all do this).

## 2. Unified format: Context

```
Context {
  system_prompt    system prompt (this layer treats it as ready-made content, not asking how it came to be)
  messages         conversation (user / assistant / tool_result)
  tools            tool list
}
```

The three are stored separately, with system standalone. This framework's `Context` uses standalone system as its baseline — translating to
APIs with standalone system (Anthropic / Bedrock / Gemini) maps directly; when translating to the OpenAI family,
that provider's translation folds system into messages.

## 3. Translation layer: where system lands + field mapping

One translation per provider (`_build_system` / `_build_messages` / `_build_tools`), dispatched by
`model.api`. The core differences are where system lands and the field names:

| provider style | where system lands | conversation field | tools field |
|---|---|---|---|
| anthropic-messages | standalone `system` | `messages` | `tools` (strict inside the tool object) |
| openai-completions | system/developer message goes into `messages[0]` | `messages` | `tools` (strict is a boolean) |
| openai-responses / codex | extracted into the `instructions` parameter | `input` | `tools` |
| google (gemini) | `systemInstruction`; assistant→model | `contents` (content is called parts) | `tools` |
| bedrock | standalone system | `messages` | `tools` |

The translation also smooths over each provider's idiosyncratic blocks: thinking-block signatures, whether tool_use arguments are objects or strings, and tool schema
dialects (strict / additionalProperties). This part is already implemented.

## 4. Caching: three modes + a declaration layer

Each provider's caching mechanism is fundamentally different, so **don't force one abstraction over them all** — split into three modes, with each provider declaring which one it belongs to.

| mode | who | approach |
|---|---|---|
| `explicit` | Anthropic, Bedrock | place cache breakpoints explicitly in the request (cache_control / cachePoint) |
| `auto` | OpenAI family | no breakpoints, automatic prefix caching; can pass a cache key (prompt_cache_key) |
| `none` | compatible providers without caching | do nothing |
| `out_of_band` | Gemini | first call a separate API to store the cache object and get an ID, then carry the ID next time (two steps; for now only read hit stats) |

### The cache_spec declaration layer

Copying the declarative paradigm of thinking under `models/`: one `cache.json` per provider declaring
`mode` + the cache-key parameter name + TTL mapping + breakpoint limit. The shared module `cache_spec.py` loads it
(`get_cache_spec` / `cache_mode` / `ttl_for_retention` / `cache_key_param`),
and provider code reads the declaration to decide behavior, rather than hardcoding the rules in `stream_simple`. A provider with no declaration
falls back to `none` (same as thinking's OpenAI-compatible fallback).

```json
{
  "mode": "explicit",
  "breakpoint_format": "cache_control",        // or "cachePoint" (bedrock)
  "retention_ttl_map": {"short": null, "long": "1h"},
  "max_breakpoints": 4
}
```

```json
{ "mode": "auto", "cache_key_param": "prompt_cache_key" }
```

```json
{ "mode": "none" }
```

### Where breakpoints go (explicit mode)

The caller can explicitly mark `cache_control` on a content block, and it passes through verbatim to apply after that block
(see [`../../plans/cache-control-passthrough.md`](../../plans/cache-control-passthrough.md));
when unmarked, the provider automatically marks the last block. The breakpoint limit (4 for Anthropic) is
constrained by cache_spec's `max_breakpoints`; on overflow, the lower-priority ones are dropped by the `tools > system > messages` priority.

> If breakpoint positions need to be decided uniformly by "the stable segments marked upstream", this relies on the existing per-block marker field
> `TextContent.cache_control` — upstream marks the block on a stable segment, and this layer passes it through. **No new
> Context-level structure is introduced, nor is any change to the system_prompt type required.**

## 5. The interface with upstream

```
../context/                     providers (this layer)
build Context, decide content,  ──→   translate into each provider's wire, apply caching by mode
mark cache_control on blocks   Context     read cache_spec, pass through cache markers
```

The contract = `Context` (a content block may carry `cache_control`). How upstream builds the context is fully
decoupled from this layer: adding a provider only touches this layer + one `cache.json`, and changing context building only touches `../context/`.

## 6. Implementation (ported from opencode)

The caching policy layer copies opencode's `cache-policy.ts` + `protocols/utils/cache.ts`
(`references/opencode/packages/llm/src/`), reproduced in Python. Delivery checklist:

| item | file | what was taken from opencode |
|---|---|---|
| declaration loading | `providers/cache_spec.py` + each provider's `cache.json` | the `RESPECTS_INLINE_HINTS` "declare caching capability per provider" approach |
| automatic breakpoint policy | `apply_cache_policy` in `providers/cache_policy.py` | `applyCachePolicy`: mark the last tool + the most recent user message, without overriding the caller's manual markers |
| breakpoint budget | `_take`/`max_breakpoints` inside `cache_policy.py` | `Breakpoints{remaining,dropped}` + the 4-breakpoint limit |
| TTL bucketing | `_ttl_bucket` in `cache_policy.py` | `ttlBucket` (≥3600s → "1h", otherwise default 5m) |
| tool-level breakpoints | the `Tool.cache_control` field + anthropic `_build_tools` passthrough | opencode's ability to mark cache on tools too |

Integration point: anthropic `stream_simple` calls `apply_cache_policy` before building messages/tools;
`_get_cache_control` is changed to read the ttl mapping and `long_ttl_endpoints` from `cache.json`, no longer hardcoded.

bedrock is also declared as explicit, but it uses `cachePoint` (a standalone block) rather than `cache_control` attached to a block,
and it already carries its own "place a breakpoint on the last message" logic; folding it too into the unified
`apply_cache_policy` (tool breakpoints) is a later increment, not wired up this time — the current state works.

One difference from opencode: opencode's `system` is a segmented array and can mark a breakpoint individually on the "last system segment";
this layer's `Context.system_prompt` is a single string, and the system breakpoint is placed by each provider's
`_build_system` on that whole block. The policy layer only covers tools + messages.
