"""Read-only access to the ``providers`` section of ~/.openprogram/config.json,
importable from ``openprogram.providers`` without pulling in webui.

The runtime registry (``enabled_models._load``) loads user-enabled model
spec rows from config, so it needs the config's providers dict — but
``openprogram.providers`` must NOT import ``openprogram.webui`` (circular).
This is the same three-line read the webui/CLI use (via ``setup._read_config``),
scoped to the one section the providers layer needs. Profile-aware through
``openprogram.paths.get_config_path``.
"""
from __future__ import annotations

import json
from typing import Any

from openprogram.paths import get_config_path


def read_providers_config() -> dict[str, dict[str, Any]]:
    """The ``providers`` sub-tree of config.json, or ``{}`` if absent/broken.

    Never raises: a missing or malformed config is a legal fresh-install
    state (→ empty registry)."""
    try:
        cfg = json.loads(get_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    providers = cfg.get("providers")
    return providers if isinstance(providers, dict) else {}
