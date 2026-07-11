# Thinking / Effort Subsystem Design

> For the overall design of the model catalog and provider configuration, see [models.md](models.md). This document only covers the control logic for thinking effort.

## 1. Problem

Different LLM providers control reasoning depth in different ways — parameter names differ (`effort` / `reasoning_effort` / `thinkingBudget`), value types differ (strings / token counts), and the number of supported levels differs (from 3 to 6). The framework needs to hide these differences and give the user a single unified slider.

## 2. API Parameters Across Vendors

| Provider | API parameter | Value type | Levels |
|---|---|---|---|
| Anthropic | `output_config.effort` | string | low/medium/high/xhigh/max (3-5 levels depending on the model) |
| Anthropic (legacy) | `thinking.budget_tokens` | token count | continuous value |
| OpenAI Responses | `reasoning.effort` | string | minimal/low/medium/high/xhigh |
| OpenAI Chat | `reasoning_effort` | string | low/medium/high |
| Google Gemini | `thinkingConfig.thinkingBudget` | token count | continuous value |
| DeepSeek V4 | `reasoning_effort` | string | minimal/low/medium/high/max |
| DeepSeek R1 | none | none | on/off only, not adjustable |
| OpenRouter | passes through the underlying parameter | same as underlying | determined from `supported_parameters` |

## 3. Unified Levels

The framework defines 6 levels + off:

```
ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
```

These are the framework's abstract names, unrelated to any API's parameter names. Each model may support only a subset of them (for example, Opus 4.5 only supports low/medium/high). The UI shows the slider according to the levels each model actually supports.

## 4. Data Flow: From User Selection to API Request

The complete call chain:

```
User selects "high" in the UI
        │
        ▼
┌─ _thinking.py ──────────────────────────────────┐
│ get_thinking_config_for_model(provider, model)   │
│ → take the model's thinking_levels from           │
│   listing.list_models_for_provider, build UI      │
│   picker options                                  │
│ → return {options: [off,low,medium,high,...]}     │
└──────────────────────────────┬───────────────────┘
                               │ user selected "high"
                               ▼
┌─ session_config.py ─────────────────────────────┐
│ _normalize_thinking("high") → "high"             │
│ store in SessionDB (per-session persistence)      │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
┌─ dispatcher → agent_loop ───────────────────────┐
│ SimpleStreamOptions(reasoning="high")            │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
┌─ provider's stream_simple() ────────────────────┐
│ thinking_spec.translate_reasoning(               │
│     "anthropic", "claude-opus-4-8", "high"       │
│ )                                                │
│ → read thinking.json → effort_map → "high"        │
│ → insert into the request body:                   │
│   {"output_config": {"effort": "high"}}          │
└──────────────────────────────┬───────────────────┘
                               │
                               ▼
                          Anthropic API
```

## 5. Deriving thinking_levels

How many levels each model shows in the UI is derived uniformly by `listing.py` when it builds the model list. Derivation priority (highest to lowest):

| Priority | Data source | Description | Example |
|---|---|---|---|
| 1 | `model_overrides` in thinking.json | written automatically from API capabilities or configured manually | Opus 4.8: 5 levels, Opus 4.5: 3 levels |
| 2 | provider-level `effort_map`/`budget_map` in thinking.json | the provider's generic mapping | Anthropic defaults to 6 levels |
| 3 | `thinking_levels` in Fetch data (models.json) | obtained from models.dev or the API during Fetch | DeepSeek V4: 5 levels |
| 4 | OpenAI-compatible fallback | used automatically for providers without thinking.json | groq/mistral: 3 levels |

The derivation logic lives in `list_models_for_provider()` in `listing.py`:

```python
# first try thinking.json (priority 1-2)
levels, default, variant = derive_thinking_fields(provider_id, model_id, reasoning)
# if thinking.json gives no result, look at Fetch data (priority 3)
if not levels and raw.get("thinking_levels"):
    levels = list(raw["thinking_levels"])
# the priority 4 fallback is already included inside derive_thinking_fields
```

**Single source of truth:** all three consumers — `_thinking.py` (the UI picker), `list_enabled_models` (the model list), and `list_models_for_provider` (provider details) — go through the same derivation path. `_thinking.py` delegates to `list_models_for_provider`, and `list_enabled_models` delegates to it as well. There is no divergence.

## 6. translate_reasoning: Framework Level → API Value

The user selects a framework level (such as `"high"`), and before the provider sends the request it must be translated into a value the API understands. The translation logic lives in `thinking_spec.translate_reasoning()`:

```python
def translate_reasoning(provider_id, model_id, level):
    spec = get_thinking_spec(provider_id)

    # 1. check model_overrides (specific to the model)
    override = spec.get("model_overrides", {}).get(model_id)
    if override:
        emap = override.get("effort_map")
        if emap is not None:
            return emap.get(level) if emap else None  # empty dict = not supported

    # 2. provider-level translation
    if spec["wire_format"] == "effort_string":
        return spec["effort_map"].get(level)
    if spec["wire_format"] == "budget_tokens":
        return spec["budget_map"].get(level)

    return None  # wire_format == "none"
```

The return value is inserted directly into each provider's API request body. Each provider's `stream_simple()` only needs to worry about "given a value, which request field to put it in," not about the translation logic.

## 7. Probing Strategy

