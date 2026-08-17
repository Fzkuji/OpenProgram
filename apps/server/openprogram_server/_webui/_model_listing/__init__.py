"""Unified provider + model listing for the webui.

Refactored from a 1388-line monolith into per-concern modules. The
``__init__`` re-exports every name external code consumes off the
``openprogram.webui._model_listing`` package so callers import from one
place.

Layout::

    _model_listing/
      __init__.py        # this file — public API re-exports
      setup_hints.py     # SETUP_HINTS dict + _setup_hint
      listing.py         # list_providers / list_models_for_provider / list_enabled_models / spec_row_for
      toggle.py          # toggle_provider + toggle_model
      test_provider.py   # connectivity probe (Codex-aware)
      fetchers/
        __init__.py       # dispatcher: _load_fetcher + fetch_and_normalize + fetch_models_remote
        openai_compat.py  # generic OpenAI-compatible /v1/models (shared fallback)

Config persistence (spec rows, custom providers/models CRUD, base-URL
resolution) lives in ``openprogram.providers.storage`` — this package is
the presentation layer on top of it and imports downward only.

A provider whose ``/v1/models`` isn't OpenAI-compatible ships its own
``providers/<name>/list_models.py`` exposing ``fetch(provider_id, timeout)``;
the dispatcher loads it by directory name (same convention as
``probe_thinking.probe()``). See ``docs/design/providers/models/overview.md`` §4.1.

Adding a new provider: usually NOTHING. The credential kind, fetch
fetcher, chat api-stamp, and base convention are all DERIVED from the
provider's wire ``api`` (``openprogram.providers.metadata.default_api_for`` reads it from the
static ``enabled_models`` rows, or detects an Anthropic ``…/anthropic``
endpoint for community providers). A provider that's in ``enabled_models``
or whose models.dev base reveals its wire needs no per-provider code.

The optional touch-points, only when something can't be derived:

  1. Append to ``openprogram.providers.metadata.PROVIDER_LABELS`` to pin a
     display name, and to ``metadata.FETCH_MODELS_PROVIDERS`` only for an
     OpenAI-compatible /v1/models lister not already covered.
  2. Map its env var in ``metadata.ENV_API_KEYS``.
  3. Add to ``metadata.PROVIDER_DEFAULT_API`` ONLY to correct a
     ``enabled_models`` mislabel or pin a multi-api provider's route —
     it is normally empty.
  4. (Optional) Add a ``setup_hints._SETUP_HINTS`` entry for
     non-paste-a-key flows.
  5. (Optional) Add ``providers/<name>/list_models.py`` with a
     ``fetch(provider_id, timeout)`` function if /v1/models needs custom
     handling — the dispatcher finds it by directory name, no registration.
  6. Add at least one Model row in
     ``providers/enabled_models.py`` (auto-registers the provider
     id with ``get_providers()``).
"""
from __future__ import annotations

# Public listing API ------------------------------------------------
from .listing import (
    list_enabled_models,
    list_models_for_provider,
    list_providers,
    spec_row_for,
)

# Public mutators ---------------------------------------------------
from .toggle import (
    toggle_model,
    toggle_provider,
)

# Public RPC entry points ------------------------------------------
from .fetchers import fetch_models_remote
from .test_provider import test_provider
from .credentials import (
    validate_credential,
    provider_auth_status,
    provider_auth_status_async,
    provider_id_for_env_var,
)

# Private symbols still imported by name from other modules --------
# (``setup_sections``). Provider metadata (labels / env vars / api routing)
# lives in ``openprogram.providers.metadata``; config persistence in
# ``openprogram.providers.storage`` — import those there, not here.
from .setup_hints import _SETUP_HINTS, _setup_hint


__all__ = [
    # Public listing
    "list_providers",
    "list_models_for_provider",
    "list_enabled_models",
    "spec_row_for",
    # Public mutators
    "toggle_provider",
    "toggle_model",
    # Public RPC
    "fetch_models_remote",
    "test_provider",
    "validate_credential",
    "provider_auth_status",
    "provider_auth_status_async",
    "provider_id_for_env_var",
    # Re-exported privates (used by other modules)
    "_setup_hint",
]
