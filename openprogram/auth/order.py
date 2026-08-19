"""Per-provider account (profile) order.

Accounts are profiles. When rotation is on, requests try them in THIS order
(the user drags to set priority — who's used first, then next). Stored as a
small JSON map at ``~/.openprogram/auth/_order.json`` (``provider_id -> [profile
names]``), like ``_active.json``. Empty / missing ⇒ the default order
(``default`` first, then alphabetical), so nothing changes until a user reorders.
"""
from __future__ import annotations

import json
import os
import threading

from .store import DEFAULT_ROOT

_LOCK = threading.RLock()


def _path():
    return DEFAULT_ROOT / "auth" / "_order.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_order(provider_id: str) -> list:
    """The saved profile order for ``provider_id`` (``[]`` if none set)."""
    with _LOCK:
        v = _read().get(provider_id)
    return list(v) if isinstance(v, list) else []


def set_order(provider_id: str, order: list) -> None:
    provider_id = (provider_id or "").strip()
    if not provider_id:
        return
    with _LOCK:
        data = _read()
        data[provider_id] = [str(x) for x in (order or [])]
        _write(data)


def sort_key(provider_id: str):
    """A key fn that sorts profile names by the saved order (unknown names keep
    a stable position after the ordered ones, default-first then alphabetical)."""
    order = get_order(provider_id)
    index = {name: i for i, name in enumerate(order)}

    def key(profile: str):
        if profile in index:
            return (0, index[profile], "")
        return (1, 0 if profile == "default" else 1, profile)

    return key
