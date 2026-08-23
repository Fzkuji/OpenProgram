# `openprogram/providers/`

> pi_ai — Unified LLM API

## Overview

Python mirror of @mariozechner/pi-ai.

Runtime construction goes through :func:`create_runtime` (auto-detection
via :func:`detect_provider`). Provider-specific Runtime classes live in
their provider packages (e.g. ``openprogram.providers.openai_codex``).

## Files in this directory

- **`_config_read.py`** — Raw, migration-free read of the ``providers`` section of
- **`_provider_meta.py`** — Provider-level api/base_url from providers/<p>/provider.json
- **`api_registry.py`** — API provider registration system
- **`budget.py`** — Budget enforcement for one provider request
- **`cache_policy.py`** — Prompt-cache breakpoint policy
- **`cache_spec.py`** — Load and query per-provider cache.json specs
- **`callable_model.py`** — CallableModel
- **`cli.py`** — OAuth login CLI for pi-ai
- **`configuration.py`** — Shared provider configuration framework
- **`default_llm.py`** — Build a plain text-in/text-out callable on the default agent's model
- **`enabled_models.py`** — Runtime model registry
- **`env_api_keys.py`** — Provider API-key resolution
- **`initialization.py`** — Explicit, process-wide provider runtime initialization
- **`metadata.py`** — Provider-level metadata: display labels, env-var mappings, default
- **`models.py`** — Model registry and utilities
- **`recording.py`** — Record provider calls to a JSONL recording file
- **`register.py`** — Register the built-in API providers and authentication adapters
- **`registry.py`** — openprogram.providers.registry
- **`replay.py`** — Replay a recorded provider recording file without touching the network
- **`storage.py`** — Persistence layer for provider / model configuration
- **`stream.py`** — Unified streaming functions
- **`structured_output.py`** — Strict JSON Schema output normalization and local validation
- **`thinking_spec.py`** — Load and query per-provider thinking.json specs
- **`types.py`** — Core type definitions

## Sub-packages

- **`_schema/`** — Tool-schema normalization across providers
- **`_shared/`** — Shared helpers used by multiple provider stream implementations
- **`amazon_bedrock/`** — Amazon Bedrock Converse Stream provider
- **`anthropic/`** — Anthropic provider
- **`azure_openai_responses/`** — Azure OpenAI Responses API provider
- **`deepseek/`**
- **`github_copilot/`** — GitHub Copilot auth adapter + helpers
- **`google/`** — Google Generative AI provider
- **`google_gemini_cli/`** — Google Gemini CLI / Cloud Code Assist provider
- **`openai_codex/`** — OpenAI Codex (ChatGPT subscription) provider
- **`openai_completions/`** — OpenAI Chat Completions API provider
- **`openai_responses/`** — OpenAI Responses API provider
- **`sources/`** — External catalogues that enrich fetched model rows with metadata
- **`utils/`**
- **`xai_subscription/`** — xAI Grok subscription (SuperGrok / X Premium+ OAuth)

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
