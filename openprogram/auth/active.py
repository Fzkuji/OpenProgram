"""Per-provider active account (profile) selection.

When a request resolves a credential for a provider WITHOUT an explicit profile,
it uses that provider's *active* profile — so a user can run "openai on the work
account, anthropic on personal" at the same time. Stored as a small JSON map at
``~/.openprogram/auth/_active.json`` (``provider_id -> profile_id``).

Falls back to the ambient :func:`auth.context.get_active_profile_id` (which
defaults to ``"default"``), so NOTHING changes until a user actually activates a
non-default account — the whole feature is opt-in and backward compatible.

This is the generic analogue of claude-code's ``meridian_profile`` selector;
claude-code keeps its own (Meridian-backed) selector, every other provider uses
this one.
"""
from __future__ import annotations

import json
import os
import threading

from .store import DEFAULT_ROOT

_LOCK = threading.RLock()


def _path():
    return DEFAULT_ROOT / "auth" / "_active.json"


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


def get_active_profile(provider_id: str) -> str:
    """Profile a request uses for ``provider_id`` when none is passed
    explicitly: the per-provider active pin if set, else the ambient scope
    (``"default"`` unless an ``auth_scope`` is entered)."""
    with _LOCK:
        pinned = _read().get(provider_id)
    if pinned:
        return pinned
    from .context import get_active_profile_id
    return get_active_profile_id()


def get_active_pin(provider_id: str) -> str:
    """The explicit pin for ``provider_id``, or ``""`` if none — without the
    ambient-scope fallback. Use this to show "which account is active" in UIs."""
    with _LOCK:
        return _read().get(provider_id, "") or ""


def set_active_profile(provider_id: str, profile_id: str) -> None:
    """Pin ``provider_id`` to ``profile_id``. An empty ``profile_id`` clears the
    pin (back to the ambient default)."""
    provider_id = (provider_id or "").strip()
    profile_id = (profile_id or "").strip()
    if not provider_id:
        return
    with _LOCK:
        data = _read()
        if profile_id:
            data[provider_id] = profile_id
        else:
            data.pop(provider_id, None)
        _write(data)


def all_active() -> dict:
    """Every explicit pin (provider_id -> profile_id)."""
    with _LOCK:
        return dict(_read())
