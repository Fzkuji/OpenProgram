# `openprogram/models/listing/`

> Unified provider + model listing every interface shows.

## Overview

Refactored from a 1388-line monolith into per-concern modules. The
``__init__`` re-exports every name external code consumes off the
``openprogram.models.listing`` package so callers import from one
place.

Layout::

    listing/
      __init__.py        # this file — public API re-exports
      setup_hints.py     # SETUP_HINTS dict + _setup_hint
      listing.py         # list_providers / list_models_for_provider / list_enabled_models / spec_row_for
      toggle.py          # toggle_provider + toggle_model
      test_provider.py   # connectivity probe (Codex-aware)
      fetchers/
        __init__.py       # dispatcher: _load_fetcher + fetch_and_normalize + fetch_models_remote
        openai_compat.py  # generic OpenAI-compatible /v1/models (shared fallback)

Config persistence (spec rows, custom providers/models CRUD, base-URL
resolution) lives in ``openprogram.models.providers.settings.storage`` — this package is
the presentation layer on top of it and imports downward only.

A provider whose ``/v1/models`` isn't OpenAI-compatible ships its own
``providers/adapters/<name>/list_models.py`` exposing ``fetch(provider_id, timeout)``;
the dispatcher loads it by directory name (same convention as
``probe_thinking.probe()``). See ``docs/design/providers/models/overview.md`` §4.1.

Adding a new provider: usually NOTHING. The credential kind, fetch
fetcher, chat api-stamp, and base convention are all DERIVED from the
provider's wire ``api`` (``openprogram.models.providers.catalog.metadata.default_api_for`` reads it from the
static ``enabled_models`` rows, or detects an Anthropic ``…/anthropic``
endpoint for community providers). A provider that's in ``enabled_models``
or whose models.dev base reveals its wire needs no per-provider code.

The optional touch-points, only when something can't be derived:

  1. Append to ``openprogram.models.providers.catalog.metadata.PROVIDER_LABELS`` to pin a
     display name, and to ``metadata.FETCH_MODELS_PROVIDERS`` only for an
     OpenAI-compatible /v1/models lister not already covered.
  2. Map its env var in ``metadata.ENV_API_KEYS``.
  3. Add to ``metadata.PROVIDER_DEFAULT_API`` ONLY to correct a
     ``enabled_models`` mislabel or pin a multi-api provider's route —
     it is normally empty.
  4. (Optional) Add a ``setup_hints._SETUP_HINTS`` entry for
     non-paste-a-key flows.
  5. (Optional) Add ``providers/adapters/<name>/list_models.py`` with a
     ``fetch(provider_id, timeout)`` function if /v1/models needs custom
     handling — the dispatcher finds it by directory name, no registration.
  6. Add at least one Model row in
     ``providers/catalog/enabled_models.py`` (auto-registers the provider
     id with ``get_providers()``).

## Files in this directory

- **`credentials.py`** — Unified provider-credential validation
- **`detected.py`** — What Add provider offers before anything is typed
- **`key_check.py`** — A key's check as one line, and what connecting a provider turns on
- **`listing.py`** — Catalog listing
- **`probe_log.py`** — The requests OpenProgram sends a provider on its own behalf
- **`provider_models.py`** — models.dev enrichment for the live browse path
- **`setup_hints.py`** — Provider-specific setup instructions surfaced in the web UI's
- **`test_provider.py`** — Connectivity probe
- **`toggle.py`** — Enable / disable a provider or one of its models

## Sub-packages

- **`fetchers/`** — Model-list dispatcher

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
