"""What a failed request does to the credential that sent it.

Every provider request goes through :func:`with_credential`
(``streaming/stream.py`` wraps every registered adapter in it), so one place
decides, for every adapter, what a failure does. The credential itself is
chosen in ``models/auth/selection.py``; a failure is recorded on it there.

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
reason ``credential``), never a dialog.

The SDKs' own retries are off (``max_retries=0``) so this layer sees every
failure once.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from openprogram.models.auth.selection import (
    QUOTA_HOLD_MS,
    NoUsableCredential,
    block_model,
    choose,
    conversation_of,
    lease,
    mark,
    succeeded,
)
from openprogram.models.auth.store import canonical_provider, get_store
from openprogram.models.auth.types import Credential

_log = logging.getLogger(__name__)

COOLDOWN_BASE_MS = 60_000
COOLDOWN_MAX_MS = 15 * 60_000
SHORT_RETRY_AFTER_S = 5.0
WAIT_FOR_RECOVERY_MS = 60_000
SERVER_RETRIES = 2


def _now_ms() -> int:
    return int(time.time() * 1000)


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
        from openprogram.models.providers.usage_port import exhausted_limit
        limit = exhausted_limit(provider, cred.id)
        if limit is not None and limit.resets_at:
            return int(limit.resets_at * 1000)
    return _now_ms() + QUOTA_HOLD_MS


_REASON_TEXT = {
    "rate_limit": "hit a rate limit", "quota": "used up its quota",
    "auth": "was refused", "model": "cannot use this model",
}


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
    ``opts.credential`` (a ``selection.Lease``) set to the chosen credential.
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
        from openprogram.models.auth.credential_provider import get_credential_provider
        from openprogram.models.auth.types import AuthError
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
        sent = lease(cred)
        opts = self.opts.model_copy(update={"api_key": sent.auth_value, "credential": sent})
        return sent, self.attempt(opts)

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
                    from openprogram.models.auth.credential_provider import get_credential_provider
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
                    from openprogram.models.auth.credential_provider import get_credential_provider
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
    "COOLDOWN_BASE_MS", "COOLDOWN_MAX_MS", "Outcome", "SERVER_RETRIES", "SHORT_RETRY_AFTER_S",
    "WAIT_FOR_RECOVERY_MS", "classify", "unavailable_error", "with_credential",
]
