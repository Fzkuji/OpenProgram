"""Unified provider + model listing for the webui.

Refactored from a 1388-line monolith into per-concern modules. The
``__init__`` re-exports every name external code consumes off the
``openprogram.webui._model_listing`` package so callers import from one
place.

Layout::

    _model_listing/
      __init__.py        # this file — public API re-exports
      providers.py       # static metadata + _is_configured
      setup_hints.py     # SETUP_HINTS dict + _setup_hint
      storage.py         # config IO + custom_models CRUD + URL/key resolution
      listing.py         # list_providers / list_models_for_provider / list_enabled_models
      toggle.py          # toggle_provider + toggle_model
      test_provider.py   # connectivity probe (Codex-aware)
      fetchers/
        __init__.py      # _FETCHERS map + fetch_models_remote dispatcher
        _common.py       # generic OpenAI-compatible /v1/models
        anthropic.py
        bedrock.py
        claude_code.py
        codex.py
        deepseek.py      # fetcher + curated metadata table
        github_copilot.py
        google.py

Adding a new provider: usually NOTHING. The credential kind, fetch
fetcher, chat api-stamp, and base convention are all DERIVED from the
provider's wire ``api`` (``providers._default_api_for`` reads it from the
static ``enabled_models`` rows, or detects an Anthropic ``…/anthropic``
endpoint for community providers). A provider that's in ``enabled_models``
or whose models.dev base reveals its wire needs no per-provider code.

The optional touch-points, only when something can't be derived:

  1. Append to ``providers._PROVIDER_LABELS`` to pin a display name, and
     to ``providers._FETCH_MODELS_PROVIDERS`` only for an OpenAI-compatible
     /v1/models lister not already covered.
  2. Map its env var in ``providers._ENV_API_KEYS``.
  3. Add to ``providers._PROVIDER_DEFAULT_API`` ONLY to correct a
     ``enabled_models`` mislabel or pin a multi-api provider's route —
     it is normally empty.
  4. (Optional) Add a ``setup_hints._SETUP_HINTS`` entry for
     non-paste-a-key flows.
  5. (Optional) Add a dedicated fetcher under ``fetchers/`` and
     register it in ``fetchers.__init__._FETCHERS`` if /v1/models
     needs custom handling.
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
)

# Public mutators ---------------------------------------------------
from .storage import (
    add_custom_models,
    get_provider_config,
    remove_custom_model,
    replace_fetched_models,
    set_provider_config,
)
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
# (``_runtime_management``, ``_model_tools``, ``setup_sections``).
from .providers import _is_configured
from .storage import _read_providers_cfg, _write_providers_cfg

# Tables and helpers that legacy code reads off the module ---------
# Keep these accessible for now; flag as `_`-prefixed private.
from .providers import (
    _CLI_PROVIDERS,
    _ENV_API_KEYS,
    _FETCH_MODELS_PROVIDERS,
    _PROVIDER_DEFAULT_API,
    _PROVIDER_LABELS,
    _default_api_for,
    _label,
    _prettify,
)
from .setup_hints import _SETUP_HINTS, _setup_hint
from .storage import _resolve_api_key, _resolve_base_url


__all__ = [
    # Public listing
    "list_providers",
    "list_models_for_provider",
    "list_enabled_models",
    # Public mutators
    "add_custom_models",
    "replace_fetched_models",
    "remove_custom_model",
    "get_provider_config",
    "set_provider_config",
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
    "_is_configured",
    "_read_providers_cfg",
    "_write_providers_cfg",
    "_resolve_api_key",
    "_resolve_base_url",
    "_label",
    "_prettify",
    "_setup_hint",
]
