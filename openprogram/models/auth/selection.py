"""Which credential a request uses, and what a failure does to it.

Every provider request goes through :func:`with_credential`
(``models/providers/streaming/stream.py`` wraps every registered adapter in
it), so one place decides, for every adapter:

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

**What a failure does** (before the reply has shown anything the same
request goes out again on the next credential; after, the failure is
raised and the next turn chooses again):

==========================  ===================================================
429                         ``Retry-After`` of 5 s or less: once more on the same
                            key. Otherwise it cools down until the provider's
                            reset, else 60 s doubling per repeat up to 15 min,
                            and the next one is tried
402 / spent quota           cools down until the quota reader's reset, else
                            24 h (a passing check clears it); next one
401 / 403                   a sign-in is refreshed once and retried; a refused
                            refresh marks it ``needs_login``, a key
                            ``invalid_key``; next one
model not allowed           only that model is blocked on that key for 24 h
5xx                         the key is left alone; two more tries with backoff
network                     left to the stream's re-send (``streaming/resend.py``)
none usable                 the earliest back within 60 s is waited for, with a
                            notice; else the error lists every credential's
                            state (model fallback, when configured, runs on it)
==========================  ===================================================

A switch adds one muted line to the conversation (a ``retry`` event with
reason ``credential``), never a dialog. Every change of a credential's state
is saved and announced as ``provider.credentials.changed``.

The SDKs' own retries are off (``max_retries=0``) so this layer sees every
failure once.
"""
from __future__ import annotations

import contextlib

import asyncio
import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

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

COOLDOWN_BASE_MS = 60_000
COOLDOWN_MAX_MS = 15 * 60_000
QUOTA_HOLD_MS = 24 * 60 * 60_000
MODEL_BLOCK_MS = 24 * 60 * 60_000
SHORT_RETRY_AFTER_S = 5.0
WAIT_FOR_RECOVERY_MS = 60_000
SERVER_RETRIES = 2

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
        from openprogram.models.usage.context import current_usage_context
        ctx = current_usage_context()
        found = ctx.parent_session_id or ctx.goal_session_id or ctx.session_id or ""
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
    from openprogram.models.providers.utils.redaction import remove_secrets
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
# Classifying a failure
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    #: rate_limit | quota | auth | model | server | network | other
    kind: str
    status: Optional[int] = None
    retry_after_s: Optional[float] = None
    resets_at: Optional[float] = None
    text: str = ""


_MODEL_MARKERS = ("does not have access to model", "not have access to the model",
                  "do not have access to model", "does not exist or you do not have access",
                  "model_not_found", "model not found", "model is not available",
                  "model not available", "not supported for this model",
                  "is not allowed to use model", "no access to model", "unsupported model",
                  "must be verified", "organization", "not available in your")
# A 403 that names the credential itself (anything else a 403 says is about
# the model, the region or the request, and is not the key's fault).
_KEY_REFUSED_MARKERS = ("invalid api key", "incorrect api key", "invalid_api_key", "api key not valid",
                        "api key is invalid", "api key invalid", "invalid x-api-key", "revoked",
                        "expired", "authentication_error", "permission_denied", "invalid token",
                        "invalid_token", "invalid authentication", "unauthenticated",
                        "account has been disabled", "account is disabled", "deactivated",
                        "key is disabled", "no such key", "permission denied")


