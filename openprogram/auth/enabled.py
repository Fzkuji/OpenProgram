"""Per-provider, per-account ENABLED state for rotation.

Accounts are profiles. When rotation is on, requests rotate across the
provider's accounts — but a user may want to keep an account configured yet
*exclude* it from the rotation (a spare key, a throttled one, …). That's an
INDEPENDENT per-account on/off, NOT the single-active pin (``active.py``):
several accounts can be on at once, and turning one off doesn't touch the
others.

We store only the EXCLUDED (disabled) profiles per provider — a small JSON map
at ``~/.openprogram/auth/_disabled.json`` (``provider_id -> [profile names]``),
like ``_order.json`` / ``_active.json``. Empty / missing ⇒ every account is
enabled, so nothing changes until a user turns one off.

Only consulted on the rotation path (``usage.acquire_pooled``); rotation-off
single-active selection is unaffected.
"""
from __future__ import annotations

import json
import os
import threading

from .store import DEFAULT_ROOT

_LOCK = threading.RLock()


def _path():
    return DEFAULT_ROOT / "auth" / "_disabled.json"


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


def get_disabled(provider_id: str) -> set:
    """The set of profile names excluded from rotation for ``provider_id``
    (empty ⇒ all accounts participate)."""
    with _LOCK:
        v = _read().get(provider_id)
    return set(str(x) for x in v) if isinstance(v, list) else set()


def is_enabled(provider_id: str, profile: str) -> bool:
    """Whether ``profile`` participates in rotation (default True)."""
    return profile not in get_disabled(provider_id)


def set_enabled(provider_id: str, profile: str, enabled: bool) -> None:
    """Add / remove ``profile`` from the provider's excluded set."""
    provider_id = (provider_id or "").strip()
    profile = (profile or "").strip()
    if not provider_id or not profile:
        return
    with _LOCK:
        data = _read()
        cur = [str(x) for x in data.get(provider_id, []) if isinstance(provider_id, str)]
        cur = [x for x in cur if x != profile]  # drop any existing entry
        if not enabled:
            cur.append(profile)                  # excluded
        if cur:
            data[provider_id] = sorted(set(cur))
        else:
            data.pop(provider_id, None)
        _write(data)


__all__ = ["get_disabled", "is_enabled", "set_enabled"]
