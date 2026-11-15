# `openprogram/models/providers/`

> pi_ai — Unified LLM API

## Overview

Python mirror of @mariozechner/pi-ai.

Provider detection is :func:`detect_provider`. Building a Runtime is the
runtime layer's (``openprogram.runtime.agent.runtime.factory.create_runtime``,
with the provider-specific classes in
``openprogram.runtime.agent.provider_runtimes``); the provider packages
here hold only model, auth and wire code.

## Files in this directory

- **`structured_output.py`** — Strict JSON Schema output normalization and local validation
- **`types.py`** — Core type definitions

## Sub-packages

- **`adapters/`** — Per-provider adapters: one directory per provider, each with a ``provider.json``
- **`catalog/`** — Which providers and models exist and are enabled
- **`recording/`** — Record, replay and capture provider requests
- **`schema/`** — Tool-schema normalization across providers
- **`settings/`** — How a provider call is configured
- **`shared/`** — Shared helpers used by multiple provider stream implementations
- **`streaming/`** — The provider call path
- **`utils/`**

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