def classify(exc: BaseException) -> Outcome:
    """What a failed request says about the credential that sent it."""
    from openprogram.models.providers.utils.failures.errors import (
        ErrorReason, LLMError, _PERMANENT_MARKERS_QUOTA, _chain, classify_error,
        is_billing_exhausted, parse_retry_after, web_page_text,
    )
    chain = _chain(exc)
    status = next((s for e in chain for s in (getattr(e, "http_status", None),
                                              getattr(e, "status_code", None),
                                              getattr(e, "code", None))
                   if isinstance(s, int) and 100 <= s < 600), None)
    text = " ".join(str(e) + " " + str(getattr(e, "error_text", "") or "") for e in chain)
    low = text.lower()
    retry_after = next((float(getattr(e, "retry_after_s")) for e in chain
                        if isinstance(getattr(e, "retry_after_s", None), (int, float))), None)
    if retry_after is None:
        for e in chain:
            headers = getattr(getattr(e, "response", None), "headers", None)
            retry_after = parse_retry_after(headers) if headers is not None else None
            if retry_after is not None:
                break
    if any(getattr(e, "web_page", False) for e in chain) or (
            status == 403 and web_page_text(text) is not None):
        return Outcome("other", status, text=text)
    if status == 402 or is_billing_exhausted(low) or any(m in low for m in _PERMANENT_MARKERS_QUOTA):
        from openprogram.models.providers.utils.failures.errors import _body_reset
        resets_at, _plan = _body_reset(text, time.time())
        if resets_at is None and retry_after:
            resets_at = time.time() + retry_after
        return Outcome("quota", status, retry_after, resets_at, text)
    if status == 429:
        return Outcome("rate_limit", status, retry_after, text=text)
    if status in (403, 404) and "model" in low and any(m in low for m in _MODEL_MARKERS):
        return Outcome("model", status, text=text)
    if status == 401 or (status == 403 and any(m in low for m in _KEY_REFUSED_MARKERS)):
        return Outcome("auth", status, text=text)
    if status == 403:
        # A policy, region or verification refusal: this request fails, the
        # key stays usable for every other model and conversation.
        return Outcome("other", status, text=text)
    if any(type(e).__name__ == "AuthNeedsReauthError" for e in chain):
        return Outcome("auth", status, text=text)
    if status is not None and 500 <= status < 600:
        return Outcome("server", status, retry_after, text=text)
    if status is None:
        if isinstance(exc, LLMError):
            reason = exc.reason
        else:
            reason, _ = classify_error(exc, error_text=getattr(exc, "error_text", "") or "")
        if reason == ErrorReason.RATE_LIMIT and "overloaded" not in low:
            return Outcome("rate_limit", None, retry_after, text=text)
        if reason == ErrorReason.AUTHENTICATION:
            return Outcome("auth", None, text=text)
        if reason == ErrorReason.PROVIDER_INTERNAL:
            return Outcome("server", None, retry_after, text=text)
        if reason == ErrorReason.TRANSPORT:
            return Outcome("network", None, text=text)
    return Outcome("other", status, text=text)


def _quota_until(provider: str, cred: Credential, outcome: Outcome) -> int:
    if outcome.resets_at:
        return int(outcome.resets_at * 1000)
    with contextlib.suppress(Exception):
        from openprogram.models.usage.quota import exhausted_limit
        limit = exhausted_limit(provider, cred.id)
        if limit is not None and limit.resets_at:
            return int(limit.resets_at * 1000)
    return _now_ms() + QUOTA_HOLD_MS


_REASON_TEXT = {
    "rate_limit": "hit a rate limit", "quota": "used up its quota",
    "auth": "was refused", "model": "cannot use this model",
}


# ---------------------------------------------------------------------------
# The one entry every adapter goes through
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


def _lease(cred: Credential) -> Lease:
    from .resolver import resolve_connection
    conn = resolve_connection(cred)
    if conn is None:
        raise AuthConfigError(f"the {cred.provider_id} credential {cred.display_label()} has no usable "
                              "secret", provider_id=cred.provider_id)
    return Lease(cred.provider_id, cred.id, cred.display_label(), conn.kind, conn.auth_value,
                 conn.base_url, dict(conn.headers or {}))


def _kind_of(event: Any) -> Optional[str]:
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", None)


def _final_of(event: Any):
    kind = _kind_of(event)
    if kind == "done":
        return event.get("message") if isinstance(event, dict) else getattr(event, "message", None)
    if kind == "error":
        return event.get("error") if isinstance(event, dict) else getattr(event, "error", None)
    return None


def _governed() -> bool:
    try:
        from openprogram.models.providers.streaming.budget import provider_retry_attempts
        return provider_retry_attempts(2) == 1
    except Exception:
        return False


def _notice(attempt: int, reason: str, text: str, delay_ms: int = 0, credential: Optional[dict] = None):
    from openprogram.models.providers.types import EventRetry
    return EventRetry(attempt=attempt, reason=reason, error=text, delay_ms=delay_ms,
                      credential=credential)


