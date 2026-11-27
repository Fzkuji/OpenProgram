"""Which credential a request uses.

Every provider request goes through ``with_credential``
(``models/providers/streaming/credential_failover.py``; ``streaming/stream.py``
wraps every registered adapter in it). It asks :func:`choose` here for the
credential and records what a failure says about it with :func:`mark`,
:func:`block_model` and :func:`succeeded`.

**Which credential.** In order:

1. the credential this conversation is pinned to (the model menu's Key
   submenu). A pinned credential that cannot be used is not replaced: the
   request fails and says so;
2. the credential this conversation is bound to, while it can be used.
   Keeping a conversation on one key keeps the vendor's prompt cache warm
   and its bill on one account;
3. otherwise the provider's strategy picks among the usable ones —
   ``in_order`` (the first in the user's order), ``evenly`` (each in turn)
   or ``least_used`` — and the pick becomes the conversation's binding.

The binding and the pin live in the session's metadata
(``credential_binding`` / ``credential_pin``, ``{provider: credential id}``).
A sub-agent's calls and a Goal's turns use their parent conversation's.

Every change of a credential's state is saved and announced as
``provider.credentials.changed``.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .store import canonical_provider, get_store
from .types import (
    STRATEGIES,
    AuthConfigError,
    AuthEvent,
    AuthEventType,
    AuthPoolExhaustedError,
    Credential,
    CredentialPool,
)

_log = logging.getLogger(__name__)

QUOTA_HOLD_MS = 24 * 60 * 60_000
MODEL_BLOCK_MS = 24 * 60 * 60_000

BINDING_KEY = "credential_binding"
PIN_KEY = "credential_pin"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Usability
# ---------------------------------------------------------------------------

def back_at(cred: Credential, model: str = "", now_ms: Optional[int] = None) -> Optional[int]:
    """When ``cred`` can be used for ``model``: ``0`` now, a time in ms, or
    ``None`` when only a person can bring it back (off, invalid, signed out)."""
    now = _now_ms() if now_ms is None else now_ms
    if not cred.enabled or cred.status in ("invalid_key", "needs_login"):
        return None
    until = cred.cooldown_until_ms if cred.status in ("cooling", "quota") else 0
    blocked = int(cred.blocked_models.get(model) or 0) if model else 0
    later = max(until, blocked)
    return later if later > now else 0


def usable(cred: Credential, model: str = "", now_ms: Optional[int] = None) -> bool:
    return back_at(cred, model, now_ms) == 0


def state_of(cred: Credential, now_ms: Optional[int] = None) -> str:
    """The status as it reads now: a cooldown that has passed reads ``ready``."""
    now = _now_ms() if now_ms is None else now_ms
    if cred.status in ("cooling", "quota") and cred.cooldown_until_ms <= now:
        return "ready"
    return cred.status


# ---------------------------------------------------------------------------
# Conversation binding and pin
# ---------------------------------------------------------------------------

_bindings: dict[tuple[str, str], str] = {}
_bindings_lock = threading.Lock()


def conversation_of(session_id: Optional[str]) -> str:
    """The conversation whose pin and binding a call uses.

    The call's own context names it before ``session_id`` does: a call made
    through a ``Runtime`` (a program, an agent method, a sub-agent) carries
    a provider cache key such as ``op-<hash>`` as its ``session_id``, which
    names no conversation. In order: the context's parent conversation, its
    Goal conversation, its session, then ``session_id``; whichever it is,
    a session whose metadata names a parent (a sub-agent's child session)
    is followed up to the conversation at the root."""
    found = ""
    with contextlib.suppress(Exception):
        from .context import call_conversation
        found = call_conversation()
    found = found or session_id or ""
    if not found:
        from .context import get_affinity_key
        found = get_affinity_key() or ""
    return _root_conversation(found)


_roots: dict[str, str] = {}


def _root_conversation(session_id: str) -> str:
    """``session_id``'s top conversation, through the sessions' ``parent_session_id``."""
    if not session_id:
        return ""
    with _bindings_lock:
        held = _roots.get(session_id)
    if held is not None:
        return held
    current, seen = session_id, {session_id}
    for _ in range(16):
        parent = str((_session_meta(current) or {}).get("parent_session_id") or "")
        if not parent or parent in seen:
            break
        seen.add(parent)
        current = parent
    with _bindings_lock:
        _roots[session_id] = current
    return current


def _session_meta(session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    try:
        from openprogram.state.store.session import default_store
        return default_store().read_disk_meta(session_id)
    except Exception:
        _log.debug("session %s metadata not read", session_id, exc_info=True)
        return None


def _write_session_map(session_id: str, key: str, provider: str, value: Optional[str]) -> bool:
    """Set (or with ``None`` drop) ``provider`` in the session's ``key`` map,
    under the session's write lock. ``False`` when there is no such session."""
    if _session_meta(session_id) is None:
        return False

    def _set(current: dict) -> dict:
        out = dict(current or {})
        if value:
            out[provider] = value
        else:
            out.pop(provider, None)
        return out

    try:
        from openprogram.state.store.session import default_store
        default_store().update_session_dict(session_id, key, _set)
    except Exception:
        _log.warning("session %s: %s not saved", session_id, key, exc_info=True)
        return False
    return True


def pinned(session_id: str, provider: str) -> str:
    meta = _session_meta(session_id) or {}
    return str((meta.get(PIN_KEY) or {}).get(canonical_provider(provider)) or "")


def bound(session_id: str, provider: str) -> str:
    provider = canonical_provider(provider)
    with _bindings_lock:
        held = _bindings.get((session_id, provider))
    if held is not None:
        return held
    meta = _session_meta(session_id) or {}
    found = str((meta.get(BINDING_KEY) or {}).get(provider) or "")
    with _bindings_lock:
        _bindings[(session_id, provider)] = found
    return found


def bind(session_id: str, provider: str, credential_id: str) -> None:
    if not session_id:
        return
    provider = canonical_provider(provider)
    with _bindings_lock:
        if _bindings.get((session_id, provider)) == credential_id:
            return
        _bindings[(session_id, provider)] = credential_id
    _write_session_map(session_id, BINDING_KEY, provider, credential_id)


def set_pin(session_id: str, provider: str, credential_id: Optional[str]) -> bool:
    """Pin the conversation to ``credential_id`` (``None``: back to automatic).
    A pin also becomes the binding, so unpinning stays on the same key."""
    provider = canonical_provider(provider)
    if credential_id and get_store().find(provider, credential_id) is None:
        raise AuthConfigError(
            f"{provider} has no key {credential_id}; set this conversation back to Auto "
            "(credential_id: null) or pick another key", provider_id=provider)
    if not _write_session_map(session_id, PIN_KEY, provider, credential_id):
        return False
    if credential_id:
        bind(session_id, provider, credential_id)
    return True


def session_credentials(session_id: str) -> dict:
    """``{"binding": {provider: id}, "pin": {provider: id}}`` of a session."""
    meta = _session_meta(session_id) or {}
    return {"binding": dict(meta.get(BINDING_KEY) or {}), "pin": dict(meta.get(PIN_KEY) or {})}


def forget_bindings() -> None:
    """Drop the in-process binding cache (tests, a provider's list cleared)."""
    with _bindings_lock:
        _bindings.clear()
        _roots.clear()


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------

class NoUsableCredential(AuthPoolExhaustedError):
    """No credential of the provider can be used for this request now."""

    def __init__(self, message: str, *, provider_id: str, rows: list[dict],
                 pinned_id: str = "", wait_ms: Optional[int] = None, wait_label: str = "") -> None:
        super().__init__(message, provider_id=provider_id)
        self.rows = rows
        self.pinned_id = pinned_id
        self.wait_ms = wait_ms
        self.wait_label = wait_label


def _row_state(cred: Credential, model: str, now: int) -> dict:
    when = back_at(cred, model, now)
    state = "off" if not cred.enabled else state_of(cred, now)
    if when and state == "ready":
        state = "model_blocked"
    return {"id": cred.id, "label": cred.display_label(), "state": state,
            "back_at": (when / 1000) if when else None,
            "error": (cred.last_error or {}).get("message")}


_SAID = {
    "cooling": "rate limited",
    "quota": "out of quota",
    "invalid_key": "key refused",
    "needs_login": "sign in again",
    "model_blocked": "cannot use this model",
    "off": "turned off",
    "failed": "failed just now",
}


def _said(state: str) -> str:
    return _SAID.get(state, state)


def _unavailable(provider: str, pool: CredentialPool, model: str, *, pinned_id: str = "",
                 exclude: frozenset = frozenset()) -> NoUsableCredential:
    now = _now_ms()
    rows = [_row_state(c, model, now) for c in pool.credentials]
    for row in rows:
        if row["id"] in exclude and row["state"] == "ready":
            row["state"] = "failed"
    # Every credential back at a known time counts, the one that just
    # failed and was cooled included: with one key, a 429 waits out its
    # cooldown instead of failing the turn. One that failed without a
    # state has no time (``back_at`` 0) and is not waited for.
    waits = [(back_at(c, model, now), c) for c in pool.credentials if back_at(c, model, now)]
    wait_ms, wait_label = None, ""
    if waits and not pinned_id:
        soonest, cred = min(waits, key=lambda w: w[0])
        wait_ms, wait_label = soonest - now, cred.display_label()
    if pinned_id:
        pin = next((r for r in rows if r["id"] == pinned_id), None)
        head = f"The {provider} key this conversation is pinned to ({pin['label'] if pin else pinned_id}) " \
               f"cannot be used now ({_said(pin['state']) if pin else 'removed'}). Set the key back to Auto, or pick another."
    else:
        head = f"No {provider} key can be used now."
    states = "; ".join(f"{r['label']}: {_said(r['state'])}" + (f" until {time.strftime('%H:%M', time.localtime(r['back_at']))}"
                       if r["back_at"] else "") for r in rows)
    message = f"{head} {states}." if states else f"{head} Add a key in Settings → Model Services."
    return NoUsableCredential(message, provider_id=provider, rows=rows, pinned_id=pinned_id,
                              wait_ms=wait_ms, wait_label=wait_label)


def choose(provider: str, model: str = "", *, session_id: str = "", record: bool = True,
           exclude: frozenset = frozenset()) -> Credential:
    """The credential the next request of ``provider`` uses (module docstring).

    ``record`` counts the use, advances ``evenly`` and binds the conversation.
    Raises :class:`AuthConfigError` when the provider has no credential and
    :class:`NoUsableCredential` when none can be used."""
    store = get_store()
    provider = canonical_provider(provider)
    pool = store.get(provider)
    if not pool.credentials:
        raise AuthConfigError(f"no credentials for {provider}", provider_id=provider)
    now = _now_ms()
    if session_id:
        pin = pinned(session_id, provider)
        if pin:
            cred = pool.find(pin)
            if cred is None or not usable(cred, model, now) or pin in exclude:
                raise _unavailable(provider, pool, model, pinned_id=pin, exclude=exclude)
            return _record(store, pool, cred, session_id, now) if record else cred
        held = bound(session_id, provider)
        cred = pool.find(held) if held else None
        if cred is not None and held not in exclude and usable(cred, model, now):
            return _record(store, pool, cred, session_id, now) if record else cred
    candidates = [c for c in pool.credentials if c.id not in exclude and usable(c, model, now)]
    cursor: Optional[int] = None
    if not candidates:
        raise _unavailable(provider, pool, model, exclude=exclude)
    if pool.strategy == "evenly":
        order = [c.id for c in pool.credentials]
        start = pool.cursor % len(order)
        rotated = order[start:] + order[:start]
        cred = next(c for cid in rotated for c in candidates if c.id == cid)
        cursor = (order.index(cred.id) + 1) % len(order)
    elif pool.strategy == "least_used":
        # Today's calls, so a key added later does not take every new
        # conversation until its lifetime count catches up.
        cred = min(candidates, key=lambda c: (c.uses_today, c.last_used_at_ms))
    else:
        cred = candidates[0]
    return _record(store, pool, cred, session_id, now, cursor) if record else cred


def _record(store, pool: CredentialPool, cred: Credential, session_id: str, now: int,
            cursor: Optional[int] = None) -> Credential:
    """Count the use in the provider's usage file (never the secrets file),
    move ``evenly``'s turn and bind the conversation."""
    try:
        store.record_use(pool.provider_id, cred.id, now, cursor=cursor)
    except Exception:
        _log.debug("use of %s not recorded", cred.id, exc_info=True)
    cred.use_count += 1
    cred.uses_today += 1
    cred.last_used_at_ms = now
    bind(session_id, pool.provider_id, cred.id)
    return cred


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def _changed(provider: str, credential_id: str, **detail) -> None:
    store = get_store()
    store.emit(AuthEvent(type=AuthEventType.POOL_MEMBER_COOLDOWN, provider_id=provider,
                         credential_id=credential_id, detail=detail))
    store.changed(provider, credential=credential_id)


_URL_SECRET = re.compile(r"(?i)([?&](?:key|api_key|apikey|access_token|token|sig)=)[^&\s\"'<>]+")


def _scrubbed(row: Credential, message: str) -> str:
    """``message`` as it may be stored and shown: a provider or proxy that
    echoes the key (in the body, a ``Bearer`` header or a ``?key=`` URL)
    must not put it in a listing, an error card or the transcript."""
    from openprogram.state.secret_removal import remove_secrets
    text = str(message or "")
    value = row.payload.auth_value
    if row.source == "env":
        import os
        value = os.environ.get(row.metadata.get("env_var") or "", "")
    value = (value or "").strip()
    if len(value) >= 8:
        text = text.replace(value, "····" + value[-4:])
    text = _URL_SECRET.sub(lambda m: m.group(1) + "[secret removed]", text)
    return remove_secrets(text)[:300]


def mark(provider: str, credential_id: str, status: str, *, until_ms: int = 0,
         code: str = "", message: str = "") -> Optional[Credential]:
    """Set a credential's status (and how long it lasts) and announce it.
    ``message`` is stored with every secret in it removed."""
    now = _now_ms()

    def _set(row: Credential) -> None:
        row.status = status
        row.cooldown_until_ms = until_ms if status in ("cooling", "quota") else 0
        row.last_error = {"code": code or status, "message": _scrubbed(row, message), "at": now}
        row.updated_at_ms = now

    cred = get_store().update(provider, credential_id, _set)
    if cred is not None:
        _changed(canonical_provider(provider), credential_id, status=status, until_ms=until_ms)
    return cred


def block_model(provider: str, credential_id: str, model: str, *, message: str = "") -> None:
    now = _now_ms()

    def _set(row: Credential) -> None:
        row.blocked_models[model] = now + MODEL_BLOCK_MS
        row.last_error = {"code": "model_not_allowed", "message": _scrubbed(row, message), "at": now}
        row.updated_at_ms = now

    if get_store().update(provider, credential_id, _set) is not None:
        _changed(canonical_provider(provider), credential_id, blocked_model=model)


def succeeded(provider: str, credential_id: str) -> None:
    """A reply arrived: a cleared state is saved; repeat strikes start over."""
    cred = get_store().find(provider, credential_id)
    if cred is None or (cred.strikes == 0 and cred.status == "ready" and not cred.last_error):
        return

    def _clear(row: Credential) -> None:
        row.strikes = 0
        row.last_error = None
        if state_of(row) == "ready":
            row.status = "ready"
            row.cooldown_until_ms = 0

    get_store().update(provider, credential_id, _clear)
    get_store().changed(provider, credential=credential_id)


def clear(provider: str, credential_id: str) -> Optional[Credential]:
    """Make a credential usable again (a passing check, a replaced key)."""

    def _clear(row: Credential) -> None:
        row.status = "ready"
        row.cooldown_until_ms = 0
        row.blocked_models = {}
        row.last_error = None
        row.strikes = 0
        row.updated_at_ms = _now_ms()

    cred = get_store().update(provider, credential_id, _clear)
    if cred is not None:
        get_store().changed(provider, credential=credential_id)
    return cred


# ---------------------------------------------------------------------------
# The credential a request is sent with
# ---------------------------------------------------------------------------

@dataclass
class Lease:
    """The credential one request is sent with, as the adapter reads it from
    ``opts.credential``."""

    provider_id: str
    credential_id: str
    label: str
    kind: str
    auth_value: str
    base_url: Optional[str]
    headers: dict


def lease(cred: Credential) -> Lease:
    """``cred`` as one request sends it."""
    from .resolver import resolve_connection
    conn = resolve_connection(cred)
    if conn is None:
        raise AuthConfigError(f"the {cred.provider_id} credential {cred.display_label()} has no usable "
                              "secret", provider_id=cred.provider_id)
    return Lease(cred.provider_id, cred.id, cred.display_label(), conn.kind, conn.auth_value,
                 conn.base_url, dict(conn.headers or {}))


__all__ = [
    "BINDING_KEY", "Lease", "NoUsableCredential", "PIN_KEY", "STRATEGIES",
    "back_at", "bind", "block_model", "bound", "choose", "clear",
    "conversation_of", "forget_bindings", "lease", "mark", "pinned", "session_credentials",
    "set_pin", "state_of", "succeeded", "usable",
]
