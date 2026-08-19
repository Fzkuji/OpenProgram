"""Raw, migration-free read of the ``providers`` section of
~/.openprogram/config.json.

The runtime registry (``enabled_models._load``) loads user-enabled model
spec rows from config. It must NOT go through
``openprogram.providers.storage._read_providers_cfg`` — that read runs the
one-time spec migration, and the migration's own persist step reloads the
registry, so routing the registry through it would recurse. This module is
the reentrancy firewall: the same three-line read as ``setup._read_config``,
scoped to the one section the registry needs, never migrating. Profile-aware
through ``openprogram.paths.get_config_path``.
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
