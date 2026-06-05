"""Per-provider rotation setting.

An account is a profile (one credential each). Normally a request uses the
provider's ACTIVE account (``auth.active``). When rotation is ON, a request
instead rotates across ALL the provider's accounts (profiles) — a rate-limited
account cools down and the next takes over (see :func:`auth.usage.acquire_pooled`).

Stored as a small JSON map at ``~/.openprogram/auth/_rotation.json``
(``provider_id -> {"enabled": bool, "strategy": str}``). Off by default, so
nothing changes until a user turns it on — fully backward compatible.
"""
from __future__ import annotations

import json
import os
import threading

from .store import DEFAULT_ROOT

_LOCK = threading.RLock()

# Rotating strategies a user can pick (mirrors PoolStrategy minus "fixed").
STRATEGIES = ("fill_first", "round_robin", "random", "least_used")


def _path():
    return DEFAULT_ROOT / "auth" / "_rotation.json"


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