Different providers expose their thinking capabilities to different degrees. The framework uses a three-layer strategy to obtain information automatically as much as possible:

### Layer 1: API capabilities (precise)

During Fetch, call the API to get the supported status of each level.

Currently only Anthropic supports this: `GET /v1/models/{id}` → `capabilities.effort.{level}.supported`. The result is written into the `model_overrides` of `thinking.json` (via `probe_thinking.py --update`).

### Layer 2: inferring whether reasoning is present

For providers without a capabilities API, at least determine whether the model supports reasoning:

- **models.dev**: `reasoning: true/false`
- **probe_thinking.py**: inferred from the model id (such as `v4` / `reasoner` / `o3`)
- **OpenRouter**: `supported_parameters` contains `"reasoning"` → supported

Once `reasoning=true` is known, the levels are assigned using the provider-level mapping in thinking.json.

### Layer 3: probe-by-downgrade at call time (to be implemented)

For models with no information at all, when sending a request start from max and step down level by level to minimal, skip on a 400, and cache the result.

## 8. Automation

Integration of probe_thinking.py with Fetch:

1. Each provider folder has a `probe_thinking.py` that exposes a `probe()` function
2. `fetchers/__init__.py` automatically calls `_load_probe(provider_id)` → `probe()` during the enrichment step
3. The result is used to fill in the missing `reasoning` field in the Fetch data
4. Anthropic's probe can also use the `--update` argument to directly update thinking.json

| Provider | Probing method |
|---|---|
| anthropic | `/v1/models/{id}` capabilities (precise down to each level) |
| deepseek | model id inference (v4→reasoning+effort, reasoner→reasoning without effort) |
| openai_codex | OpenAI models API + model id inference (o1/o3/gpt-5) |
| openai_responses | OpenRouter `supported_parameters` |
| openai_completions | model id inference (o1/o3/gpt-5) |
| google | model name inference |

**Providers without probe_thinking.py do not affect Fetch** — the enrichment step catches the ImportError and silently skips.

## 9. Key Design Decisions

### 9.1 The framework does not control reasoning length

It only passes the depth level (effort), letting the API adaptively decide how many tokens to use. Gemini and legacy Anthropic models need a specific token count, mapped via `budget_map`.

### 9.2 Empty effort_map = no effort control

`"effort_map": {}` in `model_overrides` means the model has reasoning capability but does not support effort adjustment (such as DeepSeek R1 — it always reasons at full force). `translate_reasoning` returns `None` for an empty map, and the provider does not send the effort parameter.

### 9.3 Automatic fallback for providers without thinking.json

When `get_thinking_spec()` cannot find a thinking.json, it returns the OpenAI-compatible fallback (`effort_string` + `low/medium/high`). A community provider that is added works without any configuration.

### 9.4 Provider alias

`claude-code` and `anthropic` share the same thinking.json (same API, same models). `_THINKING_ALIASES = {"claude-code": "anthropic"}` does the mapping, without copying the file.

## 10. File Inventory

| File | Responsibility |
|---|---|
| `providers/<provider>/thinking.json` | declares this provider's wire_format, effort_map, model_overrides |
| `providers/<provider>/probe_thinking.py` | automatically probes reasoning capability during Fetch |
| `providers/<provider>/models.json` | the model list generated by Fetch (includes thinking_levels, gitignore) |
| `providers/thinking_spec.py` | loads thinking.json, translate_reasoning, derive_thinking_levels, alias, fallback |
| `providers/thinking_catalog.py` | uses derive_thinking_fields at startup to populate the thinking fields of the Model object |
| `providers/types.py` | `ThinkingLevel` type definition, `SimpleStreamOptions.reasoning` field |
| `webui/_thinking.py` | UI picker construction (takes data from listing), apply_thinking_effort (sets the value at runtime) |
| `webui/_model_listing/listing.py` | list_models_for_provider (the single entry point that uniformly derives thinking_levels) |
| `webui/_model_listing/fetchers/__init__.py` | Fetch enrichment: automatically calls probe_thinking |
| `webui/_model_listing/fetchers/anthropic.py` | Anthropic Fetch: extracts thinking_levels from capabilities |
| `agent/session_config.py` | `VALID_THINKING` validation, `reasoning_from_config` conversion |

## 11. Actual Levels Per Model Today

Browser-verified results (2026-06-18):

| Provider | Model | Levels | Source |
|---|---|---|---|
| claude-code | opus-4-8 | low/medium/high/xhigh/max (5) | API capabilities |
| claude-code | fable-5 | low/medium/high/xhigh/max (5) | API capabilities |
| claude-code | sonnet-4-6 | low/medium/high/max (4) | API capabilities |
| claude-code | opus-4-5 | low/medium/high (3) | API capabilities |
| deepseek | v4-flash | minimal/low/medium/high/max (5) | Fetch + thinking.json |
| deepseek | v4-pro | minimal/low/medium/high/max (5) | Fetch + thinking.json |
| openai-codex | gpt-5.5 | low/medium/high/xhigh/max (5) | thinking.json override |
| minimax-cn | MiniMax-M3 | low/medium/high (3) | fallback |
| openrouter | gemma-4 / qwen3.7 | low/medium/high (3) | fallback |
| openrouter | llama-3.3 | none | reasoning=false |