def with_credential(model: Any, opts: Any,
                    attempt: Callable[[Any], AsyncIterator[Any]]) -> AsyncIterator[Any]:
    """Send one request through the provider's credentials (module docstring).

    ``attempt(opts)`` starts the adapter with ``opts.api_key`` and
    ``opts.credential`` (a :class:`Lease`) set to the chosen credential.
    The first credential is chosen before this returns, so a provider with
    nothing usable fails here, before any request; making it ready to send
    (a refresh may wait on another process's lock and go over the network)
    happens off the event loop once the caller iterates. A provider with no credential at all (a local server, a cloud
    credential chain) is passed through unchanged."""
    provider = canonical_provider(getattr(model, "provider", "") or "")
    model_id = getattr(model, "id", "") or ""
    store = get_store()
    if getattr(opts, "credential", None) is not None or not store.get(provider).credentials:
        return attempt(opts)
    run = _Run(provider, model, model_id, opts, attempt)
    return run.events()


class _Run:
    """One request through a provider's credentials."""

    def __init__(self, provider: str, model: Any, model_id: str, opts: Any, attempt) -> None:
        self.provider = provider
        self.model = model
        self.model_id = model_id
        self.opts = opts
        self.attempt = attempt
        self.session = conversation_of(getattr(opts, "session_id", None))
        # A request moved here along its keys uses those keys and no other.
        only = getattr(opts, "only_credentials", None)
        self.outside: frozenset = frozenset(
            c.id for c in get_store().get(provider).credentials if c.id not in only) if only else frozenset()
        self.tried: set[str] = set(self.outside)
        self.notices = 0
        self.waited = False
        self.started = False
        # Chosen now, so a provider with nothing usable fails before the
        # caller iterates; preparing it and a short wait are left to ``events``.
        self.pending: Optional[NoUsableCredential] = None
        self.first: Optional[Credential] = None
        self.cred = self.lease = self.inner = None
        try:
            self.first = choose(provider, model_id, session_id=self.session, exclude=self.outside)
        except NoUsableCredential as none:
            if none.wait_ms is None or none.wait_ms > WAIT_FOR_RECOVERY_MS:
                raise _llm_error(provider, model, none) from none
            self.pending = none

    def _choose(self, first: Optional[Credential] = None) -> Credential:
        """The next credential, ready to send (``first`` when given, already
        chosen). One whose sign-in cannot be refreshed now is marked (when
        the refusal says so) and passed over."""
        from .credential_provider import get_credential_provider
        from .types import AuthError
        while True:
            cred = first or choose(self.provider, self.model_id, session_id=self.session,
                                   exclude=frozenset(self.tried))
            first = None
            try:
                return get_credential_provider().prepare(cred)
            except AuthError as err:
                if type(err).__name__ in ("AuthNeedsReauthError", "AuthReadOnlyError"):
                    mark(self.provider, cred.id, "needs_login", message=str(err))
                self.tried.add(cred.id)

    def _start(self, cred: Credential):
        lease = _lease(cred)
        opts = self.opts.model_copy(update={"api_key": lease.auth_value, "credential": lease})
        return lease, self.attempt(opts)

    def _show(self, notice):
        """The events that put ``notice`` on the reply (a ``start`` first
        when nothing has started it)."""
        from openprogram.models.providers.types import AssistantMessage, EventStart
        out = []
        if not self.started:
            self.started = True
            out.append(EventStart(partial=AssistantMessage.empty(self.model)))
        out.append(notice)
        return out

    async def _wait_for(self, none: NoUsableCredential):
        """Wait, with a notice, for the credential back soonest."""
        self.waited = True
        seconds = max(1, round(none.wait_ms / 1000))
        self.notices += 1
        for event in self._show(_notice(self.notices, "credential_wait",
                                        f"Waiting for {none.wait_label}, back in {seconds} s",
                                        int(none.wait_ms), {"to": none.wait_label})):
            yield event
        # A little past the time, so the clock has surely passed it.
        await asyncio.sleep((none.wait_ms + 50) / 1000)
        self.tried = set(self.outside)

    async def _pick_after(self, failed: Optional[Credential], outcome: Optional[Outcome],
                          first: Optional[Credential] = None):
        """Yield notices, then ``("cred", credential)`` for the next one to
        try; waits once for one back within a minute, else raises the error
        that lists every credential's state."""
        if failed is not None:
            self.tried.add(failed.id)
        while True:
            try:
                cred = await asyncio.to_thread(self._choose, first)
                break
            except NoUsableCredential as none:
                if none.wait_ms is not None and none.wait_ms <= WAIT_FOR_RECOVERY_MS and not self.waited:
                    async for event in self._wait_for(none):
                        yield event
                    first = None
                    continue
                raise _llm_error(self.provider, self.model, none) from none
        if failed is not None and outcome is not None and cred.id != failed.id:
            self.notices += 1
            for event in self._show(_notice(
                    self.notices, "credential",
                    f"Switched to {cred.display_label()}: {failed.display_label()} "
                    f"{_REASON_TEXT.get(outcome.kind, 'failed')}",
                    credential={"to": cred.display_label(), "from": failed.display_label(),
                                "cause": outcome.kind})):
                yield event
        yield ("cred", cred)

    async def _switch(self, failed: Optional[Credential], outcome: Optional[Outcome],
                      first: Optional[Credential] = None):
        chosen = None
        async for item in self._pick_after(failed, outcome, first):
            if isinstance(item, tuple) and item and item[0] == "cred":
                chosen = item[1]
            else:
                yield item
        self.cred = chosen
        self.lease, self.inner = self._start(chosen)

    async def events(self):
        if self.pending is not None:
            none, self.pending = self.pending, None
            async for event in self._wait_for(none):
                yield event
            async for event in self._switch(None, None):
                yield event
        elif self.first is not None:
            first, self.first = self.first, None
            async for event in self._switch(None, None, first):
                yield event
        # The two second chances on one credential are counted apart, so a
        # short-429 retry does not use up the refresh a 401 after it gets.
        rate_retry_used = refresh_used = False
        server_tries = 0
        governed = _governed()
        while True:
            cred, lease, inner = self.cred, self.lease, self.inner
            shown = False
            try:
                async for event in inner:
                    kind = _kind_of(event)
                    if kind == "start":
                        if self.started:
                            continue
                        self.started = True
                    elif kind != "retry":
                        shown = True
                    final = _final_of(event)
                    if final is not None:
                        try:
                            final.credential_id = lease.credential_id
                        except (AttributeError, ValueError):
                            pass
                        if kind == "done":
                            await asyncio.to_thread(succeeded, self.provider, cred.id)
                    yield event
                return
            except Exception as exc:
                if _cancelled(self.opts):
                    raise
                outcome = classify(exc)
                action = await asyncio.to_thread(self._settle, cred, outcome,
                                                 rate_retry_used, refresh_used)
                # After the reply has shown something the failure is the
                # caller's; the state recorded above makes the next turn switch.
                if action == "raise" or shown:
                    raise
                if action == "same":
                    rate_retry_used = True
                    await asyncio.sleep(outcome.retry_after_s or 0)
                    self.lease, self.inner = self._start(cred)
                elif action == "refreshed":
                    refresh_used = True
                    from .credential_provider import get_credential_provider
                    fresh = get_store().find(self.provider, cred.id) or cred
                    self.cred = await asyncio.to_thread(get_credential_provider().prepare, fresh)
                    self.lease, self.inner = self._start(self.cred)
                elif action == "server":
                    if governed or server_tries >= SERVER_RETRIES:
                        raise
                    server_tries += 1
                    await asyncio.sleep(min(8.0, (2 ** server_tries) * 0.5 + random.random()))
                    self.lease, self.inner = self._start(cred)
                else:  # next
                    rate_retry_used = refresh_used = False
                    server_tries = 0
                    try:
                        async for event in self._switch(cred, outcome):
                            yield event
                    except Exception as err:
                        raise err from exc

    def _settle(self, cred: Credential, outcome: Outcome, rate_retry_used: bool = False,
                refresh_used: bool = False) -> str:
        """Record what ``outcome`` says about ``cred`` and name what comes next:
        ``same`` / ``refreshed`` (send again on it), ``server`` (back off and
        send again), ``next`` (another credential), ``raise``."""
        now = _now_ms()
        provider = self.provider
        if outcome.kind == "rate_limit":
            if outcome.retry_after_s is not None and outcome.retry_after_s <= SHORT_RETRY_AFTER_S \
                    and not rate_retry_used:
                return "same"
            stored = get_store().find(provider, cred.id) or cred
            if outcome.retry_after_s:
                until = now + int(outcome.retry_after_s * 1000)
            else:
                until = now + min(COOLDOWN_MAX_MS, COOLDOWN_BASE_MS * (2 ** min(stored.strikes, 8)))

            def _strike(row: Credential) -> None:
                row.strikes += 1
            get_store().update(provider, cred.id, _strike)
            mark(provider, cred.id, "cooling", until_ms=until, code="rate_limit",
                 message=outcome.text)
            return "next"
        if outcome.kind == "quota":
            mark(provider, cred.id, "quota", until_ms=_quota_until(provider, cred, outcome),
                 code="quota", message=outcome.text)
            return "next"
        if outcome.kind == "auth":
            if cred.kind in ("oauth", "device_code") and not cred.read_only and not refresh_used:
                try:
                    from .credential_provider import get_credential_provider
                    refreshed = get_credential_provider().force_refresh_sync(cred)
                    if refreshed.payload.auth_value and refreshed.payload.auth_value != cred.payload.auth_value:
                        return "refreshed"
                except Exception:
                    _log.info("%s: refresh after a refusal failed", provider, exc_info=True)
            status = "invalid_key" if cred.kind in ("api_key", "credential_process") else "needs_login"
            mark(provider, cred.id, status, code=status, message=outcome.text)
            return "next"
        if outcome.kind == "model" and self.model_id:
            block_model(provider, cred.id, self.model_id, message=outcome.text)
            return "next"
        if outcome.kind == "server":
            return "server"
        return "raise"


