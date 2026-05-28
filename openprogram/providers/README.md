# `openprogram/providers/`

> pi_ai — Unified LLM API

## Overview

Python mirror of @mariozechner/pi-ai.

This module also keeps the older ``openprogram.providers`` compatibility
surface alive, so existing imports like ``detect_provider`` or
``AnthropicRuntime`` continue to work during the provider refactor.

## Files in this directory

- **`api_registry.py`** — API provider registration system
- **`cli.py`** — OAuth login CLI for pi-ai
- **`configuration.py`** — Shared provider configuration framework
- **`env_api_keys.py`** — Environment variable API key resolution
- **`models.py`** — Model registry and utilities
- **`models_generated.py`** — Auto-generated model definitions
- **`register.py`** — Register all built-in API providers
- **`registry.py`** — openprogram.providers.registry
- **`stream.py`** — Unified streaming functions
- **`thinking_catalog.py`** — Thinking-capability overrides
- **`types.py`** — Core type definitions

## Sub-packages

- **`_shared/`** — Shared helpers used by multiple provider stream implementations
- **`amazon_bedrock/`** — Amazon Bedrock Converse Stream provider
- **`anthropic/`** — Anthropic provider
- **`azure_openai_responses/`** — Azure OpenAI Responses API provider
- **`github_copilot/`** — GitHub Copilot auth adapter + helpers
- **`google/`** — Google Generative AI provider
- **`google_gemini_cli/`** — Google Gemini CLI / Cloud Code Assist provider
- **`openai_codex/`** — OpenAI Codex (ChatGPT subscription) provider
- **`openai_completions/`** — OpenAI Chat Completions API provider
- **`openai_responses/`** — OpenAI Responses API provider
- **`utils/`**

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
