"""Raw, migration-free read of the ``providers`` section of
~/.openprogram/config.json.

The runtime registry (``enabled_models._load``) loads user-enabled model
spec rows from config. It must NOT go through
``openprogram.models.providers.settings.storage._read_providers_cfg`` — that read runs the
one-time spec migration, and the migration's own persist step reloads the
registry, so routing the registry through it would recurse. This module is
the reentrancy firewall: the same three-line read as ``state.config.read_config``,
scoped to the one section the registry needs, never migrating. Profile-aware
through ``openprogram.state.paths.get_config_path``.
"""
from __future__ import annotations

import json
from typing import Any

from openprogram.state.paths import get_config_path


# The last file read and what it parsed to. One provider-status answer asks
# 1,800 times (once per provider per question), and parsing a 130 KB config
# each time took a second; the file is parsed again only when it changes.
_parsed: tuple[tuple[str, int, int, int], dict[str, dict[str, Any]]] | None = None


def read_providers_config() -> dict[str, dict[str, Any]]:
    """The ``providers`` sub-tree of config.json, or ``{}`` if absent/broken.

    Never raises: a missing or malformed config is a legal fresh-install
    state (→ empty registry). The result is shared between calls until the
    file changes, so callers read it and do not change it."""
    global _parsed
    path = get_config_path()
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size)
    cached = _parsed
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    providers = cfg.get("providers") if isinstance(cfg, dict) else None
    providers = providers if isinstance(providers, dict) else {}
    _parsed = (key, providers)
    return providers