def _cancelled(opts: Any) -> bool:
    signal = getattr(opts, "signal", None)
    return bool(signal is not None and callable(getattr(signal, "is_set", None)) and signal.is_set())


_WAITING = ("cooling", "quota", "model_blocked")


def _llm_error(provider: str, model: Any, none: NoUsableCredential):
    """The error a request ends with when no credential can be used."""
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    if none.pinned_id:
        # The pinned row decides: a wait is a limit, a refusal is the key's,
        # and a key turned off or removed is a setting to change, not a
        # failed sign-in.
        pin = next((r for r in none.rows if r["id"] == none.pinned_id), None)
        if pin is None or pin["state"] == "off":
            reason = ErrorReason.INVALID_REQUEST
        elif pin["state"] in _WAITING:
            reason = ErrorReason.RATE_LIMIT
        else:
            reason = ErrorReason.AUTHENTICATION
    else:
        waiting = any(r["state"] in _WAITING + ("failed",) for r in none.rows)
        reason = ErrorReason.RATE_LIMIT if waiting else ErrorReason.AUTHENTICATION
    err = LLMError(message=str(none), reason=reason, retryable=False, provider=provider,
                   model=getattr(model, "id", None))
    with contextlib.suppress(Exception):
        err.failure = {"cause": "credentials_unavailable", "http_status": None, "provider": provider,
                       "limit": None, "resets_at": None, "account": None, "other_limits": [],
                       "credentials": none.rows, "pinned": none.pinned_id or None}
    return err


def unavailable_error(provider: str, model: Any, none: NoUsableCredential):
    return _llm_error(provider, model, none)


__all__ = [
    "BINDING_KEY", "Lease", "NoUsableCredential", "Outcome", "PIN_KEY", "STRATEGIES",
    "back_at", "bind", "block_model", "bound", "choose", "classify", "clear",
    "conversation_of", "forget_bindings", "mark", "pinned", "session_credentials",
    "set_pin", "state_of", "succeeded", "unavailable_error", "usable", "with_credential",
]
