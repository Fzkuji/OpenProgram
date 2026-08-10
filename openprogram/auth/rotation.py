"""Per-provider rotation setting, and per-account membership in that rotation.

Normally a request uses the provider's ACTIVE account
(``auth.account_selection``). When rotation is ON, a request instead rotates
across the provider's accounts — a rate-limited account cools down and the next
takes over (see :func:`auth.usage.acquire_pooled`). Stored as a small JSON map
at ``~/.openprogram/auth/_rotation.json``
(``provider_id -> {"enabled": bool, "strategy": str}``), off by default.

A user may want to keep an account configured yet EXCLUDE it from the rotation
(a spare key, a throttled one, …). That's an independent per-account on/off, not
the single-active pin: several accounts can be in the rotation at once, and
taking one out doesn't touch the others. The excluded names live in
``~/.openprogram/auth/_rotation_excluded.json``
(``provider_id -> [account names]``); empty / missing ⇒ every account
participates. Only consulted on the rotation path.
"""
from __future__ import annotations

import json
import os
import threading

from .store import DEFAULT_ROOT

_LOCK = threading.RLock()

# Rotating strategies a user can pick (mirrors PoolStrategy minus "fixed").
STRATEGIES = ("fill_first", "round_robin", "random", "least_used")


def _path(name: str = "_rotation.json"):
    return DEFAULT_ROOT / "auth" / name


def _read(name: str = "_rotation.json") -> dict:
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict, name: str = "_rotation.json") -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_rotation(provider_id: str) -> dict:
    """``{"enabled": bool, "strategy": str}`` for ``provider_id``
    (defaults: off, ``fill_first``)."""
    with _LOCK:
        entry = _read().get(provider_id) or {}
    strat = entry.get("strategy")
    return {
        "enabled": bool(entry.get("enabled")),
        "strategy": strat if strat in STRATEGIES else "fill_first",
    }


def set_rotation(provider_id: str, *, enabled: bool, strategy: str = "") -> dict:
    """Turn rotation on/off for ``provider_id`` (and optionally set the
    strategy). Returns the new setting."""
    provider_id = (provider_id or "").strip()
    if not provider_id:
        return {"enabled": False, "strategy": "fill_first"}
    with _LOCK:
        data = _read()
        cur = data.get(provider_id) or {}
        strat = strategy if strategy in STRATEGIES else (cur.get("strategy") or "fill_first")
        if enabled:
            data[provider_id] = {"enabled": True, "strategy": strat}
        elif provider_id in data:
            # Keep the chosen strategy but mark disabled (so re-enabling restores it).
            data[provider_id] = {"enabled": False, "strategy": strat}
        _write(data)
    return {"enabled": bool(enabled), "strategy": strat}


# --- per-account membership in the rotation ---------------------------------

_EXCLUDED = "_rotation_excluded.json"


def _read_excluded() -> dict:
    data = _read(_EXCLUDED)
    if data or not _path("_disabled.json").exists():
        return data
    # One-time move of the old ``_disabled.json`` to the current filename.
    data = _read("_disabled.json")
    _write(data, _EXCLUDED)
    try:
        _path("_disabled.json").unlink()
    except OSError:
        pass
    return data


def get_accounts_out_of_rotation(provider_id: str) -> set:
    """The account names excluded from ``provider_id``'s rotation (empty ⇒ all
    participate)."""
    with _LOCK:
        v = _read_excluded().get(provider_id)
    return set(str(x) for x in v) if isinstance(v, list) else set()


def is_account_in_rotation(provider_id: str, account: str) -> bool:
    """Whether ``account`` participates in rotation (default True)."""
    return account not in get_accounts_out_of_rotation(provider_id)


def set_account_in_rotation(provider_id: str, account: str, in_rotation: bool) -> None:
    """Put ``account`` into / take it out of the provider's rotation."""
    provider_id = (provider_id or "").strip()
    account = (account or "").strip()
    if not provider_id or not account:
        return
    with _LOCK:
        data = _read_excluded()
        cur = [str(x) for x in data.get(provider_id, []) if x != account]
        if not in_rotation:
            cur.append(account)
        if cur:
            data[provider_id] = sorted(set(cur))
        else:
            data.pop(provider_id, None)
        _write(data, _EXCLUDED)


__all__ = [
    "STRATEGIES", "get_rotation", "set_rotation",
    "get_accounts_out_of_rotation", "is_account_in_rotation",
    "set_account_in_rotation",
]
